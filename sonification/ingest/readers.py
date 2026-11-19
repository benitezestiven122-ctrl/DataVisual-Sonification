"""Lectores por formato y detección automática del formato.

Cada lector recibe un :class:`RawSource` y un ``dict`` de opciones (que puede
consumir con ``pop``) y devuelve un ``DataFrame``. Para añadir un formato nuevo
basta con decorar una función con ``@register("nombre", [".ext", ...])``.
"""

from __future__ import annotations

import io
import json
import sqlite3
from typing import Any, Callable

import pandas as pd

from .sources import RawSource


class DataLoadError(Exception):
    """No se pudo convertir la fuente en una tabla."""


class UnsupportedFormatError(DataLoadError):
    """Formato desconocido o no soportado."""


Reader = Callable[[RawSource, dict], pd.DataFrame]

_READERS: dict[str, Reader] = {}
_EXTENSIONS: dict[str, str] = {}
_ALIASES = {
    "xlsx": "excel", "xls": "excel", "xlsm": "excel", "ods": "excel",
    "txt": "csv", "dat": "csv", "ndjson": "jsonl", "pq": "parquet",
    "arrow": "feather", "db": "sqlite", "sqlite3": "sqlite", "tab": "tsv",
}


def register(fmt: str, extensions: list[str]) -> Callable[[Reader], Reader]:
    def deco(fn: Reader) -> Reader:
        _READERS[fmt] = fn
        for ext in extensions:
            _EXTENSIONS[ext] = fmt
        return fn

    return deco


def supported_formats() -> dict[str, list[str]]:
    """``{"csv": [".csv", ".txt", ...], ...}``"""
    out: dict[str, list[str]] = {fmt: [] for fmt in _READERS}
    for ext, fmt in _EXTENSIONS.items():
        out[fmt].append(ext)
    return out


def extension_map() -> dict[str, str]:
    return dict(_EXTENSIONS)


def get_reader(fmt: str) -> Reader:
    return _READERS[fmt]


def normalize_format(fmt: str) -> str:
    """Acepta ``"csv"``, ``".csv"``, ``"xlsx"``… y devuelve el nombre canónico."""
    key = fmt.lower().strip()
    if key in _EXTENSIONS:
        return _EXTENSIONS[key]
    key = key.lstrip(".")
    key = _ALIASES.get(key, key)
    if key not in _READERS:
        raise UnsupportedFormatError(
            f"Formato '{fmt}' no soportado. Disponibles: {', '.join(sorted(_READERS))}"
        )
    return key


# --------------------------------------------------------------------------- #
# Detección
# --------------------------------------------------------------------------- #
def detect_format(raw: RawSource) -> str:
    """Por extensión; si no hay, por la firma de los primeros bytes."""
    if raw.extension in _EXTENSIONS:
        return _EXTENSIONS[raw.extension]
    return sniff_format(raw.data)


def sniff_format(data: bytes) -> str:
    head = data[:2048]
    if head.startswith(b"PAR1"):
        return "parquet"
    if head.startswith(b"ARROW1"):
        return "feather"
    if head.startswith(b"SQLite format 3\x00"):
        return "sqlite"
    if head.startswith(b"PK\x03\x04") or head.startswith(b"\xd0\xcf\x11\xe0"):
        return "excel"  # xlsx/ods (zip) o xls (OLE2); pandas distingue cuál
    text = head.decode("utf-8", errors="ignore").lstrip("﻿ \t\r\n")
    if text.startswith("<"):
        return "xml"
    if text.startswith("["):
        return "json"
    if text.startswith("{"):
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        complete = lines[:-1] if len(lines) > 1 else lines  # la última puede estar cortada
        if len(complete) > 1 and all(ln.startswith("{") and ln.endswith("}") for ln in complete):
            return "jsonl"
        return "json"
    return "csv"


# --------------------------------------------------------------------------- #
# Utilidades de texto
# --------------------------------------------------------------------------- #
def decode_text(data: bytes, encoding: str | None = None) -> str:
    """UTF-8 (con o sin BOM) → cp1252 (Excel en Windows) → latin-1 (nunca falla)."""
    if encoding:
        return data.decode(encoding)
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("latin-1")


_DELIMITERS = [";", "\t", "|", ","]  # ';' antes que ',' porque ',' puede ser decimal


def sniff_delimiter(text: str) -> str:
    lines = [ln for ln in text.splitlines()[:50] if ln.strip()]
    if not lines:
        return ","
    header, rows = lines[0], lines[1:21]
    for d in _DELIMITERS:
        n = header.count(d)
        if n > 0 and all(r.count(d) == n for r in rows):
            return d
    counts = {d: header.count(d) for d in _DELIMITERS}
    best = max(counts, key=counts.get)
    if counts[best] > 0:
        return best
    if len(header.split()) > 1:
        return r"\s+"
    return ","


# --------------------------------------------------------------------------- #
# Lectores
# --------------------------------------------------------------------------- #
@register("csv", [".csv", ".txt", ".dat"])
def read_delimited(raw: RawSource, opts: dict) -> pd.DataFrame:
    text = decode_text(raw.data, opts.pop("encoding", None))
    sep = opts.pop("sep", None) or opts.pop("delimiter", None) or sniff_delimiter(text)
    decimal = opts.pop("decimal", None) or ("," if sep == ";" else ".")
    return pd.read_csv(io.StringIO(text), sep=sep, decimal=decimal, **opts)


