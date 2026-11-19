"""Piezas de audio de bajo nivel: escalas, síntesis y exportación.

No dependen del mapeo; el orquestador de alto nivel está en ``sonification.sonify``.
"""

from .export import to_midi_bytes, to_wav_bytes
from .scales import SCALES, midi_to_hz, midi_to_name, note_to_midi, quantize
from .synth import render

__all__ = [
    "SCALES", "midi_to_hz", "midi_to_name", "note_to_midi", "quantize",
    "render", "to_wav_bytes", "to_midi_bytes",
]
