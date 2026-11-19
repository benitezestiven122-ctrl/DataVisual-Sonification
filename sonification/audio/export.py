"""Exportación a WAV (16 bits, módulo estándar ``wave``) y MIDI (``mido``)."""

from __future__ import annotations

import io
import math
import wave
from typing import TYPE_CHECKING, Sequence

import numpy as np

if TYPE_CHECKING:
    from ..mapping import NoteEvent

MIDI_TICKS_PER_BEAT = 480
MIDI_TEMPO_US = 500_000  # 120 BPM fijo: 1 s = 960 ticks; las notas ya vienen en segundos
# Un canal por rango para poder separarlos en el DAW; programa General MIDI sugerido.
MIDI_CHANNELS = (0, 1, 2)
MIDI_PROGRAMS = (89, 11, 81)  # Pad 2 (warm), Vibráfono, Lead 2 (sawtooth)


def to_wav_bytes(audio: np.ndarray, sr: int) -> bytes:
    """Mono o (n, canales). Valores en [−1, 1] → PCM 16 bits."""
    a = np.asarray(audio, dtype=np.float64)
    channels = 1 if a.ndim == 1 else a.shape[1]
    pcm = (np.clip(a, -1.0, 1.0) * 32767).astype("<i2")
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())
    return buf.getvalue()


def _cc(value: float) -> int:
    return int(np.clip(round(value), 0, 127))


def to_midi_bytes(events: Sequence["NoteEvent"], cutoff_range: tuple[float, float] = (250.0, 9000.0)) -> bytes:
    """Archivo MIDI tipo 1 con una pista. Por nota envía CC74 (brillo ← cutoff),
    CC1 (modulación ← profundidad del LFO) y CC91 (reverb) antes del note-on."""
    import mido

    sec_to_ticks = MIDI_TICKS_PER_BEAT * 1_000_000 / MIDI_TEMPO_US
    lo, hi = cutoff_range
    timeline: list[tuple[int, int, mido.Message]] = []  # (tick, orden, mensaje)
    for ch, prog in zip(MIDI_CHANNELS, MIDI_PROGRAMS):
        timeline.append((0, 0, mido.Message("program_change", channel=ch, program=prog)))
    for ev in events:
        ch = MIDI_CHANNELS[ev.level]
        on = int(round(ev.start * sec_to_ticks))
        off = max(on + 1, int(round((ev.start + ev.duration) * sec_to_ticks)))
        brightness = 127 * math.log(max(ev.cutoff, lo) / lo) / math.log(hi / lo) if hi > lo else 64
        timeline += [
            (on, 1, mido.Message("control_change", channel=ch, control=74, value=_cc(brightness))),
            (on, 1, mido.Message("control_change", channel=ch, control=1, value=_cc(ev.lfo_depth * 127))),
            (on, 1, mido.Message("control_change", channel=ch, control=91, value=_cc(ev.reverb * 127))),
            (on, 2, mido.Message("note_on", channel=ch, note=ev.midi, velocity=max(1, _cc(ev.velocity * 127)))),
            (off, 0, mido.Message("note_off", channel=ch, note=ev.midi, velocity=0)),
        ]
    timeline.sort(key=lambda x: (x[0], x[1]))

    track = mido.MidiTrack()
    track.append(mido.MetaMessage("track_name", name="Decision Final", time=0))
    track.append(mido.MetaMessage("set_tempo", tempo=MIDI_TEMPO_US, time=0))
    last = 0
    for tick, _, msg in timeline:
        track.append(msg.copy(time=tick - last))
        last = tick
    track.append(mido.MetaMessage("end_of_track", time=0))

    mid = mido.MidiFile(type=1, ticks_per_beat=MIDI_TICKS_PER_BEAT)
    mid.tracks.append(track)
    buf = io.BytesIO()
    mid.save(file=buf)
    return buf.getvalue()
