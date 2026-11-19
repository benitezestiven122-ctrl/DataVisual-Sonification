"""Sintetizador sustractivo en numpy/scipy (sin tarjeta de sonido: genera muestras).

Cadena por nota::

    oscilador (anti-aliasing PolyBLEP) → filtro paso bajo con cutoff variable (+ LFO)
      → saturación tanh → envolvente ADSR → bus seco + envío a reverb

Mezcla final: seco + reverb por convolución + pedal grave opcional, normalizado a −1 dBFS.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Sequence

import numpy as np
from scipy.signal import fftconvolve, lfilter

from .scales import midi_to_hz

if TYPE_CHECKING:
    from ..mapping import NoteEvent

WAVEFORMS = ("sine", "triangle", "saw", "square")
FILTER_BLOCK = 128  # muestras por bloque con coeficientes de filtro constantes
FILTER_Q = 0.9
PEAK = 10 ** (-1 / 20)  # −1 dBFS


# --------------------------------------------------------------------------- #
# Osciladores
# --------------------------------------------------------------------------- #
def _polyblep(t: np.ndarray, dt: float) -> np.ndarray:
    """Corrección polinómica alrededor de la discontinuidad (reduce aliasing)."""
    out = np.zeros_like(t)
    lo = t < dt
    x = t[lo] / dt
    out[lo] = 2 * x - x * x - 1
    hi = t > 1 - dt
    x = (t[hi] - 1) / dt
    out[hi] = x * x + 2 * x + 1
    return out


def oscillator(waveform: str, freq: float, n: int, sr: int, phase0: float = 0.0) -> np.ndarray:
    dt = freq / sr
    t = (phase0 + dt * np.arange(n)) % 1.0
    if waveform == "sine":
        return np.sin(2 * np.pi * t)
    if waveform == "triangle":
        return 1.0 - 4.0 * np.abs(t - 0.5)
    if waveform == "saw":
        return 2.0 * t - 1.0 - _polyblep(t, dt)
    if waveform == "square":
        return np.where(t < 0.5, 1.0, -1.0) + _polyblep(t, dt) - _polyblep((t + 0.5) % 1.0, dt)
    raise ValueError(f"Forma de onda desconocida: {waveform}. Opciones: {WAVEFORMS}")


# --------------------------------------------------------------------------- #
# Envolvente y filtro
# --------------------------------------------------------------------------- #
def adsr(n_on: int, n_total: int, sr: int, attack: float, release: float,
         decay: float = 0.08, sustain: float = 0.75) -> np.ndarray:
    """Envolvente lineal A-D-S y release cuadrático desde el nivel alcanzado."""
    a = max(1, int(attack * sr))
    d = max(1, int(decay * sr))
    k = np.arange(n_on)
    on = np.where(k < a, k / a, np.where(k < a + d, 1 - (1 - sustain) * (k - a) / d, sustain))
    start_level = on[-1] if n_on else 0.0
    n_rel = n_total - n_on
    rel = start_level * (1 - np.linspace(0, 1, n_rel, endpoint=False)) ** 2 if n_rel > 0 else np.empty(0)
    return np.concatenate([on, rel])


def biquad_lowpass(fc: float, sr: int, q: float = FILTER_Q) -> tuple[np.ndarray, np.ndarray]:
    """Coeficientes RBJ (Audio EQ Cookbook) de un paso bajo de 2.º orden."""
    w0 = 2 * np.pi * fc / sr
    alpha = np.sin(w0) / (2 * q)
    cw = np.cos(w0)
    b = np.array([(1 - cw) / 2, 1 - cw, (1 - cw) / 2])
    a = np.array([1 + alpha, -2 * cw, 1 - alpha])
    return b / a[0], a / a[0]


def lowpass(x: np.ndarray, cutoff: np.ndarray | float, sr: int, block: int = FILTER_BLOCK) -> np.ndarray:
    """Paso bajo con cutoff variable en el tiempo (coeficientes por bloque, estado continuo)."""
    cutoff = np.broadcast_to(np.asarray(cutoff, dtype=float), x.shape)
    out = np.empty_like(x)
    zi = np.zeros(2)
    for s in range(0, len(x), block):
        b, a = biquad_lowpass(float(cutoff[s]), sr)
        out[s:s + block], zi = lfilter(b, a, x[s:s + block], zi=zi)
    return out


def saturate(x: np.ndarray, drive: float) -> np.ndarray:
    if drive <= 0:
        return x
    g = 1.0 + drive
    return np.tanh(g * x) / np.tanh(g)


# --------------------------------------------------------------------------- #
# Reverb
# --------------------------------------------------------------------------- #
def reverb_ir(rt60: float, sr: int, seed: int = 0, predelay: float = 0.02) -> np.ndarray:
    """Respuesta al impulso sintética: ruido con decaimiento exponencial, algo oscurecido."""
    rng = np.random.default_rng(seed)
    n = int(rt60 * sr)
    t = np.arange(n) / sr
    ir = rng.standard_normal(n) * np.exp(-6.91 * t / rt60)  # −60 dB en rt60
    ir = lfilter([0.35], [1, -0.65], ir)  # quita brillo a la cola
    ir /= np.sqrt(np.sum(ir ** 2))
    return np.concatenate([np.zeros(int(predelay * sr)), ir])


# --------------------------------------------------------------------------- #
# Render
# --------------------------------------------------------------------------- #
def render_note(ev: "NoteEvent", sr: int, max_len: int | None = None) -> np.ndarray:
    n_on = max(1, int(ev.duration * sr))
    n = n_on + int(ev.release * sr)
    if max_len is not None:
        n = min(n, max_len)
        n_on = min(n_on, n)
    s0 = int(ev.start * sr)
    freq = float(midi_to_hz(ev.midi))
    y = oscillator(ev.waveform, freq, n, sr)
    t_abs = (s0 + np.arange(n)) / sr  # LFO en fase con el tiempo global (continuo entre notas)
    lfo = np.sin(2 * np.pi * ev.lfo_rate * t_abs)
    cutoff = np.clip(ev.cutoff * 2.0 ** (ev.lfo_depth * lfo), 30.0, 0.45 * sr)
    y = lowpass(y, cutoff, sr)
    y = saturate(y, ev.drive)
    return y * adsr(n_on, n, sr, ev.attack, ev.release) * ev.velocity


def render(
    events: Sequence["NoteEvent"],
    duration_s: float,
    sr: int = 44100,
    *,
    reverb_time: float = 2.5,
    drone_env: np.ndarray | None = None,
    drone_midi: int = 48,
    drone_gain: float = 0.18,
    seed: int = 0,
) -> np.ndarray:
    """Mezcla todos los eventos en una señal mono ``float32`` normalizada a −1 dBFS.

    ``drone_env`` (opcional, 0–1, una muestra por muestra de audio hasta
    ``duration_s``) controla el volumen de un pedal grave continuo.
    """
    max_release = max((ev.release for ev in events), default=0.0)
    total = int((duration_s + max_release + reverb_time) * sr)
    dry = np.zeros(total)
    send = np.zeros(total)

    for ev in events:
        s0 = int(ev.start * sr)
        if s0 >= total:
            continue
        y = render_note(ev, sr, max_len=total - s0)
        dry[s0:s0 + len(y)] += y * (1 - 0.5 * ev.reverb)
        send[s0:s0 + len(y)] += y * ev.reverb

    if drone_env is not None and drone_gain > 0:
        env = np.zeros(total)
        m = min(len(drone_env), total)
        env[:m] = drone_env[:m]
        f = float(midi_to_hz(drone_midi))
        t = np.arange(total) / sr
        tone = np.sin(2 * np.pi * f * t) + 0.3 * np.sin(2 * np.pi * 2 * f * t + 0.5)
        drone = tone * env * drone_gain
        dry += drone * 0.6
        send += drone * 0.6

    mix = dry + fftconvolve(send, reverb_ir(reverb_time, sr, seed))[:total]
    peak = np.max(np.abs(mix))
    if peak > 0:
        mix *= PEAK / peak
    fade = min(int(0.01 * sr), total // 2)
    if fade:
        ramp = np.linspace(0, 1, fade)
        mix[:fade] *= ramp
        mix[-fade:] *= ramp[::-1]
    return mix.astype(np.float32)
