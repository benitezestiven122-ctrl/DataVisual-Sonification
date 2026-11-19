import json

import numpy as np
import pandas as pd
import pytest

from sonification.ingest import load_data
from sonification.ingest.samples import make_sensor_frame, write_all_formats
from sonification.preprocess import (
    LEVEL_NAMES,
    PreprocessConfig,
    PreprocessError,
    classify,
    preprocess,
    resample_uniform,
)


@pytest.fixture(scope="module")
def sensor():
    return make_sensor_frame(n=120)


def test_defaults_on_sensor_data(sensor):
    data = preprocess(sensor)
    assert data.names == ["potenciometro", "temperatura", "humedad", "luz"]
    assert data.time_kind == "seconds"
    np.testing.assert_allclose(data.time, np.arange(120))
    for s in data.series.values():
        assert len(s.norm) == 120
        assert s.norm.min() == pytest.approx(0) and s.norm.max() == pytest.approx(100)
        assert set(np.unique(s.level)) <= {0, 1, 2}
        assert sum(s.level_share().values()) == pytest.approx(1)


def test_known_domain(sensor):
    data = preprocess(sensor, columns=["potenciometro"], domain={"potenciometro": (0, 4095)}, outliers=None)
    s = data.series["potenciometro"]
    np.testing.assert_allclose(s.norm, sensor["potenciometro"] / 4095 * 100)
    assert s.domain == (0.0, 4095.0)


def test_values_outside_domain_are_clipped():
    df = pd.DataFrame({"v": [-10.0, 50.0, 200.0]})
    norm = preprocess(df, domain={"v": (0, 100)}, outliers=None).series["v"].norm
    assert norm.tolist() == [0.0, 50.0, 100.0]


def test_classify_boundaries():
    lv = classify(np.array([0, 30, 30.01, 70, 70.01, 100]), (30, 70))
    assert lv.tolist() == [0, 0, 1, 1, 2, 2]
    assert LEVEL_NAMES[lv[-1]] == "alto"


def test_interpolation_respects_irregular_time():
    df = pd.DataFrame({"ms": [0, 1000, 3000], "v": [0.0, np.nan, 30.0]})
    data = preprocess(df, time_column="ms", outliers=None)
    assert data.time_kind == "units"
    assert data.series["v"].values.tolist() == [0.0, 10.0, 30.0]


def test_fill_drop_and_ffill():
    df = pd.DataFrame({"v": [1.0, np.nan, 3.0, 4.0]})
    assert preprocess(df, fill="drop", outliers=None).series["v"].values.tolist() == [1, 3, 4]
    assert preprocess(df, fill="ffill", outliers=None).series["v"].values.tolist() == [1, 1, 3, 4]


def test_outlier_spike_does_not_squash_scale():
    y = np.sin(np.linspace(0, 6, 200)) * 10 + 50
    y[100] = 10_000  # glitch del sensor
    df = pd.DataFrame({"v": y})
    others = np.arange(200) != 100
    raw = preprocess(df, outliers=None).series["v"]
    assert raw.norm[others].max() < 1  # sin tratamiento: todo aplastado abajo
    cleaned = preprocess(df).series["v"]  # hampel por defecto
    assert cleaned.values.max() < 61
    assert cleaned.norm[others].max() == pytest.approx(100)
    np.testing.assert_allclose(cleaned.values[others], y[others])  # no toca lo demás
    for method in ("iqr", "percentile"):
        assert preprocess(df, outliers=method).series["v"].values.max() < 10_000


def test_hampel_keeps_sustained_event():
    y = np.full(200, 20.0) + np.sin(np.arange(200)) * 0.5
    y[80:100] = 95.0  # evento crítico de 20 muestras: NO es un glitch
    v = preprocess(pd.DataFrame({"v": y}), domain={"v": (0, 100)}).series["v"]
    np.testing.assert_allclose(v.values[80:100], 95.0)
    assert (v.level[80:100] == 2).all()


def test_resample_down_averages_and_up_interpolates():
    t = np.arange(1000, dtype=float)
    y = np.where(np.arange(1000) % 2 == 0, 0.0, 10.0)  # alterna 0/10
    grid, down = resample_uniform(t, y, 100)
    assert len(grid) == 100 and grid[0] == 0 and grid[-1] == 999
    assert np.abs(down[1:-1] - 5).max() < 1  # promedia, no muestrea (sin aliasing)
    _, up = resample_uniform(np.array([0.0, 10.0]), np.array([0.0, 100.0]), 11)
    np.testing.assert_allclose(up, np.arange(0, 101, 10))


