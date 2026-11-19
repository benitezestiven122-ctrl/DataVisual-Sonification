"""Fase 2 — Preprocesamiento.

Convierte el DataFrame de la ingesta en series limpias y comparables, listas
para que el motor de audio (fase 3) y el sketch p5.js (fase 4) usen
**exactamente los mismos valores**:

1. selección de columnas numéricas y eje de tiempo (ordenado, sin duplicados);
2. relleno de huecos (interpolación respetando el tiempo real);
3. outliers: filtro Hampel (quita glitches aislados, conserva eventos sostenidos)
   o recorte global IQR/percentiles — evita que un pico espurio aplaste la escala;
4. remuestreo a una rejilla uniforme de ``target_points`` (promedia al reducir);
5. normalización a 0–100 (con dominio del sensor o min/max de los datos);
6. media y desviación estándar móviles, velocidad de cambio;
7. clasificación en rangos **bajo / medio / alto** (0–30 / 31–70 / 71–100 por defecto).

Uso::

    from sonification.ingest import load_data
    from sonification.preprocess import preprocess

    data = preprocess(load_data("data/samples/sensor.csv"),
                      domain={"potenciometro": (0, 4095)}, target_points=300)
    data.series["potenciometro"].norm      # np.ndarray 0–100
    data.to_payload()                      # dict JSON para p5.js
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal, Sequence

import numpy as np
import pandas as pd

from .ingest.schema import detect_time_column, numeric_columns

LEVEL_NAMES = ("bajo", "medio", "alto")

FillMethod = Literal["interpolate", "ffill", "drop"]
OutlierMethod = Literal["hampel", "iqr", "percentile"]


class PreprocessError(ValueError):
    """Los datos o la configuración no permiten preprocesar."""


@dataclass
class PreprocessConfig:
    columns: Sequence[str] | None = None
    """Columnas a procesar. ``None`` = todas las numéricas (excepto la de tiempo)."""
    time_column: str | None = "auto"
    """``"auto"`` la detecta, un nombre la fuerza, ``None`` usa el orden de las filas."""
    fill: FillMethod = "interpolate"
    outliers: OutlierMethod | None = "hampel"
    """``"hampel"`` (por defecto) reemplaza picos *aislados* por la mediana local y
    conserva eventos sostenidos; ``"iqr"``/``"percentile"`` recortan globalmente."""
    hampel_window: int = 7
    hampel_k: float = 3.0
    iqr_k: float = 1.5
    percentiles: tuple[float, float] = (1.0, 99.0)
    domain: dict[str, tuple[float, float]] = field(default_factory=dict)
    """Rango físico conocido por columna, p. ej. ``{"potenciometro": (0, 4095)}``.
    Sin dominio se usa min/max de los datos ya limpios."""
    target_points: int | None = None
    """Número de puntos de la rejilla uniforme. ``None`` = conservar las lecturas."""
    window: int = 10
    """Ventana (en puntos) de las estadísticas móviles."""
    thresholds: tuple[float, float] = (30.0, 70.0)
    """Límites bajo|medio y medio|alto sobre la escala 0–100."""

    def validate(self) -> None:
        low, high = self.thresholds
        if not 0 <= low < high <= 100:
            raise PreprocessError(f"Umbrales inválidos {self.thresholds}: se requiere 0 ≤ bajo < alto ≤ 100")
        if self.window < 1:
            raise PreprocessError("window debe ser ≥ 1")
        if self.target_points is not None and self.target_points < 2:
            raise PreprocessError("target_points debe ser ≥ 2")
        if self.fill not in ("interpolate", "ffill", "drop"):
            raise PreprocessError(f"fill desconocido: {self.fill}")
        if self.outliers not in (None, "hampel", "iqr", "percentile"):
            raise PreprocessError(f"outliers desconocido: {self.outliers}")
        if self.hampel_window < 3:
            raise PreprocessError("hampel_window debe ser ≥ 3")
        p_lo, p_hi = self.percentiles
        if not 0 <= p_lo < p_hi <= 100:
            raise PreprocessError(f"Percentiles inválidos {self.percentiles}")
        for col, (lo, hi) in self.domain.items():
            if not hi > lo:
                raise PreprocessError(f"Dominio inválido para '{col}': {(lo, hi)}")


# --------------------------------------------------------------------------- #
# Resultado
# --------------------------------------------------------------------------- #
@dataclass
class ProcessedSeries:
    name: str
    values: np.ndarray
    """Valores limpios en unidades originales."""
    norm: np.ndarray
    """Valores en escala 0–100."""
    mean: np.ndarray
    """Media móvil de ``norm``."""
    std: np.ndarray
    """Desviación estándar móvil de ``norm``."""
    change: np.ndarray
    """Velocidad de cambio |d norm / d paso| — alimenta tempo/LFO en la fase 3."""
    level: np.ndarray
    """0 = bajo, 1 = medio, 2 = alto."""
    domain: tuple[float, float]

    @property
    def level_names(self) -> np.ndarray:
        return np.asarray(LEVEL_NAMES)[self.level]

    def level_share(self) -> dict[str, float]:
        """Fracción del tiempo que la serie pasa en cada rango."""
        counts = np.bincount(self.level, minlength=3) / max(len(self.level), 1)
        return {name: float(c) for name, c in zip(LEVEL_NAMES, counts)}


@dataclass
class ProcessedData:
    time: np.ndarray
    """Eje de tiempo desde el inicio: segundos (``"seconds"``), unidades de la
    columna numérica de tiempo (``"units"``) o número de fila (``"index"``)."""
    time_kind: str
    series: dict[str, ProcessedSeries]
    thresholds: tuple[float, float]
    window: int

    def __len__(self) -> int:
        return len(self.time)

    @property
    def names(self) -> list[str]:
        return list(self.series)

    def to_frame(self) -> pd.DataFrame:
        cols: dict[str, Any] = {"t": self.time}
        for name, s in self.series.items():
            cols[name] = s.values
            cols[f"{name}_norm"] = s.norm
            cols[f"{name}_mean"] = s.mean
            cols[f"{name}_std"] = s.std
            cols[f"{name}_change"] = s.change
            cols[f"{name}_level"] = pd.Categorical(s.level_names, categories=LEVEL_NAMES, ordered=True)
        return pd.DataFrame(cols)

    def to_payload(self, decimals: int = 2) -> dict[str, Any]:
        """Estructura JSON compacta para el sketch p5.js (fase 4)."""

        def r(a: np.ndarray) -> list:
            return np.round(a.astype(float), decimals).tolist()

        return {
            "time": r(self.time),
            "time_kind": self.time_kind,
            "thresholds": list(self.thresholds),
            "levels": list(LEVEL_NAMES),
            "series": {
                name: {
                    "domain": [float(s.domain[0]), float(s.domain[1])],
                    "values": r(s.values),
                    "norm": r(s.norm),
                    "mean": r(s.mean),
                    "std": r(s.std),
                    "change": r(s.change),
                    "level": s.level.astype(int).tolist(),
                }
                for name, s in self.series.items()
            },
        }


# --------------------------------------------------------------------------- #
# Pasos (funciones puras, reutilizables por separado)
# --------------------------------------------------------------------------- #
def select_columns(df: pd.DataFrame, columns: Sequence[str] | None, exclude: str | None = None) -> list[str]:
    if columns is None:
        cols = [c for c in numeric_columns(df) if c != exclude]
        if not cols:
            raise PreprocessError("No hay columnas numéricas para procesar")
        return cols
    cols = list(columns)
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise PreprocessError(f"Columnas inexistentes: {missing}. Disponibles: {list(df.columns)}")
    not_numeric = [c for c in cols if c not in numeric_columns(df)]
    if not_numeric:
        raise PreprocessError(f"Columnas no numéricas: {not_numeric}")
    if not cols:
        raise PreprocessError("La lista de columnas está vacía")
    return cols


def time_values(s: pd.Series) -> tuple[np.ndarray, str] | None:
    """Tiempo relativo al primer instante; ``None`` si la columna no es temporal ni numérica."""
    if pd.api.types.is_datetime64_any_dtype(s):
        return ((s - s.min()).dt.total_seconds()).to_numpy(float), "seconds"
    if pd.api.types.is_numeric_dtype(s) and not pd.api.types.is_bool_dtype(s):
        return (s - s.min()).to_numpy(float), "units"
    return None


def fill_missing(values: pd.DataFrame, method: FillMethod) -> pd.DataFrame:
    """``values`` debe tener como índice el tiempo (para interpolar respetándolo)."""
    if method == "interpolate":
        return values.interpolate(method="index", limit_direction="both")
    if method == "ffill":
        return values.ffill().bfill()
    return values.dropna()


def hampel(y: np.ndarray, window: int = 7, k: float = 3.0) -> np.ndarray:
    """Filtro Hampel: un punto que se aleja de la mediana de su vecindario más de
    ``k`` desviaciones robustas (MAD) se reemplaza por esa mediana.

    Elimina glitches de 1–2 muestras; un evento que dura más de media ventana
    arrastra la mediana local y se conserva.
    """
    s = pd.Series(y)
    med = s.rolling(window, center=True, min_periods=1).median()
    mad = (s - med).abs().rolling(window, center=True, min_periods=1).median() * 1.4826
    # Piso: en tramos casi planos la MAD local ≈ 0 y cualquier variación mínima
    # se marcaría como outlier. Usamos al menos el 1 % del rango global (p5–p95).
    p5, p95 = np.percentile(y, [5, 95])
    floor = 0.01 * (p95 - p5)
    outlier = (s - med).abs() > k * np.maximum(mad, floor)
    return np.where(outlier, med, s).astype(float)


def clip_outliers(y: np.ndarray, method: OutlierMethod, iqr_k: float = 1.5,
                  percentiles: tuple[float, float] = (1.0, 99.0),
                  hampel_window: int = 7, hampel_k: float = 3.0) -> np.ndarray:
    """Trata outliers sin cambiar la longitud: Hampel reemplaza, IQR/percentil recortan."""
    if method == "hampel":
        return hampel(y, hampel_window, hampel_k)
    if method == "iqr":
        q1, q3 = np.percentile(y, [25, 75])
        iqr = q3 - q1
        lo, hi = q1 - iqr_k * iqr, q3 + iqr_k * iqr
    else:
        lo, hi = np.percentile(y, percentiles)
    return np.clip(y, lo, hi)


def resample_uniform(t: np.ndarray, y: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """Lleva ``(t, y)`` (``t`` creciente, sin NaN) a ``n`` puntos equiespaciados.

    Al **reducir** promedia las lecturas que caen en cada celda (sin aliasing);
    al **ampliar** interpola linealmente.
    """
    grid = np.linspace(t[0], t[-1], n)
    if len(t) <= n:
        return grid, np.interp(grid, t, y)
    edges = (grid[:-1] + grid[1:]) / 2
    idx = np.searchsorted(edges, t, side="right")
    sums = np.bincount(idx, weights=y, minlength=n)
    counts = np.bincount(idx, minlength=n)
    out = np.full(n, np.nan)
    np.divide(sums, counts, out=out, where=counts > 0)
    empty = np.isnan(out)
    if empty.any():  # huecos largos en el tiempo original
        out[empty] = np.interp(grid[empty], grid[~empty], out[~empty])
    return grid, out


def normalize(y: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Escala a 0–100 y recorta. Una serie constante (hi == lo) queda en 0."""
    if hi <= lo:
        return np.zeros_like(y, dtype=float)
    return np.clip((y - lo) / (hi - lo) * 100.0, 0.0, 100.0)


