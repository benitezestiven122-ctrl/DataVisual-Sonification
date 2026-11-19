"""Escalas musicales, nombres de nota y cuantización de valores 0–100 a notas MIDI."""

from __future__ import annotations

import re

import numpy as np

# Intervalos en semitonos desde la tónica. Las pentatónicas no tienen semitonos
# entre grados, así que cualquier secuencia suena consonante (ideación §3A).
SCALES: dict[str, tuple[int, ...]] = {
    "pentatonica_menor": (0, 3, 5, 7, 10),
    "pentatonica_mayor": (0, 2, 4, 7, 9),
    "menor_armonica": (0, 2, 3, 5, 7, 8, 11),
    "menor": (0, 2, 3, 5, 7, 8, 10),
    "mayor": (0, 2, 4, 5, 7, 9, 11),
    "dorica": (0, 2, 3, 5, 7, 9, 10),
    "blues": (0, 3, 5, 6, 7, 10),
    "cromatica": tuple(range(12)),
}

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
_PITCH_CLASS = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
_NOTE_RE = re.compile(r"\s*([A-Ga-g])([#b]?)(-?\d)\s*")


def note_to_midi(note: str | int) -> int:
    """``"C4"`` → 60, ``"A4"`` → 69, ``"F#3"`` → 54, ``"Bb2"`` → 46. Un ``int`` pasa tal cual."""
    if isinstance(note, (int, np.integer)):
        return int(note)
    m = _NOTE_RE.fullmatch(note)
    if not m:
        raise ValueError(f"Nota inválida: {note!r} (usa p. ej. 'C3', 'F#4', 'Bb2')")
    letter, accidental, octave = m.groups()
    pc = _PITCH_CLASS[letter.upper()] + {"#": 1, "b": -1, "": 0}[accidental]
    return 12 * (int(octave) + 1) + pc


def midi_to_name(midi: int) -> str:
    return f"{NOTE_NAMES[midi % 12]}{midi // 12 - 1}"


def midi_to_hz(midi):
    return 440.0 * 2.0 ** ((np.asarray(midi, dtype=float) - 69.0) / 12.0)


def get_scale(name: str) -> tuple[int, ...]:
    try:
        return SCALES[name]
    except KeyError:
        raise ValueError(f"Escala '{name}' desconocida. Disponibles: {', '.join(SCALES)}") from None


def scale_notes(scale: tuple[int, ...], root: int, octaves: int) -> np.ndarray:
    """Todas las notas MIDI de la escala desde ``root`` durante ``octaves`` octavas (incluye la octava final)."""
    steps = [12 * o + s for o in range(octaves) for s in scale] + [12 * octaves]
    return root + np.asarray(steps)


def quantize(norm, scale: tuple[int, ...], root: int, octaves: int):
    """Valor 0–100 → nota MIDI de la escala (0 = tónica, 100 = tónica + ``octaves`` octavas)."""
    notes = scale_notes(scale, root, octaves)
    idx = np.rint(np.clip(np.asarray(norm, dtype=float), 0, 100) / 100 * (len(notes) - 1)).astype(int)
    out = notes[idx]
    return int(out) if np.ndim(out) == 0 else out
