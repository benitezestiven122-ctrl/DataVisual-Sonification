"""Datos sintéticos de sensores (como los de Wokwi) escritos en todos los formatos soportados."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd


def make_sensor_frame(n: int = 120, seed: int = 7) -> pd.DataFrame:
    """Potenciómetro (0–4095, ADC del ESP32), DHT22 (temp/humedad) y LDR (lux)."""
    rng = np.random.default_rng(seed)
    t = np.arange(n)
    pot = np.clip(2048 + 1800 * np.sin(t / 15) + rng.normal(0, 80, n), 0, 4095).round().astype(int)
    temp = (22 + 6 * np.sin(t / 40) + rng.normal(0, 0.3, n)).round(1)
    hum = (55 - 15 * np.sin(t / 40) + rng.normal(0, 1, n)).round(1)
    lux = np.clip(300 + 250 * np.cos(t / 10) + rng.normal(0, 20, n), 0, None).round(1)
    return pd.DataFrame({
        "timestamp": pd.date_range("2026-11-10 10:00:00", periods=n, freq="s"),
        "potenciometro": pot,
        "temperatura": temp,
        "humedad": hum,
        "luz": lux,
    })


def write_all_formats(df: pd.DataFrame, outdir: str | Path, stem: str = "sensor") -> dict[str, Path]:
    """Escribe ``df`` en cada formato soportado; devuelve ``{etiqueta: ruta}``."""
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    iso = df.assign(timestamp=df["timestamp"].dt.strftime("%Y-%m-%dT%H:%M:%S"))
    records = iso.to_dict(orient="records")
    paths: dict[str, Path] = {}

    def p(label: str, name: str) -> Path:
        paths[label] = out / name
        return paths[label]

    iso.to_csv(p("csv", f"{stem}.csv"), index=False)
    # CSV "a la española": separador ';', coma decimal, fecha dd/mm/aaaa, cp1252
    es = df.assign(timestamp=df["timestamp"].dt.strftime("%d/%m/%Y %H:%M:%S"))
    es.to_csv(p("csv_es", f"{stem}_es.csv"), index=False, sep=";", decimal=",", encoding="cp1252")
    iso.to_csv(p("tsv", f"{stem}.tsv"), index=False, sep="\t")
    df.to_excel(p("xlsx", f"{stem}.xlsx"), index=False, sheet_name="lecturas")
    iso.to_excel(p("ods", f"{stem}.ods"), index=False, engine="odf")
    # JSON con forma de respuesta de API (ThingSpeak): la tabla está en "feeds"
    p("json", f"{stem}.json").write_text(
        json.dumps({"channel": {"name": "wokwi-esp32"}, "feeds": records}, indent=1),
        encoding="utf-8",
    )
    p("jsonl", f"{stem}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8"
    )
    df.to_parquet(p("parquet", f"{stem}.parquet"), index=False)
    df.to_feather(p("feather", f"{stem}.feather"))
    db = p("sqlite", f"{stem}.sqlite")
    db.unlink(missing_ok=True)
    with sqlite3.connect(db) as con:
        iso.to_sql("lecturas", con, index=False)
    con.close()
    iso.to_xml(p("xml", f"{stem}.xml"), index=False, root_name="lecturas", row_name="lectura",
               parser="etree")
    return paths