@register("tsv", [".tsv", ".tab"])
def read_tsv(raw: RawSource, opts: dict) -> pd.DataFrame:
    opts.setdefault("sep", "\t")
    return read_delimited(raw, opts)


@register("excel", [".xlsx", ".xlsm", ".xls", ".ods"])
def read_excel(raw: RawSource, opts: dict) -> pd.DataFrame:
    sheet = opts.pop("sheet", opts.pop("sheet_name", 0))
    result = pd.read_excel(io.BytesIO(raw.data), sheet_name=sheet, **opts)
    if isinstance(result, dict):  # sheet=None → todas las hojas
        frames = [df.assign(_sheet=name) for name, df in result.items() if not df.empty]
        if not frames:
            raise DataLoadError(f"El libro no tiene hojas con datos: {raw.name}")
        return pd.concat(frames, ignore_index=True)
    return result


def frame_from_obj(obj: Any, record_path: str | None = None) -> pd.DataFrame:
    """Convierte JSON ya parseado (lista/dict, anidado o no) en un DataFrame."""
    if record_path:
        for key in record_path.split("."):
            obj = obj[int(key)] if isinstance(obj, list) else obj[key]

    if isinstance(obj, list):
        if not obj:
            raise DataLoadError("La lista de registros está vacía")
        if all(isinstance(r, dict) for r in obj):
            return pd.json_normalize(obj)
        if all(isinstance(r, (list, tuple)) for r in obj):
            return pd.DataFrame(obj)
        return pd.DataFrame({"value": obj})

    if isinstance(obj, dict):
        # Respuestas de API: {"channel": {...}, "feeds": [{...}, ...]} (ThingSpeak), {"data": [...]}…
        record_lists = {
            k: v for k, v in obj.items()
            if isinstance(v, list) and v and all(isinstance(r, dict) for r in v)
        }
        if record_lists:
            key = max(record_lists, key=lambda k: len(record_lists[k]))
            return pd.json_normalize(record_lists[key])
        values = list(obj.values())
        if values and all(isinstance(v, list) for v in values):
            if len({len(v) for v in values}) == 1:  # {"col": [..], "col2": [..]}
                return pd.DataFrame(obj)
        if values and all(isinstance(v, dict) for v in values):  # DataFrame.to_json() por defecto
            return pd.DataFrame(obj)
        return pd.json_normalize(obj)  # un solo registro (p. ej. una trama de Wokwi)

    raise DataLoadError(f"No se puede convertir {type(obj).__name__} en tabla")


@register("json", [".json"])
def read_json(raw: RawSource, opts: dict) -> pd.DataFrame:
    text = decode_text(raw.data, opts.pop("encoding", None))
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        # Muchos ".json" exportados de loggers son en realidad JSON Lines.
        if text.lstrip().startswith("{") and "\n" in text.strip():
            return read_jsonl(raw, opts)
        raise DataLoadError(f"JSON inválido en {raw.name}: {exc}") from exc
    return frame_from_obj(obj, opts.pop("record_path", None))


@register("jsonl", [".jsonl", ".ndjson"])
def read_jsonl(raw: RawSource, opts: dict) -> pd.DataFrame:
    text = decode_text(raw.data, opts.pop("encoding", None))
    records, skipped = [], 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            skipped += 1  # líneas cortadas o ruido del monitor serie
    if not records:
        raise DataLoadError(f"No hay líneas JSON válidas en {raw.name}")
    df = frame_from_obj(records, opts.pop("record_path", None))
    df.attrs["skipped_lines"] = skipped
    return df


@register("parquet", [".parquet", ".pq"])
def read_parquet(raw: RawSource, opts: dict) -> pd.DataFrame:
    return pd.read_parquet(io.BytesIO(raw.data), **opts)


@register("feather", [".feather", ".arrow"])
def read_feather(raw: RawSource, opts: dict) -> pd.DataFrame:
    return pd.read_feather(io.BytesIO(raw.data), **opts)


@register("sqlite", [".db", ".sqlite", ".sqlite3"])
def read_sqlite(raw: RawSource, opts: dict) -> pd.DataFrame:
    table = opts.pop("table", None)
    query = opts.pop("query", None)
    con = sqlite3.connect(":memory:")
    try:
        con.deserialize(raw.data)  # Python ≥ 3.11: sin archivos temporales
        if query is None:
            tables = [r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view') "
                "AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )]
            if table is None:
                if len(tables) != 1:
                    raise DataLoadError(
                        f"La base tiene {len(tables)} tablas {tables}; indica table='...' o query='...'"
                    )
                table = tables[0]
            elif table not in tables:
                raise DataLoadError(f"No existe la tabla '{table}'. Tablas: {tables}")
            query = f'SELECT * FROM "{table}"'  # nombre validado contra sqlite_master
        return pd.read_sql_query(query, con, **opts)
    finally:
        con.close()


@register("xml", [".xml"])
def read_xml(raw: RawSource, opts: dict) -> pd.DataFrame:
    opts.setdefault("parser", "etree")  # no requiere lxml
    return pd.read_xml(io.BytesIO(raw.data), **opts)
