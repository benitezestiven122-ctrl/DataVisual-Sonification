import base64
import io
import json
import re
import wave

import numpy as np
import pandas as pd
import pytest

from sonification.ingest.samples import make_sensor_frame
from sonification.preprocess import preprocess
from sonification.sonify import main as sonify_cli
from sonification.sonify import sonify
from sonification.visual import P5_URL, SKETCH_PATH, build_html, save_html

SR = 8000


@pytest.fixture(scope="module")
def son():
    data = preprocess(make_sensor_frame(80), domain={"potenciometro": (0, 4095)}, target_points=100)
    return sonify(data, duration_s=3, sr=SR, pitch="potenciometro", cutoff="luz")


def extract_payload(page: str) -> dict:
    m = re.search(r"var PAYLOAD = (.*?);\n", page)
    return json.loads(m.group(1))


def test_html_contains_everything(son):
    page = build_html(son)
    assert page.startswith("<!doctype html>")
    assert P5_URL in page
    assert "createDecisionSketch" in page and SKETCH_PATH.read_text(encoding="utf-8")[:200] in page
    assert not re.search(r"__(TITLE|P5_URL|ARIA|AUDIO|META|SKETCH|PAYLOAD|OPTIONS)__", page)
    payload = extract_payload(page)
    assert payload == json.loads(json.dumps(son.to_payload()))
    assert len(payload["data"]["series"]["potenciometro"]["norm"]) == 100


def test_embedded_audio_is_valid_wav(son):
    page = build_html(son)
    b64 = re.search(r'src="data:audio/wav;base64,([A-Za-z0-9+/=]+)"', page).group(1)
    with wave.open(io.BytesIO(base64.b64decode(b64))) as w:
        assert w.getframerate() == SR
        assert w.getnframes() == len(son.audio)


def test_without_audio_and_options(son):
    page = build_html(son, include_audio=False, options={"particles": 50, "primary": "luz"})
    assert "<audio" not in page and "modo demo" in page
    opts = json.loads(re.search(r"var OPTIONS = (.*?);\n", page).group(1))
    assert opts == {"particles": 50, "primary": "luz"}
    with pytest.raises(ValueError, match="no está"):
        build_html(son, options={"primary": "nope"})


def test_hostile_column_names_are_escaped():
    """Nombres de columna con HTML/JS no deben romper ni inyectar en la página."""
    evil = '</script><script>alert(1)</script>'
    tricky = "__PAYLOAD__ & <b>"
    df = pd.DataFrame({evil: np.linspace(0, 100, 30), tricky: np.linspace(100, 0, 30)})
    data = preprocess(df, outliers=None)
    page = build_html(sonify(data, duration_s=1, sr=SR), title="<i>x</i>", include_audio=False)
    assert "<script>alert(1)" not in page
    assert page.count("</script>") == 3  # p5 + sketch + bootstrap, nada más
    assert "<i>x</i>" not in page and "<b>" not in page
    payload = extract_payload(page)
    assert set(payload["data"]["series"]) == {evil, tricky}  # el dato llega intacto al JS


def test_warns_on_huge_payload():
    df = pd.DataFrame({"v": np.sin(np.arange(6000) / 50)})
    s = sonify(preprocess(df, outliers=None), duration_s=1, sr=SR)
    with pytest.warns(UserWarning, match="target_points"):
        build_html(s, include_audio=False)


def test_save_html_and_cli(son, tmp_path):
    p = save_html(son, tmp_path / "sub" / "v.html")
    assert p.read_text(encoding="utf-8").startswith("<!doctype html>")

    from sonification.ingest.samples import write_all_formats
    src = write_all_formats(make_sensor_frame(40), tmp_path)["csv"]
    out = tmp_path / "o.wav"
    assert sonify_cli([str(src), "--duration", "2", "--sr", str(SR), "-o", str(out), "--html"]) == 0
    assert out.with_suffix(".html").exists()
