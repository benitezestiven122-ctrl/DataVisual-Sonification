"""Ingesta multiformato: cualquier fuente de datos → ``pandas.DataFrame``.

Ejemplo::

    from sonification.ingest import load_data, describe_dataset

    df = load_data("data/samples/sensor.xlsx")
    df = load_data("https://docs.google.com/spreadsheets/d/<id>/edit#gid=0")
    df = load_data(uploaded_file)                 # st.file_uploader
    df = load_data("logs.db", table="lecturas")
    df = load_data({"pot": 2048, "temp": 24.5})   # una trama JSON de Wokwi
    print(describe_dataset(df))
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from .readers import (
    DataLoadError,
    UnsupportedFormatError,
    detect_format,
    frame_from_obj,
    get_reader,
    normalize_format,
    register,
    supported_formats,
)
from .schema import describe_dataset, detect_time_column, finalize, numeric_columns
from .sources import RawSource, resolve_source, rewrite_url

__all__ = [
    "load_data", "describe_dataset", "supported_formats", "register",
    "detect_time_column", "numeric_columns", "rewrite_url",
    "DataLoadError", "UnsupportedFormatError", "RawSource",
]

# Paquete pip que falta según el formato (para mensajes de error útiles).
_PIP_HINT = {"excel": "openpyxl odfpy xlrd", "parquet": "pyarrow", "feather": "pyarrow"}


def load_data(
    source: Any,
    fmt: str | None = None,
    *,
    parse_dates: bool = True,
    **options: Any,
) -> pd.DataFrame:
    """Carga ``source`` y devuelve un DataFrame limpio.

    Parameters
    ----------
    source:
        Ruta, URL (incluye Google Sheets y GitHub), ``bytes``, objeto tipo archivo
        (``BytesIO``, ``UploadedFile`` de Streamlit), ``DataFrame``, o JSON ya
        parseado (``dict``/``list``).
    fmt:
        Fuerza el formato (``"csv"``, ``"xlsx"``, ``"json"``…). Por defecto se
        deduce de la extensión, el Content-Type o el contenido.
    parse_dates:
        Convertir la columna de tiempo detectada a ``datetime``.
    **options:
        Opciones del lector: ``sep``, ``decimal``, ``encoding`` (CSV);
        ``sheet`` (Excel); ``record_path`` (JSON); ``table``/``query`` (SQLite);
        y cualquier argumento extra de la función de pandas correspondiente.
    """
    if isinstance(source, pd.DataFrame):
        df, name, fmt = source, "<DataFrame>", "dataframe"
    elif isinstance(source, (dict, list)):
        df, name, fmt = frame_from_obj(source, options.pop("record_path", None)), "<records>", "records"
    else:
        raw = resolve_source(source)
        name = raw.name
        fmt = normalize_format(fmt) if fmt else detect_format(raw)
        try:
            df = get_reader(fmt)(raw, dict(options))
        except DataLoadError:
            raise
        except ImportError as exc:
            hint = _PIP_HINT.get(fmt, "")
            raise DataLoadError(
                f"Falta una dependencia para leer '{fmt}': {exc}. Instala: pip install {hint}"
            ) from exc
        except Exception as exc:  # noqa: BLE001 — se re-lanza con contexto
            raise DataLoadError(f"No se pudo leer {name} como '{fmt}': {exc}") from exc

    extra_attrs = dict(df.attrs)
    df = finalize(df, parse_dates=parse_dates)
    if df.empty:
        raise DataLoadError(f"La fuente {name} no contiene datos")
    df.attrs.update(extra_attrs, source=name, format=fmt)
    return df
