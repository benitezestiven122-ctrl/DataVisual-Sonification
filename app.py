"""app.py — Fase 5: Streamlit app para Decision Final.

Dos pestañas:
- Dataset: carga archivo/URL, controles, sonificación, visual p5.js.
- En vivo (MQTT): suscripción a broker.hivemq.com, métricas en vivo, gráfica,
  tabla de mapeo y botón para sonificar el buffer acumulado.

Ejecución::

    streamlit run app.py
"""

from __future__ import annotations

import io
import json
import threading
import time
from collections import deque
from typing import Any

import numpy as np
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components

# ---------------------------------------------------------------------------
# Configuración de la app
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Decision Final — Sonificación",
    page_icon="🎵",
    layout="wide",
)

# ---------------------------------------------------------------------------
# Constantes MQTT
# ---------------------------------------------------------------------------
MQTT_BROKER = "broker.hivemq.com"
MQTT_PORT = 1883
MQTT_TOPIC = "decisionfinal/sensores"
BUFFER_MAX = 300  # lecturas máximas en el buffer rodante

# ---------------------------------------------------------------------------
# Importaciones del paquete de sonificación
# ---------------------------------------------------------------------------
from sonification.ingest import DataLoadError, load_data
from sonification.mapping import SonificationMapping
from sonification.preprocess import preprocess
from sonification.sonify import sonify
from sonification.visual import build_html
from sonification.audio.scales import SCALES
from sonification import live as live_mqtt

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _init_session_state() -> None:
    """Inicializa variables de st.session_state si no existen."""
    defaults: dict[str, Any] = {
        # Dataset
        "dataset_df": None,
    }
    for key, val in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = val


_init_session_state()


@st.cache_data(show_spinner="Cargando datos…")
def _load_data_cached(source_key: str, raw: bytes | None, url: str | None) -> pd.DataFrame:
    """Carga datos con caché. ``source_key`` garantiza que el caché se invalida
    cuando cambia el archivo o la URL."""
    if raw is not None:
        return load_data(io.BytesIO(raw))
    return load_data(url)


@st.cache_data(show_spinner="Sonificando…", max_entries=8)
def _sonify_cached(
    df_json: str,
    col_pitch: str,
    col_cutoff: str,
    col_tempo: str,
    col_level: str,
    scale: str,
    duration_s: float,
) -> tuple[bytes, bytes, str]:
    """Ejecuta el pipeline y devuelve (wav_bytes, midi_bytes, html_str)."""
    df = pd.read_json(io.StringIO(df_json), orient="split")
    data = preprocess(
        df,
        domain={"potenciometro": (0, 4095)},
        target_points=min(len(df), 500),
    )
    mapping = SonificationMapping(
        pitch=col_pitch,
        cutoff=col_cutoff,
        tempo=col_tempo,
        level=col_level,
        scale=scale,
    )
    son = sonify(data, mapping=mapping, duration_s=duration_s)
    wav = son.to_wav_bytes()
    mid = son.to_midi_bytes()
    html = build_html(son, title="Decision Final — Dataset")
    return wav, mid, html


# ---------------------------------------------------------------------------
# Pestaña 1 — Dataset
# ---------------------------------------------------------------------------