def test_resample_irregular_time():
    grid, y = resample_uniform(np.array([0.0, 1.0, 10.0]), np.array([0.0, 1.0, 10.0]), 11)
    np.testing.assert_allclose(y, grid)


def test_target_points_in_pipeline(sensor):
    data = preprocess(sensor, target_points=30)
    assert len(data) == 30
    assert all(len(s.norm) == 30 for s in data.series.values())
    assert data.time[-1] == pytest.approx(119)


def test_duplicate_timestamps_averaged_and_sorted():
    df = pd.DataFrame({"t": [2, 0, 0, 1], "v": [5.0, 1.0, 3.0, 4.0]})
    data = preprocess(df, time_column="t", outliers=None)
    assert data.time.tolist() == [0, 1, 2]
    assert data.series["v"].values.tolist() == [2.0, 4.0, 5.0]


def test_no_time_column_uses_row_order():
    data = preprocess(pd.DataFrame({"a": [3.0, 1.0, 2.0]}), time_column=None)
    assert data.time_kind == "index"
    assert data.series["a"].values.tolist() == [3.0, 1.0, 2.0]


def test_constant_series():
    s = preprocess(pd.DataFrame({"v": [5.0] * 4})).series["v"]
    assert s.norm.tolist() == [0.0] * 4
    assert s.std.tolist() == [0.0] * 4


def test_rolling_stats():
    df = pd.DataFrame({"v": [0.0, 100.0, 0.0, 100.0]})
    s = preprocess(df, window=2, outliers=None).series["v"]
    assert s.mean.tolist() == [0, 50, 50, 50]
    assert s.std.tolist() == [0, 50, 50, 50]
    assert s.change.tolist() == [100, 0, 0, 100]


def test_config_object_and_overrides(sensor):
    cfg = PreprocessConfig(columns=["luz"], thresholds=(20, 80))
    data = preprocess(sensor, cfg, window=5)
    assert data.names == ["luz"] and data.thresholds == (20, 80) and data.window == 5
    assert cfg.window == 10  # el config original no se modifica


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"columns": ["nope"]}, "inexistentes"),
        ({"columns": ["nombre"]}, "no numéricas"),
        ({"thresholds": (70, 30)}, "Umbrales"),
        ({"window": 0}, "window"),
        ({"target_points": 1}, "target_points"),
        ({"domain": {"v": (5, 5)}}, "Dominio"),
        ({"time_column": "fecha"}, "tiempo"),
        ({"fill": "magia"}, "fill"),
        ({"hampel_window": 2}, "hampel_window"),
    ],
)
def test_errors(kwargs, match):
    df = pd.DataFrame({"v": [1.0, 2.0], "nombre": ["a", "b"]})
    with pytest.raises(PreprocessError, match=match):
        preprocess(df, **kwargs)


def test_too_few_rows_and_empty_column():
    with pytest.raises(PreprocessError, match="2 lecturas"):
        preprocess(pd.DataFrame({"v": [1.0]}))
    with pytest.raises(PreprocessError, match="sin ningún valor"):
        preprocess(pd.DataFrame({"v": [1.0, 2.0], "w": [np.nan, np.nan]}), columns=["v", "w"])
    with pytest.raises(PreprocessError, match="numéricas"):
        preprocess(pd.DataFrame({"s": ["a", "b"]}))


def test_payload_and_frame(sensor):
    data = preprocess(sensor, target_points=50)
    payload = data.to_payload()
    json.dumps(payload)
    assert payload["levels"] == ["bajo", "medio", "alto"]
    pot = payload["series"]["potenciometro"]
    assert len(payload["time"]) == len(pot["norm"]) == len(pot["level"]) == 50
    frame = data.to_frame()
    assert len(frame) == 50
    assert frame["potenciometro_level"].cat.categories.tolist() == list(LEVEL_NAMES)


@pytest.mark.parametrize("label", ["csv_es", "xlsx", "jsonl", "sqlite"])
def test_end_to_end_from_files(label, sensor, tmp_path):
    path = write_all_formats(sensor, tmp_path)[label]
    data = preprocess(load_data(path), target_points=60)
    ref = preprocess(sensor, target_points=60)
    for name in ref.names:
        np.testing.assert_allclose(data.series[name].norm, ref.series[name].norm, atol=1e-6)
