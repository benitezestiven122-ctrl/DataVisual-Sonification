# Plan de creación e implementación — *Decision Final*

Plataforma de sonificación (Python) y visualización generativa (p5.js, el "Processing en JavaScript") de datos de sensores, publicada en **GitHub** y desplegada en **Streamlit Community Cloud**.

---

## 0. Decisiones de arquitectura (leer antes de programar)

El documento de ideación propone un flujo en vivo `Wokwi → UDP/OSC → Python → audio + Processing`. Al desplegar en Streamlit Cloud hay tres restricciones que cambian el diseño:

| Restricción | Consecuencia | Decisión |
|---|---|---|
| El servidor de Streamlit Cloud **no tiene tarjeta de sonido** y el audio que genere `pyo`/SuperCollider no llega al navegador. | No se puede "tocar" audio en tiempo real desde el servidor. | Python **renderiza** el audio (síntesis con `numpy` → WAV) y el navegador lo reproduce con `st.audio`. También se exporta MIDI. |
| Streamlit Cloud **no acepta paquetes UDP/OSC entrantes**, y Wokwi en el navegador no puede enviar UDP a tu red local. | OSC solo sirve en modo local. | Modo en vivo vía **MQTT** (Wokwi sí sale a internet por la red `Wokwi-GUEST`). OSC queda como opción local hacia Processing/DAW. |
| `oscP5` es una librería de Processing **Java**, no de p5.js. | El sketch web no puede usar oscP5. | El sketch p5.js recibe los datos ya mapeados como JSON (incrustado con `st.components.v1.html`) o por WebSocket/MQTT en el modo en vivo. |

Por eso el proyecto tendrá **dos modos** sobre el mismo núcleo:

1. **Modo dataset (Streamlit, público):** subir o enlazar datos en cualquier formato → limpiar → sonificar a WAV/MIDI → visualizar con p5.js sincronizado con el audio.
2. **Modo en vivo (local / demo):** Wokwi → MQTT → Python → OSC (Processing/DAW) y WebSocket (p5.js).

La pieza común es la **ingesta multiformato**: cualquier fuente se convierte en un `pandas.DataFrame` con columnas limpias. Por eso es la primera fase.

```
 Fuentes                    Núcleo Python (paquete `sonification`)                Salidas
 ───────                    ──────────────────────────────────────               ───────
 CSV/TSV/TXT ─┐
 Excel/ODS ───┤
 JSON/JSONL ──┤   ingest  ──►  preprocess  ──►  mapping  ──┬─► audio  ──► WAV / MIDI ──► st.audio
 Parquet ─────┼─► (DataFrame)  (limpieza,      (rangos    │
 SQLite/XML ──┤                 normaliza      bajo/medio/ └─► visual ──► JSON ──► p5.js (navegador)
 URL/Sheets ──┤                 0–100, ventanas) alto)
 Wokwi MQTT ──┘ (modo en vivo, mismo pipeline por trama)
```

---

## 1. Estructura del repositorio

```
Sonification project/
├── app.py                      # Fase 5: app Streamlit (punto de entrada en Streamlit Cloud)
├── requirements.txt            # dependencias de ejecución (Streamlit Cloud las lee)
├── requirements-dev.txt        # + pytest
├── PLAN.md  README.md  .gitignore
├── sonification/
│   ├── ingest/                 # Fase 1 ✅  carga multiformato
│   │   ├── sources.py          #   rutas, URLs, bytes, archivos subidos
│   │   ├── readers.py          #   un lector por formato + detección automática
│   │   ├── schema.py           #   limpieza básica, columna de tiempo, resumen
│   │   ├── samples.py          #   datos de sensor sintéticos en todos los formatos
│   │   └── __main__.py         #   CLI: python -m sonification.ingest archivo
│   ├── preprocess.py           # Fase 2 ✅  limpieza, 0–100, rangos, estadísticas
│   ├── mapping.py              # Fase 3 ✅  matriz de mapeo → eventos de nota
│   ├── sonify.py               # Fase 3 ✅  orquestador + CLI
│   ├── audio/                  # Fase 3 ✅  escalas, sintetizador, export WAV/MIDI
│   └── live/                   # Fase 6 (MQTT, OSC, WebSocket)
│   ├── visual.py               # Fase 4 ✅  HTML autocontenido (sketch + audio + datos)
├── web/
│   └── sketch.js               # Fase 4 ✅  visualización p5.js
├── wokwi/                      # Fase 6: diagram.json + firmware ESP32
├── data/samples/               # datos de ejemplo generados
├── scripts/make_samples.py
└── tests/
```

---

## 2. Fases

### Fase 0 — Preparación del entorno *(½ día)*
- Instalar **Git** (no está instalado en este equipo) y **Python 3.12** (se puede usar `uv`, que sí está instalado: `uv venv --python 3.12`).
- `git init`, crear repo en GitHub, primer commit con esta estructura.
- Entorno: `uv venv --python 3.12` → `uv pip install -r requirements-dev.txt`.