def tab_dataset() -> None:
    st.header("📂 Dataset")

    # --- Fuente de datos ---
    col_src, col_url = st.columns([1, 2])
    with col_src:
        uploaded = st.file_uploader(
            "Subir archivo",
            type=["csv", "xlsx", "xls", "ods", "parquet", "feather", "json"],
            help="CSV, Excel, ODS, Parquet, Feather, JSON",
        )
    with col_url:
        url_input = st.text_input(
            "…o pegar URL",
            placeholder="https://docs.google.com/spreadsheets/…",
        )

    df: pd.DataFrame | None = None
    source_key = ""

    if uploaded is not None:
        raw_bytes = uploaded.read()
        source_key = f"file:{uploaded.name}:{len(raw_bytes)}"
        try:
            df = _load_data_cached(source_key, raw_bytes, None)
        except DataLoadError as exc:
            st.error(f"Error al cargar archivo: {exc}")
            return
    elif url_input.strip():
        source_key = f"url:{url_input.strip()}"
        try:
            df = _load_data_cached(source_key, None, url_input.strip())
        except DataLoadError as exc:
            st.error(f"Error al cargar URL: {exc}")
            return

    if df is None:
        st.info("Sube un archivo o pega una URL para empezar.")
        # Muestra CSV de ejemplo
        sample_path = "data/samples/sensor.csv"
        try:
            if st.button("Cargar datos de ejemplo (sensor.csv)"):
                source_key = f"sample:{sample_path}"
                df = _load_data_cached(source_key, None, sample_path)
                st.session_state["dataset_df"] = df
        except Exception:
            pass

    if df is None and st.session_state.get("dataset_df") is not None:
        df = st.session_state["dataset_df"]

    if df is None:
        return

    st.session_state["dataset_df"] = df

    # --- Vista previa ---
    with st.expander("Vista previa del DataFrame", expanded=True):
        st.dataframe(df.head(100), use_container_width=True)
        st.caption(f"{len(df)} filas × {len(df.columns)} columnas")

    # --- Columnas numéricas disponibles ---
    num_cols = df.select_dtypes(include="number").columns.tolist()
    if not num_cols:
        st.error("El dataset no contiene columnas numéricas.")
        return

    # --- Controles ---
    st.subheader("⚙️ Parámetros de sonificación")
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        col_pitch = st.selectbox("Columna → Altura", num_cols, index=0, key="ds_pitch")
    with c2:
        col_cutoff = st.selectbox("Columna → Filtro", num_cols,
                                  index=min(1, len(num_cols) - 1), key="ds_cutoff")
    with c3:
        col_tempo = st.selectbox("Columna → Tempo", num_cols,
                                 index=min(2, len(num_cols) - 1), key="ds_tempo")
    with c4:
        col_level = st.selectbox("Columna → Rango/Timbre", num_cols,
                                 index=0, key="ds_level")

    c5, c6 = st.columns(2)
    with c5:
        scale = st.selectbox("Escala musical", sorted(SCALES.keys()),
                             index=sorted(SCALES.keys()).index("pentatonica_menor")
                             if "pentatonica_menor" in SCALES else 0,
                             key="ds_scale")
    with c6:
        duration_s = st.slider("Duración (segundos)", 5, 120, 30, 5, key="ds_duration")

    # --- Botón sonificar ---
    if st.button("🎵 Sonificar", type="primary", key="btn_sonify_ds"):
        df_json = df[num_cols].to_json(orient="split")
        try:
            wav_bytes, midi_bytes, html_str = _sonify_cached(
                df_json, col_pitch, col_cutoff, col_tempo, col_level, scale, duration_s
            )
        except Exception as exc:
            st.error(f"Error en la sonificación: {exc}")
            return

        st.success("✅ Sonificación completada")

        col_a, col_b = st.columns(2)
        with col_a:
            st.audio(wav_bytes, format="audio/wav")
            st.download_button(
                "⬇️ Descargar WAV",
                data=wav_bytes,
                file_name="sonificacion.wav",
                mime="audio/wav",
                key="dl_wav",
            )
        with col_b:
            st.download_button(
                "⬇️ Descargar MIDI",
                data=midi_bytes,
                file_name="sonificacion.mid",
                mime="audio/midi",
                key="dl_midi",
            )

        st.subheader("🎨 Visual generativo (p5.js)")
        components.html(html_str, height=520, scrolling=False)


# ---------------------------------------------------------------------------
# Pestaña 2 — En vivo (MQTT)
# ---------------------------------------------------------------------------

def _buffer_to_df() -> pd.DataFrame:
    buf = live_mqtt.get_buffer()
    if not buf:
        return pd.DataFrame(columns=["ts", "pot", "temp", "hum", "luz"])
    return pd.DataFrame(buf)


