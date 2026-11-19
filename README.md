# Decision Final — Sonificación y visualización de datos

Convierte lecturas de sensores (simulados en Wokwi o cargados desde un archivo) en **sonido** (Python) y **arte generativo** (p5.js), desplegado como app de Streamlit.

> Estado: fases **1 (ingesta)**, **2 (preprocesamiento)**, **3 (sonificación)** y **4 (visualización p5.js)** completas. Ver la hoja de ruta en [PLAN.md](PLAN.md).

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (Linux/macOS: source .venv/bin/activate)
pip install -r requirements-dev.txt
```

## Cargar datos en cualquier formato

```python
from sonification.ingest import load_data, describe_dataset

df = load_data("data/samples/sensor.xlsx")                       # Excel / ODS
df = load_data("data/samples/sensor_es.csv")                     # CSV con ';' y coma decimal
df = load_data("data/samples/sensor.json")                       # JSON (también anidado / API)
df = load_data("data/samples/sensor.sqlite", table="lecturas")   # SQLite
df = load_data("https://docs.google.com/spreadsheets/d/<id>/edit#gid=0")  # Google Sheets público
df = load_data({"pot": 2048, "temp": 24.5})                      # una trama JSON de Wokwi

print(describe_dataset(df))
```

Formatos: CSV/TXT/TSV, Excel (`.xlsx .xlsm .xls`), OpenDocument (`.ods`), JSON, JSON Lines, Parquet, Feather, SQLite, XML, URLs (http/https, Google Sheets, GitHub) y objetos en memoria (`bytes`, `BytesIO`, archivos subidos en Streamlit, `DataFrame`, `dict`/`list`).

## Preprocesar: limpiar, normalizar a 0–100 y clasificar en rangos

```python
from sonification.preprocess import preprocess

data = preprocess(df, domain={"potenciometro": (0, 4095)}, target_points=300)
pot = data.series["potenciometro"]
pot.norm          # 0–100
pot.level_names   # "bajo" / "medio" / "alto"  (umbrales 30 / 70)
pot.mean, pot.std # estadísticas móviles
data.to_payload() # JSON para el sketch p5.js
```

## Sonificar: WAV y MIDI

```python
from sonification.sonify import sonify

son = sonify(data, duration_s=30, pitch="potenciometro", cutoff="luz", scale="menor_armonica")
son.save_wav("output/sensor.wav")
son.save_midi("output/sensor.mid")
```

| Rango | Sonido |
|---|---|
| bajo (0–30) | seno grave sostenido, reverb amplio, pedal |
| medio (31–70) | melodía pentatónica en triángulo, ritmo moderado |
| alto (71–100) | diente de sierra agudo, LFO rápido, distorsión |

La altura sigue el valor, el filtro se abre con la columna `cutoff` y el tempo sube cuando la señal cambia rápido.

## Visualizar: p5.js sincronizado con el audio

```python
from sonification.visual import save_html

save_html(son, "output/sensor.html")   # un solo archivo: ábrelo en el navegador
```

El sketch ([web/sketch.js](web/sketch.js)) sigue al audio: azul y partículas lentas en rango bajo, geometrías en rotación en medio, trazos acelerados, glitch y flashes en alto. Abajo se dibuja el historial de la serie con su mapa de calor de rangos. Respeta `prefers-reduced-motion` y limita los flashes a 3 por segundo.

## Terminal

```bash
python -m sonification.ingest data/samples/sensor.parquet
python -m sonification.ingest --list-formats
python -m sonification.sonify data/samples/sensor.csv --pitch potenciometro --cutoff luz --domain potenciometro=0:4095 --midi --html
```

Regenerar los datos de ejemplo y correr los tests:

```bash
python scripts/make_samples.py
pytest
```
