import io
import json
import sqlite3

import numpy as np
import pandas as pd
import pytest

from sonification.ingest import (
    DataLoadError,
    UnsupportedFormatError,
    describe_dataset,
    load_data,
    rewrite_url,
)
from sonification.ingest.readers import sniff_delimiter
from sonification.ingest.samples import make_sensor_frame, write_all_formats

NUMERIC = ["potenciometro", "temperatura", "humedad", "luz"]


@pytest.fixture(scope="module")
def reference():
    return make_sensor_frame(n=60)


@pytest.fixture(scope="module")
def sample_files(reference, tmp_path_factory):
    return write_all_formats(reference, tmp_path_factory.mktemp("samples"))


def assert_matches(df, reference):
    assert len(df) == len(reference)
    for col in NUMERIC:
        np.testing.assert_allclose(df[col].to_numpy(float), reference[col].to_numpy(float))
    assert pd.api.types.is_datetime64_any_dtype(df["timestamp"])
    assert (df["timestamp"].dt.tz_localize(None).to_numpy() == reference["timestamp"].to_numpy()).all()


@pytest.mark.parametrize(
    "label", ["csv", "csv_es", "tsv", "xlsx", "ods", "json", "jsonl", "parquet", "feather", "sqlite", "xml"]
)
def test_every_format_from_path(label, sample_files, reference):
    df = load_data(sample_files[label])
    assert_matches(df, reference)
    assert df.attrs["source"] == str(sample_files[label])


@pytest.mark.parametrize("label", ["parquet", "xlsx", "ods", "sqlite", "json", "jsonl", "csv", "xml"])
def test_format_sniffed_from_bytes_without_name(label, sample_files, reference):
    df = load_data(sample_files[label].read_bytes())
    assert_matches(df, reference)


def test_uploaded_file_like_object(sample_files, reference):
    """Simula el UploadedFile de Streamlit (tiene .name y .getvalue())."""
    buf = io.BytesIO(sample_files["xlsx"].read_bytes())
    buf.name = "subido.xlsx"
    df = load_data(buf)
    assert_matches(df, reference)
    assert df.attrs["format"] == "excel"


def test_forced_format_overrides_extension(sample_files, tmp_path, reference):
    wrong = tmp_path / "datos.bin"
    wrong.write_bytes(sample_files["csv"].read_bytes())
    assert_matches(load_data(wrong, fmt="csv"), reference)


def test_spanish_csv_decimal_comma_and_cp1252(tmp_path):
    path = tmp_path / "es.csv"
    path.write_bytes("año;medición\n2024;1,5\n2025;2,25\n".encode("cp1252"))
    df = load_data(path)
    assert list(df.columns) == ["año", "medición"]
    assert df["medición"].tolist() == [1.5, 2.25]


def test_whitespace_delimited_txt(tmp_path):
    path = tmp_path / "serial.txt"
    path.write_text("t pot temp\n0 100 21.5\n1 200 21.7\n")
    df = load_data(path)
    assert df["pot"].tolist() == [100, 200]


@pytest.mark.parametrize(
    "text, expected",
    [("a,b\n1,2\n", ","), ("a;b\n1,5;2\n", ";"), ("a\tb\n1\t2\n", "\t"), ("a|b\n1|2\n", "|")],
)
def test_sniff_delimiter(text, expected):
    assert sniff_delimiter(text) == expected


def test_jsonl_skips_corrupt_lines(tmp_path):
    path = tmp_path / "serial.jsonl"
    path.write_text('{"pot": 1}\n{"pot": 2\nbasura\n{"pot": 3}\n')
    df = load_data(path)
    assert df["pot"].tolist() == [1, 3]
    assert df.attrs["skipped_lines"] == 2


def test_json_nested_and_record_path(tmp_path):
    payload = {"meta": {"v": 1}, "result": {"items": [{"s": {"temp": 20}}, {"s": {"temp": 21}}]}}
    path = tmp_path / "api.json"
    path.write_text(json.dumps(payload))
    df = load_data(path, record_path="result.items")
    assert df["s.temp"].tolist() == [20, 21]


def test_json_columns_orient(tmp_path):
    path = tmp_path / "cols.json"
    pd.DataFrame({"x": [1, 2], "y": [3.0, 4.0]}).to_json(path)
    df = load_data(path)
    assert df["y"].tolist() == [3.0, 4.0]


def test_python_objects_and_dataframe():
    frame = load_data({"pot": 2048, "temp": "24.5"})  # trama única de Wokwi
    assert frame.loc[0, "temp"] == 24.5
    assert len(load_data([{"a": 1}, {"a": 2}])) == 2
    assert load_data(pd.DataFrame({"a": [1]})).attrs["format"] == "dataframe"


def test_sqlite_multiple_tables(tmp_path):
    path = tmp_path / "multi.db"
    with sqlite3.connect(path) as con:
        pd.DataFrame({"a": [1, 2]}).to_sql("uno", con, index=False)
        pd.DataFrame({"b": [3]}).to_sql("dos", con, index=False)
    con.close()
    with pytest.raises(DataLoadError, match="table="):
        load_data(path)
    assert load_data(path, table="uno")["a"].tolist() == [1, 2]
    assert load_data(path, query="SELECT b * 2 AS c FROM dos")["c"].tolist() == [6]
    with pytest.raises(DataLoadError, match="No existe"):
        load_data(path, table='uno"; DROP TABLE uno; --')


def test_excel_all_sheets(tmp_path):
    path = tmp_path / "libro.xlsx"
    with pd.ExcelWriter(path) as w:
        pd.DataFrame({"v": [1]}).to_excel(w, sheet_name="A", index=False)
        pd.DataFrame({"v": [2]}).to_excel(w, sheet_name="B", index=False)
    df = load_data(path, sheet=None)
    assert df["v"].tolist() == [1, 2]
    assert df["_sheet"].tolist() == ["A", "B"]
    assert load_data(path, sheet="B")["v"].tolist() == [2]


def test_errors(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_data(tmp_path / "no_existe.csv")
    with pytest.raises(UnsupportedFormatError):
        load_data(b"a,b\n1,2\n", fmt="mp3")
    bad = tmp_path / "roto.xlsx"
    bad.write_bytes(b"esto no es un excel")
    with pytest.raises(DataLoadError):
        load_data(bad)
    with pytest.raises(TypeError):
        load_data(42)


def test_rewrite_url():
    assert rewrite_url("https://docs.google.com/spreadsheets/d/AbC-123/edit#gid=456") == (
        "https://docs.google.com/spreadsheets/d/AbC-123/export?format=csv&gid=456"
    )
    assert rewrite_url("https://github.com/u/r/blob/main/data/x.csv") == (
        "https://raw.githubusercontent.com/u/r/main/data/x.csv"
    )
    assert rewrite_url("https://example.com/x.csv") == "https://example.com/x.csv"


def test_describe_dataset(sample_files):
    info = describe_dataset(load_data(sample_files["parquet"]))
    assert info["time_column"] == "timestamp"
    assert info["numeric_columns"] == NUMERIC
    assert 0 <= info["stats"]["potenciometro"]["min"] <= info["stats"]["potenciometro"]["max"] <= 4095
    json.dumps(info, default=str)  # serializable