def tab_mqtt() -> None:
    st.header("📡 En vivo (MQTT)")

    # --- Info broker ---
    st.info(
        f"**Broker:** `{live_mqtt.MQTT_BROKER}` &nbsp;|&nbsp; "
        f"**Puerto:** `{live_mqtt.MQTT_PORT}` &nbsp;|&nbsp; "
        f"**Topic:** `{live_mqtt.MQTT_TOPIC}`"
    )

    # --- Botón conectar / desconectar ---
    connected = live_mqtt.is_connected()
    btn_label = "🔴 Desconectar" if connected else "🟢 Conectar"
    if st.button(btn_label, key="btn_mqtt_toggle"):
        if connected:
            live_mqtt.disconnect()
        else:
            live_mqtt.connect()
        st.rerun()

    err = live_mqtt.get_error()
    last = live_mqtt.get_last()
    count = live_mqtt.get_count()
    is_sub = live_mqtt.is_subscribed()

    if err:
        st.error(err)

    if not live_mqtt.is_connected():
        st.caption("Presiona **Conectar** para suscribirte al broker y recibir datos en tiempo real.")
        return

    if is_sub:
        st.success(f"🟢 Conectado y escuchando el topic `{live_mqtt.MQTT_TOPIC}`")
    else:
        st.warning("🟡 Conectando al broker... si tarda más de 5 segundos, el broker puede estar lento.")

    # --- Métricas en vivo ---
    st.subheader(f"📊 Valores actuales (Mensajes recibidos: {count})")

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Potenciómetro", f"{last.get('pot', '—')}", help="ADC 0–4095 (GPIO34)")
    m2.metric("Temperatura (°C)", f"{last.get('temp', '—')}", help="DHT22 (GPIO4)")
    m3.metric("Humedad (%)", f"{last.get('hum', '—')}", help="DHT22 (GPIO4)")
    m4.metric("Luz (ADC)", f"{last.get('luz', '—')}", help="LDR (GPIO35)")

    # --- Gráfica rodante ---
    df_buf = _buffer_to_df()
    if not df_buf.empty:
        st.subheader(f"📈 Historial ({len(df_buf)} lecturas, máx. {BUFFER_MAX})")
        chart_df = df_buf[["pot", "temp", "hum", "luz"]].copy()
        # Normalizar para visualizar en la misma escala
        st.line_chart(chart_df, use_container_width=True)

    # --- Tabla de mapeo ---
    st.subheader("🗺️ Mapeo de sonificación")
    mapeo_data = {
        "Sensor": ["Potenciómetro (pot)", "Temperatura (temp)", "Humedad (hum)", "Luz (luz)"],
        "Rango físico": ["0 – 4095", "0 – 50 °C", "0 – 100 %", "0 – 4095"],
        "Rol sonoro": ["Altura (pitch)", "Filtro (cutoff)", "Tempo", "Rango/timbre"],
        "Rango sonoro": [
            "C3 – C5 (pentatónica menor)",
            "250 – 9000 Hz",
            "60 – 150 BPM",
            "bajo / medio / alto (0–30 / 31–70 / 71–100 norm.)",
        ],
    }
    st.table(pd.DataFrame(mapeo_data))

    # --- Sonificar buffer ---
    st.subheader("🎵 Sonificar buffer")
    if df_buf.empty or len(df_buf) < 5:
        st.warning("Espera a tener al menos 5 lecturas en el buffer.")
    else:
        if st.button("🎵 Sonificar buffer", type="primary", key="btn_sonify_mqtt"):
            # Mapeamos las columnas del payload a nombres del pipeline
            work_df = df_buf[["pot", "temp", "hum", "luz"]].copy()
            work_df = work_df.rename(columns={
                "pot": "potenciometro",
                "temp": "temperatura",
                "hum": "humedad",
                "luz": "luz",
            })
            df_json = work_df.to_json(orient="split")
            try:
                wav_bytes, midi_bytes, html_str = _sonify_cached(
                    df_json,
                    "potenciometro", "luz", "potenciometro", "potenciometro",
                    "pentatonica_menor", 20.0,
                )
            except Exception as exc:
                st.error(f"Error en la sonificación: {exc}")
                return

            st.success("✅ Sonificación del buffer completada")
            col_a, col_b = st.columns(2)
            with col_a:
                st.audio(wav_bytes, format="audio/wav")
                st.download_button(
                    "⬇️ Descargar WAV",
                    data=wav_bytes,
                    file_name="buffer_mqtt.wav",
                    mime="audio/wav",
                    key="dl_mqtt_wav",
                )
            with col_b:
                st.download_button(
                    "⬇️ Descargar MIDI",
                    data=midi_bytes,
                    file_name="buffer_mqtt.mid",
                    mime="audio/midi",
                    key="dl_mqtt_midi",
                )
            st.subheader("🎨 Visual generativo (p5.js)")
            components.html(html_str, height=520, scrolling=False)

    # --- Auto-refresco cada 2 s mientras esté conectado ---
    if live_mqtt.is_connected():
        time.sleep(2)
        st.rerun()


# ---------------------------------------------------------------------------
# Layout principal
# ---------------------------------------------------------------------------
st.title("🎶 Decision Final — Sonificación de sensores")
st.caption(
    "Convierte datos de sensores (potenciómetro, temperatura, humedad, luz) "
    "en música y visualización generativa."
)

tab1, tab2 = st.tabs(["📂 Dataset", "📡 En vivo (MQTT)"])

with tab1:
    tab_dataset()

with tab2:
    tab_mqtt()
