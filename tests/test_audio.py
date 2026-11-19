import io
import wave

import mido
import numpy as np
import pandas as pd
import pytest

from sonification.audio.export import to_midi_bytes, to_wav_bytes
from sonification.audio.scales import SCALES, midi_to_hz, midi_to_name, note_to_midi, quantize
from sonification.audio.synth import adsr, lowpass, oscillator, render
from sonification.ingest.samples import make_sensor_frame
from sonification.mapping import (
    DEFAULT_STYLES,
    LevelStyle,
    MappingError,
    SonificationMapping,
    build_events,
)
from sonification.preprocess import preprocess
from sonification.sonify import main as sonify_cli
from sonification.sonify import sonify

SR = 22050


def const_data(value: float, n: int = 50):
    """Serie constante en un valor 0–100 conocido."""
    return preprocess(pd.DataFrame({"v": np.full(n, value)}), domain={"v": (0, 100)}, outliers=None)


def spectral_centroid(x: np.ndarray, sr: int) -> float:
    power = np.abs(np.fft.rfft(x)) ** 2
    freqs = np.fft.rfftfreq(len(x), 1 / sr)
    return float((freqs * power).sum() / power.sum())


# --------------------------------------------------------------------------- escalas
@pytest.mark.parametrize("name, midi", [("C4", 60), ("A4", 69), ("F#3", 54), ("Bb2", 46), ("c-1", 0), (64, 64)])
def test_note_to_midi(name, midi):
    assert note_to_midi(name) == midi


def test_note_helpers():
    assert midi_to_hz(69) == pytest.approx(440)
    assert midi_to_hz(81) == pytest.approx(880)
    assert midi_to_name(61) == "C#4"
    with pytest.raises(ValueError):
        note_to_midi("H2")


@pytest.mark.parametrize("scale", list(SCALES))
def test_quantize_stays_in_scale(scale):
    notes = quantize(np.linspace(0, 100, 101), SCALES[scale], 48, 2)
    assert notes[0] == 48 and notes[-1] == 72
    assert set((notes - 48) % 12) <= set(SCALES[scale])
    assert np.all(np.diff(notes) >= 0)  # monótono: más valor = más agudo


# --------------------------------------------------------------------------- mapeo
def test_events_cover_duration_and_positions():
    data = preprocess(make_sensor_frame(120), domain={"potenciometro": (0, 4095)})
    events = build_events(data, SonificationMapping(pitch="potenciometro"), 20.0)
    starts = np.array([e.start for e in events])
    assert starts[0] == 0 and np.all(np.diff(starts) > 0)
    assert events[-1].start + events[-1].duration <= 20.0 + 1e-6
    assert all(0 <= e.data_pos < 1 for e in events)
    np.testing.assert_allclose([e.data_pos for e in events], starts / 20.0)  # sincronía lineal
    lvl = data.series["potenciometro"].level
    for e in events:
        assert e.level == lvl[round(e.data_pos * (len(lvl) - 1))]


def test_level_styles_follow_matrix():
    low = build_events(const_data(10), SonificationMapping(), 10)
    mid = build_events(const_data(50), SonificationMapping(), 10)
    high = build_events(const_data(90), SonificationMapping(), 10)
    assert {e.level for e in low} == {0} and {e.level for e in high} == {2}
    # bajo: sostenido (notas repetidas fusionadas) / alto: pulsos rápidos y distorsión
    assert len(low) == 1 and low[0].duration == pytest.approx(10)
    assert len(high) > len(mid) > len(low)
    assert high[0].waveform == "saw" and high[0].drive > 0 and high[0].lfo_rate > mid[0].lfo_rate
    assert low[0].reverb > mid[0].reverb > high[0].reverb
    assert low[0].midi < mid[0].midi < high[0].midi  # grave → agudo


def test_faster_change_means_faster_tempo():
    t = np.arange(400)
    calm = 50 + 15 * np.sin(t / 80)  # cambia lento
    busy = 50 + 15 * np.sin(t / 3)  # cambia rápido
    df = pd.DataFrame({"calm": calm, "busy": busy})
    data = preprocess(df, domain={"calm": (0, 100), "busy": (0, 100)}, outliers=None)
    m = SonificationMapping(pitch="calm", level="calm")  # mismo estilo (medio) en ambos
    n_calm = len(build_events(data, m, 20))
    n_busy = len(build_events(data, replace_tempo(m, "busy"), 20))
    assert n_busy > n_calm * 1.3


def test_tempo_independent_of_row_count():
    """La misma curva con 100 o 1000 filas debe dar el mismo tempo."""
    counts = []
    for n in (100, 1000):
        y = 50 + 30 * np.sin(np.linspace(0, 6 * np.pi, n))
        data = preprocess(pd.DataFrame({"v": y}), domain={"v": (0, 100)}, outliers=None)
        counts.append(len(build_events(data, SonificationMapping(level="v"), 20)))
    assert abs(counts[0] - counts[1]) <= 0.05 * counts[0]


def replace_tempo(m, col):
    from dataclasses import replace
    return replace(m, tempo=col)


def test_cutoff_is_log_mapped():
    lo = build_events(const_data(0), SonificationMapping(), 4)[0]
    hi = build_events(const_data(100), SonificationMapping(), 4)[0]
    assert lo.cutoff == pytest.approx(250) and hi.cutoff == pytest.approx(9000)
    mid = build_events(const_data(50), SonificationMapping(), 4)[0]
    assert mid.cutoff == pytest.approx(np.sqrt(250 * 9000))