### Fase 1 — Ingesta multiformato ✅ *(implementada)*
Función única `load_data(fuente, fmt=None, **opciones) -> DataFrame`.

| Formato | Extensiones | Notas |
|---|---|---|
| CSV / texto delimitado | `.csv .txt .dat` | detecta `, ; \t \|` y espacios; si el separador es `;` usa coma decimal (CSV de Excel en español); codificación UTF-8 → cp1252 → latin-1 |
| TSV | `.tsv .tab` | |
| Excel / OpenDocument | `.xlsx .xlsm .xls .ods` | `sheet=` por nombre/índice, `sheet=None` une todas las hojas |
| JSON | `.json` | lista de registros, objetos anidados (`json_normalize`), respuestas de API con la lista dentro (`feeds` de ThingSpeak, `data`, `results`), `record_path="a.b"` |
| JSON Lines | `.jsonl .ndjson` | ideal para logs del serial de Wokwi; ignora líneas corruptas |
| Parquet / Feather | `.parquet .pq .feather .arrow` | |
| SQLite | `.db .sqlite .sqlite3` | `table=` o `query=` |
| XML | `.xml` | |
| URL | `http(s)://…` | formato por extensión o Content-Type; reescribe enlaces de **Google Sheets** y **GitHub** (`blob` → `raw`) |
| Memoria | `bytes`, `BytesIO`, `UploadedFile` de Streamlit, `DataFrame`, `dict`/`list` | sin extensión se detecta por contenido (firmas binarias / primer carácter) |

No se soporta `pickle` a propósito (ejecuta código al cargarse; inseguro en una app pública).

Tras leer, `schema.finalize` limpia nombres de columnas, elimina filas/columnas vacías, convierte texto numérico a número y parsea la columna de tiempo (`timestamp`, `fecha`, `time`…). `describe_dataset` devuelve el resumen que usará la app para elegir columnas.

**Criterio de terminado:** `pytest` en verde; `python -m sonification.ingest data/samples/<archivo>` funciona para todos los formatos de ejemplo.

### Fase 2 — Preprocesamiento ✅ *(implementada)*
`preprocess(df, **opciones) -> ProcessedData` en `sonification/preprocess.py`.

| Paso | Detalle |
|---|---|
| Columnas y tiempo | todas las numéricas o `columns=[...]`; tiempo detectado (fecha → segundos, numérico p. ej. `millis()` → unidades, o el orden de las filas); ordena y promedia marcas de tiempo duplicadas |
| Huecos | `fill="interpolate"` (respeta el tiempo real, no el número de fila), `"ffill"` o `"drop"` |
| Outliers | `"hampel"` por defecto: reemplaza glitches aislados por la mediana local y **conserva eventos críticos sostenidos** (un recorte global los borraría); también `"iqr"`, `"percentile"` o `None` |
| Remuestreo | `target_points=N`: rejilla uniforme; al reducir **promedia** (sin aliasing), al ampliar interpola |
| Normalización | 0–100 con `domain={"potenciometro": (0, 4095)}` (rango físico del sensor) o min/max de los datos |
| Analítica | media y desviación estándar móviles (`window`), velocidad de cambio `change` (para tempo/LFO) |
| Rangos | `level` 0/1/2 = **bajo ≤30 < medio ≤70 < alto**, `thresholds` configurables; `level_share()` |
| Salida | `to_frame()` (DataFrame) y `to_payload()` (JSON para p5.js) |

Las estadísticas móviles, que la ideación ubicaba en Processing, se calculan en Python para que audio y visual usen exactamente los mismos valores.

### Fase 3 — Motor de sonificación ✅ *(implementada)*
`sonify(data, duration_s=30, pitch=..., cutoff=..., scale=...) -> Sonification` en `sonification/sonify.py`.

Tres capas: `mapping.py` (datos → eventos de nota, sin audio) → `audio/synth.py` (eventos → muestras) → `audio/export.py` (WAV/MIDI).

| Parámetro sonoro | Fuente (columna asignable por rol) |
|---|---|
| Altura | `norm` cuantizado a la escala (pentatónica menor por defecto; 8 escalas), tónica y octavas configurables |
| Filtro paso bajo | `norm` → cutoff logarítmico 250–9000 Hz, modulado por LFO |
| Tempo | velocidad de cambio **absoluta** (puntos 0–100 por cada 1 % del recorrido; `tempo_ref`) → 60–150 BPM; no depende del número de filas |
| Timbre por rango | **bajo**: seno sostenido (notas repetidas fusionadas), reverb amplio, pedal grave · **medio**: triángulo en corcheas, LFO moderado · **alto**: diente de sierra +1 octava en semicorcheas, LFO 8 Hz, saturación |

