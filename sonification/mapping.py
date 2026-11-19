"""Fase 3 — Mapeo datos → parámetros sonoros (matriz de la ideación, §4).

Convierte un :class:`ProcessedData` en una lista de :class:`NoteEvent`. Es un
paso puramente de datos (no genera audio): el mismo resultado alimenta al
sintetizador (WAV), a la exportación MIDI y, más adelante, al sketch p5.js.

Reglas
------
=================  =====================================================
Parámetro          Fuente
=================  =====================================================
Pitch              ``norm`` de la columna ``pitch`` cuantizado a la escala
Filtro (cutoff)    ``norm`` de la columna ``cutoff``, escala logarítmica
Tempo (BPM)        velocidad de cambio (``change``) de la columna ``tempo``
Estilo / timbre    rango (bajo / medio / alto) de la columna ``level``
=================  =====================================================

El eje temporal de los datos se estira linealmente sobre ``duration_s``: el
instante ``t`` del audio corresponde a la posición ``t / duration_s`` de los
datos, aunque la densidad de notas varíe con el tempo. Así el visual puede
sincronizarse leyendo solo ``audio.currentTime``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

import numpy as np
import pandas as pd

from .audio.scales import get_scale, note_to_midi, quantize
from .preprocess import ProcessedData


class MappingError(ValueError):
    pass


@dataclass(frozen=True)
class LevelStyle:
    """Cómo suena un rango (bajo / medio / alto)."""

    waveform: str = "triangle"  # sine | triangle | saw | square
    note_value: float = 0.5  # duración de cada nota en pulsos (1 = negra)
    gate: float = 0.85  # fracción de la duración en que la nota suena
    octave_shift: int = 0
    attack: float = 0.01  # s
    release: float = 0.25  # s
    lfo_rate: float = 2.0  # Hz
    lfo_depth: float = 0.3  # octavas de modulación del cutoff
    drive: float = 0.0  # 0 = limpio; >0 saturación tanh
    reverb: float = 0.3  # envío a reverb 0–1
    velocity: float = 0.8
    legato: bool = False  # une notas repetidas en una sola sostenida


DEFAULT_STYLES: tuple[LevelStyle, LevelStyle, LevelStyle] = (
    # 0–30 Bajo: tono grave sostenido, reverb amplio, textura suave
    LevelStyle(waveform="sine", note_value=2.0, gate=1.0, attack=0.25, release=0.9,
               lfo_rate=0.3, lfo_depth=0.15, reverb=0.6, velocity=0.7, legato=True),
    # 31–70 Medio: melodía pentatónica, ritmo moderado
    LevelStyle(waveform="triangle", note_value=0.5, gate=0.85, attack=0.01, release=0.25,
               lfo_rate=2.0, lfo_depth=0.3, reverb=0.3, velocity=0.8),
    # 71–100 Alto/crítico: sinte agudo, LFO rápido, distorsión
    LevelStyle(waveform="saw", note_value=0.25, gate=0.6, octave_shift=1, attack=0.003,
               release=0.12, lfo_rate=8.0, lfo_depth=1.0, drive=4.0, reverb=0.12, velocity=0.9),
)


@dataclass
class SonificationMapping:
    pitch: str | None = None  # None → primera serie de los datos
    cutoff: str | None = None  # None → la misma que pitch
    tempo: str | None = None
    level: str | None = None
    scale: str = "pentatonica_menor"
    root: str | int = "C3"
    octaves: int = 2
    bpm_range: tuple[float, float] = (60.0, 150.0)
    tempo_ref: float = 10.0
    """Cambio (en puntos 0–100 por cada 1 % del recorrido de los datos) que lleva
    el tempo al máximo. Absoluto: una serie tranquila suena lenta y una agitada
    rápida, sin importar cuántas filas tenga el archivo."""
    cutoff_range: tuple[float, float] = (250.0, 9000.0)  # Hz
    styles: tuple[LevelStyle, LevelStyle, LevelStyle] = field(default=DEFAULT_STYLES)
    drone: bool = True  # pedal grave que entra cuando la serie está en rango bajo
    drone_gain: float = 0.18
    reverb_time: float = 2.5  # s (RT60)

    def roles(self, data: ProcessedData) -> dict[str, str]:
        """Columna asignada a cada rol, validada contra ``data``."""
        if not data.names:
            raise MappingError("Los datos no tienen series")
        pitch = self.pitch or data.names[0]
        roles = {
            "pitch": pitch,
            "cutoff": self.cutoff or pitch,
            "tempo": self.tempo or pitch,
            "level": self.level or pitch,
        }
        missing = {r: c for r, c in roles.items() if c not in data.series}
        if missing:
            raise MappingError(f"Columnas no presentes en los datos: {missing}. Disponibles: {data.names}")
        return roles

    def validate(self) -> None:
        get_scale(self.scale)
        note_to_midi(self.root)
        if self.octaves < 1:
            raise MappingError("octaves debe ser ≥ 1")
        lo, hi = self.bpm_range
        if not 0 < lo <= hi:
            raise MappingError(f"bpm_range inválido: {self.bpm_range}")
        if self.tempo_ref <= 0:
            raise MappingError("tempo_ref debe ser > 0")
        lo, hi = self.cutoff_range
        if not 20 <= lo <= hi:
            raise MappingError(f"cutoff_range inválido: {self.cutoff_range}")
        if len(self.styles) != 3:
            raise MappingError("styles debe tener 3 estilos (bajo, medio, alto)")
        for st in self.styles:
            if st.waveform not in ("sine", "triangle", "saw", "square"):
                raise MappingError(f"waveform desconocida: {st.waveform}")
            if st.note_value <= 0 or not 0 < st.gate <= 1:
                raise MappingError("note_value debe ser > 0 y gate en (0, 1]")


@dataclass(frozen=True)
class NoteEvent:
    start: float  # s
    duration: float  # s (tiempo con la nota pulsada; la cola de release va aparte)
    midi: int
    velocity: float  # 0–1
    cutoff: float  # Hz
    level: int  # 0 bajo, 1 medio, 2 alto
    waveform: str
    lfo_rate: float
    lfo_depth: float
    drive: float
    reverb: float
    attack: float
    release: float
    data_pos: float  # 0–1: posición en los datos que originó la nota


def tempo_drive(change: np.ndarray, tempo_ref: float) -> np.ndarray:
    """Velocidad de cambio → 0–1 para el tempo, suavizada.

    ``change`` viene en puntos 0–100 *por fila*; se convierte a puntos por cada
    1 % del recorrido (independiente del número de filas) y se compara con
    ``tempo_ref``.
    """
    n = len(change)
    per_pct = change * (n - 1) / 100.0
    rel = np.clip(per_pct / tempo_ref, 0, 1)
    win = max(3, n // 50)
    return pd.Series(rel).rolling(win, center=True, min_periods=1).mean().to_numpy()


def build_events(data: ProcessedData, mapping: SonificationMapping, duration_s: float) -> list[NoteEvent]:
    mapping.validate()
    if duration_s <= 0:
        raise MappingError("duration_s debe ser > 0")
    roles = mapping.roles(data)
    scale = get_scale(mapping.scale)
    root = note_to_midi(mapping.root)

    n = len(data)
    grid = np.linspace(0.0, 1.0, n)
    pitch_norm = data.series[roles["pitch"]].norm
    cut_norm = data.series[roles["cutoff"]].norm
    tempo_rel = tempo_drive(data.series[roles["tempo"]].change, mapping.tempo_ref)
    level = data.series[roles["level"]].level
    bpm_lo, bpm_hi = mapping.bpm_range
    cut_lo, cut_hi = mapping.cutoff_range

    events: list[NoteEvent] = []
    t = 0.0
    eps = 1e-9
    while t < duration_s - eps:
        u = t / duration_s
        lvl = int(level[min(int(round(u * (n - 1))), n - 1)])
        st = mapping.styles[lvl]
        bpm = bpm_lo + (bpm_hi - bpm_lo) * float(np.interp(u, grid, tempo_rel))
        step = min(60.0 / bpm * st.note_value, duration_s - t)
        p = float(np.interp(u, grid, pitch_norm))
        midi = quantize(p, scale, root + 12 * st.octave_shift, mapping.octaves)
        cutoff = cut_lo * (cut_hi / cut_lo) ** (float(np.interp(u, grid, cut_norm)) / 100)
        ev = NoteEvent(
            start=t, duration=step * st.gate, midi=midi,
            velocity=st.velocity * (0.75 + 0.25 * p / 100), cutoff=cutoff, level=lvl,
            waveform=st.waveform, lfo_rate=st.lfo_rate, lfo_depth=st.lfo_depth, drive=st.drive,
            reverb=st.reverb, attack=st.attack, release=st.release, data_pos=u,
        )
        prev = events[-1] if events else None
        if (st.legato and prev is not None and prev.level == lvl and prev.midi == midi
                and abs(prev.start + prev.duration - t) < 1e-6):
            events[-1] = replace(prev, duration=prev.duration + ev.duration)  # nota sostenida
        else:
            events.append(ev)
        t += step
    return events