@pytest.mark.parametrize(
    "kwargs, match",
    [
        ({"pitch": "nope"}, "no presentes"),
        ({"scale": "jazz"}, "Escala"),
        ({"root": "H3"}, "Nota"),
        ({"octaves": 0}, "octaves"),
        ({"bpm_range": (0, 100)}, "bpm"),
        ({"cutoff_range": (10, 100)}, "cutoff"),
        ({"styles": (LevelStyle(waveform="noise"),) * 3}, "waveform"),
    ],
)
def test_mapping_errors(kwargs, match):
    with pytest.raises((MappingError, ValueError), match=match):
        build_events(const_data(50), SonificationMapping(**kwargs), 5)


# --------------------------------------------------------------------------- síntesis
@pytest.mark.parametrize("wf", ["sine", "triangle", "saw", "square"])
def test_oscillators(wf):
    y = oscillator(wf, 440.0, SR, SR)
    assert np.abs(y).max() <= 1.1
    assert abs(y.mean()) < 0.02
    peak_hz = np.argmax(np.abs(np.fft.rfft(y))) * SR / len(y)
    assert peak_hz == pytest.approx(440, abs=2)


def test_lowpass_removes_highs():
    noise = np.random.default_rng(0).standard_normal(SR)
    filtered = lowpass(noise, 500.0, SR)
    f = np.fft.rfftfreq(SR, 1 / SR)
    hi = f > 5000
    ratio = (np.abs(np.fft.rfft(filtered))[hi] ** 2).sum() / (np.abs(np.fft.rfft(noise))[hi] ** 2).sum()
    assert ratio < 0.01


def test_adsr_shape():
    env = adsr(1000, 1500, 10000, attack=0.01, release=0.05)
    assert len(env) == 1500 and env[0] == 0 and env.max() == pytest.approx(1, abs=0.01)
    assert env[-1] < 0.01 and np.all(env >= 0)


def test_render_basic_properties():
    events = build_events(const_data(50), SonificationMapping(), 3)
    a = render(events, 3, SR, reverb_time=1.0)
    b = render(events, 3, SR, reverb_time=1.0)
    assert a.dtype == np.float32 and np.isfinite(a).all()
    assert np.abs(a).max() == pytest.approx(10 ** (-1 / 20), rel=1e-3)
    assert len(a) / SR == pytest.approx(3 + events[0].release + 1.0, abs=0.01)
    np.testing.assert_array_equal(a, b)  # determinista
    assert render([], 1, SR).max() == 0  # silencio sin notas, sin dividir por cero


def test_timbre_brightens_with_level():
    """Sin escuchar: el centroide espectral debe subir de bajo → medio → alto."""
    cents = []
    for v in (10, 50, 90):
        son = sonify(const_data(v), duration_s=3, sr=SR, drone=False)
        cents.append(spectral_centroid(son.audio[: 3 * SR], SR))
    assert cents[0] < cents[1] < cents[2]


# --------------------------------------------------------------------------- export
def test_wav_bytes():
    raw = to_wav_bytes(np.sin(np.linspace(0, 100, SR)), SR)
    with wave.open(io.BytesIO(raw)) as w:
        assert (w.getnchannels(), w.getsampwidth(), w.getframerate(), w.getnframes()) == (1, 2, SR, SR)


def test_midi_bytes_roundtrip():
    data = preprocess(make_sensor_frame(120), domain={"potenciometro": (0, 4095)})
    events = build_events(data, SonificationMapping(), 15)
    mid = mido.MidiFile(file=io.BytesIO(to_midi_bytes(events)))
    ons = [m for m in mid if m.type == "note_on"]
    assert len(ons) == len(events)
    assert [m.note for m in ons] == [e.midi for e in events]
    assert mid.length == pytest.approx(events[-1].start + events[-1].duration, abs=0.01)
    assert {m.channel for m in ons} == {e.level for e in events}


# --------------------------------------------------------------------------- extremo a extremo
def test_sonify_end_to_end(tmp_path):
    data = preprocess(make_sensor_frame(120), domain={"potenciometro": (0, 4095)}, target_points=200)
    son = sonify(data, duration_s=8, sr=SR, pitch="potenciometro", cutoff="luz", scale="menor_armonica")
    assert son.length_s > 8
    assert son.mapping.roles(data)["cutoff"] == "luz"
    payload = son.to_payload()
    assert payload["duration_s"] == 8 and len(payload["events"]) == len(son.events)
    assert len(payload["data"]["time"]) == 200
    frame = son.events_frame()
    assert set(frame["level_name"].astype(str)) <= {"bajo", "medio", "alto"}
    assert son.save_wav(tmp_path / "a" / "x.wav").stat().st_size > 8 * SR * 2
    assert son.save_midi(tmp_path / "x.mid").exists()
    with pytest.raises(TypeError, match="desconocidas"):
        sonify(data, escala="mayor")


def test_cli(tmp_path, capsys):
    from sonification.ingest.samples import write_all_formats

    src = write_all_formats(make_sensor_frame(60), tmp_path)["xlsx"]
    out = tmp_path / "o.wav"
    code = sonify_cli([str(src), "--pitch", "temperatura", "--duration", "4", "--sr", str(SR),
                       "-o", str(out), "--midi", "--domain", "temperatura=15:35"])
    assert code == 0
    assert out.exists() and out.with_suffix(".mid").exists()
    assert "pitch←temperatura" in capsys.readouterr().out
    assert sonify_cli([str(src), "--pitch", "nope"]) == 1


def test_default_styles_count():
    assert len(DEFAULT_STYLES) == 3