- Síntesis en `numpy`/`scipy`: osciladores con anti-aliasing PolyBLEP, ADSR, biquad paso bajo variable por bloques, saturación tanh, reverb por convolución, normalizado a −1 dBFS. Render de 30 s ≈ 1,3 s.
- **Sincronía:** los datos se estiran linealmente sobre `duration_s` → el visual solo necesita `audio.currentTime / duration_s`. `Sonification.to_payload()` ya entrega datos + notas para p5.js.
- MIDI: un canal por rango (pad / vibráfono / lead) con CC74 (brillo), CC1 (LFO) y CC91 (reverb) por nota.
- CLI: `python -m sonification.sonify archivo --pitch col --cutoff col --domain col=min:max --midi`.
- `pyo`/SuperCollider quedan solo para el modo en vivo local (fase 6).

### Fase 4 — Visualización p5.js ✅ *(implementada)*
- `web/sketch.js` (p5.js 1.11, modo instancia): `createDecisionSketch(contenedor, payload, audio, opciones)`.
- `sonification/visual.py`: `build_html(son)` / `save_html(son, ruta)` generan **un único HTML** (sketch + datos JSON + WAV en base64; p5.js por CDN). Es lo que la fase 5 pasará a `st.components.v1.html`. CLI: `python -m sonification.sonify … --html`.

| Rango | Visual |
|---|---|
| bajo | partículas lentas y circulares, azules, estelas largas |
| medio | 5 polígonos concéntricos (3–7 lados) en rotación (velocidad ← cambio), textura distorsionada (← desviación estándar), verdes/naranjas |
| alto | trazos acelerados, glitch por franjas, flashes rojo/blanco en cada nota |

- Transición progresiva entre estados (mezcla de paletas en ~0,5 s).
- **Capa acumulativa:** trazo histórico coloreado por rango, banda media ± desviación, umbrales 30/70, mapa de calor de rangos y otras series en gris; lo que aún no ha sonado aparece atenuado.
- **Sincronía:** posición = `audio.currentTime / duration_s`; cada nota dispara un pulso (altura → posición vertical). Verificado en navegador: 56 notas → 56 pulsos; tras un salto no se disparan las notas saltadas.
- **Independiente de los FPS:** todas las animaciones avanzan en segundos, no por cuadro, así que con pocos FPS la imagen no se retrasa respecto al audio.
- **Accesibilidad:** máx. ~3 flashes por segundo (WCAG 2.3.1; intervalo mínimo medido 0,346 s); `prefers-reduced-motion` desactiva glitch y flashes; descripción `aria-label` con el reparto por rangos; HUD legible sobre el glitch; tecla Espacio reproduce/pausa.
- Seguridad: nombres de columna y títulos se escapan (test con `</script>` como nombre de columna).

### Fase 5 — App Streamlit *(2–3 días)*
- `app.py`: subir archivo (`st.file_uploader`) o pegar URL → `load_data` → vista previa y resumen.
- Controles: columna, escala, duración, umbrales, instrumento.
- Botón "Sonificar" → `st.audio` + descarga WAV/MIDI.
- Componente p5.js (`st.components.v1.html`) con audio + visual sincronizados.
- `st.cache_data` en la carga y la síntesis.

### Fase 6 — Modo en vivo con Wokwi *(3 días, local)*
- Proyecto Wokwi ESP32 (potenciómetro, DHT22, LDR) que publica JSON por **MQTT** (`broker.hivemq.com` o `test.mosquitto.org`).
- `sonification/live/`: suscriptor MQTT (`paho-mqtt`) → cada trama pasa por `load_data(dict)` → preprocesado incremental → OSC (`python-osc`) hacia Processing/DAW y WebSocket hacia p5.js.
- Calibración de latencia audio/visual (fase 5 de la ideación).

### Fase 7 — Publicación *(1 día)*
- README con capturas, GIF y enlace a la app.
- GitHub Actions que corre `pytest` en cada push.
- Streamlit Community Cloud: conectar el repo, `app.py` como entrada, Python 3.12.
- Licencia (MIT) y datos de ejemplo.

---

## 3. Dependencias por fase

| Fase | Paquetes |
|---|---|
| 1 | `pandas`, `numpy`, `openpyxl`, `odfpy`, `pyarrow`, (`xlrd` para `.xls` antiguo) |
| 2 | ninguno nuevo (solo `pandas`/`numpy`) |
| 3 | `scipy`, `mido` (WAV con el módulo estándar `wave`) |
| 5 | `streamlit` |
| 6 | `paho-mqtt`, `python-osc`, `websockets` (solo local) |
| Dev | `pytest` |

## 4. Riesgos

- **Archivos grandes en Streamlit Cloud** (≈1 GB de RAM): limitar el tamaño de subida y remuestrear antes de sintetizar.
- **Política de autoplay** de los navegadores: el audio arranca con un clic del usuario.
- **Brokers MQTT públicos** sin garantía: usar un tópico único y tener datos grabados de respaldo para la demo.