def rolling_stats(norm: np.ndarray, window: int) -> tuple[np.ndarray, np.ndarray]:
    roll = pd.Series(norm).rolling(window, min_periods=1)
    return roll.mean().to_numpy(), roll.std(ddof=0).to_numpy()


def classify(norm: np.ndarray, thresholds: tuple[float, float]) -> np.ndarray:
    """``≤ bajo`` → 0, ``≤ alto`` → 1, resto → 2."""
    low, high = thresholds
    return np.where(norm <= low, 0, np.where(norm <= high, 1, 2)).astype(np.int8)


# --------------------------------------------------------------------------- #
# Pipeline
# --------------------------------------------------------------------------- #
def preprocess(df: pd.DataFrame, config: PreprocessConfig | None = None, **overrides: Any) -> ProcessedData:
    """Ejecuta el pipeline completo. ``overrides`` reemplaza campos de ``config``."""
    cfg = replace(config or PreprocessConfig(), **overrides)
    cfg.validate()

    tcol = detect_time_column(df) if cfg.time_column == "auto" else cfg.time_column
    if tcol is not None and tcol not in df.columns:
        raise PreprocessError(f"No existe la columna de tiempo '{tcol}'")
    cols = select_columns(df, cfg.columns, exclude=tcol)

    # --- eje de tiempo: ordenar, descartar tiempos nulos, promediar duplicados
    tv = time_values(df[tcol]) if tcol is not None else None
    if tv is not None:
        t_raw, time_kind = tv
        work = df[cols].astype(float).assign(_t=t_raw)
        work = work[work["_t"].notna()]
        work = work.groupby("_t", sort=True)[cols].mean()
    else:
        time_kind = "index"
        work = df[cols].astype(float).set_index(pd.Index(np.arange(len(df), dtype=float), name="_t"))

    all_nan = [c for c in cols if work[c].isna().all()]
    if all_nan:
        raise PreprocessError(f"Columnas sin ningún valor: {all_nan}")

    work = fill_missing(work, cfg.fill)
    if len(work) < 2:
        raise PreprocessError("Se necesitan al menos 2 lecturas válidas")

    t = work.index.to_numpy(float)
    t = t - t[0]
    if t[-1] == 0:  # todas las lecturas en el mismo instante
        t, time_kind = np.arange(len(work), dtype=float), "index"

    series: dict[str, ProcessedSeries] = {}
    out_t = t
    for col in cols:
        y = work[col].to_numpy(float)
        if cfg.outliers:
            y = clip_outliers(y, cfg.outliers, cfg.iqr_k, cfg.percentiles,
                              cfg.hampel_window, cfg.hampel_k)
        if cfg.target_points:
            out_t, y = resample_uniform(t, y, cfg.target_points)
        lo, hi = cfg.domain.get(col, (float(y.min()), float(y.max())))
        norm = normalize(y, lo, hi)
        mean, std = rolling_stats(norm, cfg.window)
        change = np.abs(np.gradient(norm))
        series[col] = ProcessedSeries(
            name=col, values=y, norm=norm, mean=mean, std=std, change=change,
            level=classify(norm, cfg.thresholds), domain=(float(lo), float(hi)),
        )

    return ProcessedData(time=out_t, time_kind=time_kind, series=series,
                         thresholds=tuple(cfg.thresholds), window=cfg.window)
