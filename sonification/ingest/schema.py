"""Limpieza mínima tras la carga e inspección del esquema."""

from __future__ import annotations

from typing import Any

import pandas as pd

# Fragmentos de nombre que indican una columna de tiempo (es/en).
_TIME_HINTS = ("timestamp", "datetime", "time", "date", "fecha", "hora", "tiempo", "created_at")


def _to_numeric_strict(s: pd.Series) -> pd.Series | None:
    """Convierte texto a número solo si TODOS los valores no nulos son numéricos."""
    stripped = s.astype("string").str.strip()
    for candidate in (stripped, stripped.str.replace(",", ".", regex=False)):
        conv = pd.to_numeric(candidate, errors="coerce")
        if conv.notna().sum() == s.notna().sum():
            return conv.astype("float64") if conv.isna().any() else conv
    return None


def _to_datetime_strict(s: pd.Series) -> pd.Series | None:
    for kwargs in ({"format": "ISO8601"}, {"format": "mixed", "dayfirst": True}):
        try:
            parsed = pd.to_datetime(s, errors="coerce", **kwargs)
        except (ValueError, TypeError):
            continue
        if parsed.notna().sum() == s.notna().sum():
            return parsed
    return None


def detect_time_column(df: pd.DataFrame) -> str | None:
    """Primera columna ya de tipo fecha; si no, la primera cuyo nombre sugiera tiempo."""
    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            return col
    for col in df.columns:
        name = str(col).lower()
        if name == "ts" or any(h in name for h in _TIME_HINTS):
            return col
    return None


def numeric_columns(df: pd.DataFrame) -> list[str]:
    return [
        c for c in df.columns
        if pd.api.types.is_numeric_dtype(df[c]) and not pd.api.types.is_bool_dtype(df[c])
    ]


def finalize(df: pd.DataFrame, parse_dates: bool = True) -> pd.DataFrame:
    """Normaliza nombres, quita filas/columnas vacías, tipa números y fechas."""
    df = df.copy()
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(axis=0, how="all").dropna(axis=1, how="all")

    for col in df.columns:
        if df[col].dtype == object or pd.api.types.is_string_dtype(df[col]):
            if df[col].notna().any():
                conv = _to_numeric_strict(df[col])
                if conv is not None:
                    df[col] = conv

    if parse_dates:
        tcol = detect_time_column(df)
        if tcol is not None and not pd.api.types.is_numeric_dtype(df[tcol]) \
                and not pd.api.types.is_datetime64_any_dtype(df[tcol]):
            parsed = _to_datetime_strict(df[tcol])
            if parsed is not None:
                df[tcol] = parsed

    if not isinstance(df.index, pd.RangeIndex) and df.index.dtype == object:
        df = df.reset_index(drop=True)
    return df


def describe_dataset(df: pd.DataFrame) -> dict[str, Any]:
    """Resumen serializable para la app / CLI: filas, columnas, tiempo, rangos."""
    nums = numeric_columns(df)
    tcol = detect_time_column(df)
    stats = {}
    for c in nums:
        s = df[c]
        stats[c] = {
            "min": float(s.min()) if s.notna().any() else None,
            "max": float(s.max()) if s.notna().any() else None,
            "mean": float(s.mean()) if s.notna().any() else None,
            "missing": int(s.isna().sum()),
        }
    return {
        "source": df.attrs.get("source"),
        "format": df.attrs.get("format"),
        "rows": int(len(df)),
        "columns": {c: str(df[c].dtype) for c in df.columns},
        "time_column": tcol,
        "numeric_columns": nums,
        "stats": stats,
    }
