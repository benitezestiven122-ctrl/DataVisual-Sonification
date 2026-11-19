"""Fase 3 — Orquestador de la sonificación.

Uso::

    from sonification.ingest import load_data
    from sonification.preprocess import preprocess
    from sonification.sonify import sonify

    data = preprocess(load_data("data/samples/sensor.csv"), domain={"potenciometro": (0, 4095)})
    son = sonify(data, duration_s=30, pitch="potenciometro", cutoff="luz", scale="menor_armonica")
    son.save_wav("output/sensor.wav")
    son.save_midi("output/sensor.mid")

CLI::

    python -m sonification.sonify data/samples/sensor.csv --pitch potenciometro --duration 20
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, fields, replace
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .audio.export import to_midi_bytes, to_wav_bytes
from .audio.scales import SCALES, note_to_midi
from .audio.synth import render
from .mapping import NoteEvent, SonificationMapping, build_events
from .preprocess import LEVEL_NAMES, ProcessedData

_MAPPING_FIELDS = {f.name for f in fields(SonificationMapping)}
DRONE_CONTROL_RATE = 100  # Hz
DRONE_SMOOTH_S = 0.6


@dataclass
class Sonification:
    audio: np.ndarray  # mono float32 en [−1, 1]; incluye la cola de reverb tras duration_s
    sr: int
    duration_s: float  # duración "musical" sobre la que se estiran los datos
    events: list[NoteEvent]
    mapping: SonificationMapping
    data: ProcessedData

    @property
    def length_s(self) -> float:
        return len(self.audio) / self.sr

    def to_wav_bytes(self) -> bytes:
        return to_wav_bytes(self.audio, self.sr)

    def to_midi_bytes(self) -> bytes:
        return to_midi_bytes(self.events, self.mapping.cutoff_range)

    def save_wav(self, path: str | Path) -> Path:
        return _write(path, self.to_wav_bytes())

    def save_midi(self, path: str | Path) -> Path:
        return _write(path, self.to_midi_bytes())

    def events_frame(self) -> pd.DataFrame:
        df = pd.DataFrame([vars(e) for e in self.events])
        if not df.empty:
            df["level_name"] = pd.Categorical(
                [LEVEL_NAMES[i] for i in df["level"]], categories=LEVEL_NAMES, ordered=True
            )
        return df

    def to_payload(self, decimals: int = 3) -> dict[str, Any]:
        """Datos + notas para el sketch p5.js (fase 4). Sincronía:
        ``posición en datos = audio.currentTime / duration_s``."""
        return {
            "duration_s": self.duration_s,
            "roles": self.mapping.roles(self.data),
            "data": self.data.to_payload(),
            "events": [
                {"t": round(e.start, decimals), "d": round(e.duration, decimals), "midi": e.midi,
                 "vel": round(e.velocity, decimals), "level": e.level}
                for e in self.events
            ],
        }


def _write(path: str | Path, data: bytes) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    return p


def drone_envelope(level: np.ndarray, duration_s: float, sr: int) -> np.ndarray:
    """Volumen 0–1 del pedal: sube cuando la serie está en rango bajo, con transiciones suaves."""
    n_ctrl = max(2, int(duration_s * DRONE_CONTROL_RATE))
    u = np.linspace(0, 1, n_ctrl)
    idx = np.clip(np.rint(u * (len(level) - 1)).astype(int), 0, len(level) - 1)
    is_low = (level[idx] == 0).astype(float)
    win = max(1, int(DRONE_SMOOTH_S * DRONE_CONTROL_RATE))
    smooth = pd.Series(is_low).rolling(win, center=True, min_periods=1).mean().to_numpy()
    t_audio = np.arange(int(duration_s * sr)) / sr
    return np.interp(t_audio, np.linspace(0, duration_s, n_ctrl), smooth)


def sonify(
    data: ProcessedData,
    mapping: SonificationMapping | None = None,
    *,
    duration_s: float = 30.0,
    sr: int = 44100,
    seed: int = 0,
    **overrides: Any,
) -> Sonification:
    """Datos preprocesados → audio + eventos. ``overrides`` reemplaza campos del mapeo
    (``pitch``, ``scale``, ``root``, ``bpm_range``…)."""
    unknown = set(overrides) - _MAPPING_FIELDS
    if unknown:
        raise TypeError(f"Opciones desconocidas: {sorted(unknown)}")
    if sr < 8000:
        raise ValueError("sr debe ser ≥ 8000")
    mapping = replace(mapping or SonificationMapping(), **overrides)
    events = build_events(data, mapping, duration_s)

    drone_env = None
    if mapping.drone:
        level = data.series[mapping.roles(data)["level"]].level
        drone_env = drone_envelope(level, duration_s, sr)

    audio = render(
        events, duration_s, sr,
        reverb_time=mapping.reverb_time, drone_env=drone_env,
        drone_midi=note_to_midi(mapping.root), drone_gain=mapping.drone_gain, seed=seed,
    )
    return Sonification(audio=audio, sr=sr, duration_s=duration_s, events=events, mapping=mapping, data=data)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _parse_domain(items: list[str]) -> dict[str, tuple[float, float]]:
    out = {}
    for item in items:
        try:
            col, rng = item.split("=", 1)
            lo, hi = rng.split(":", 1)
            out[col] = (float(lo), float(hi))
        except ValueError:
            raise SystemExit(f"--domain inválido '{item}'. Formato: columna=min:max") from None
    return out


def main(argv: list[str] | None = None) -> int:
    from .ingest import DataLoadError, load_data
    from .preprocess import PreprocessError, preprocess

    ap = argparse.ArgumentParser(prog="python -m sonification.sonify",
                                 description="Sonifica un archivo de datos a WAV (y MIDI).")
    ap.add_argument("source", help="archivo o URL de datos")
    ap.add_argument("--pitch", help="columna → altura (por defecto, la primera numérica)")
    ap.add_argument("--cutoff", help="columna → filtro")
    ap.add_argument("--tempo", help="columna → tempo")
    ap.add_argument("--level", help="columna → rango/timbre")
    ap.add_argument("--domain", action="append", default=[], metavar="COL=MIN:MAX",
                    help="rango físico del sensor, p. ej. potenciometro=0:4095 (repetible)")
    ap.add_argument("--duration", type=float, default=30.0, help="segundos (por defecto 30)")
    ap.add_argument("--scale", default="pentatonica_menor", choices=sorted(SCALES))
    ap.add_argument("--root", default="C3", help="tónica, p. ej. C3, A2, F#3")
    ap.add_argument("--octaves", type=int, default=2)
    ap.add_argument("--points", type=int, help="remuestrear los datos a N puntos")
    ap.add_argument("--sr", type=int, default=44100)
    ap.add_argument("--no-drone", action="store_true")
    ap.add_argument("-o", "--output", help="WAV de salida (por defecto output/<nombre>.wav)")
    ap.add_argument("--midi", nargs="?", const="", help="también exportar MIDI (ruta opcional)")
    ap.add_argument("--html", nargs="?", const="",
                    help="también exportar la visualización p5.js + audio en un HTML (ruta opcional)")
    args = ap.parse_args(argv)

    try:
        df = load_data(args.source)
        data = preprocess(df, domain=_parse_domain(args.domain), target_points=args.points)
        son = sonify(
            data, duration_s=args.duration, sr=args.sr, pitch=args.pitch, cutoff=args.cutoff,
            tempo=args.tempo, level=args.level, scale=args.scale, root=args.root,
            octaves=args.octaves, drone=not args.no_drone,
        )
    except (DataLoadError, PreprocessError, ValueError, FileNotFoundError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    stem = Path(str(args.source).split("?")[0]).stem or "sonificacion"
    wav = son.save_wav(args.output or Path("output") / f"{stem}.wav")
    print(f"WAV  {wav}  ({son.length_s:.1f} s, {len(son.events)} notas)")
    if args.midi is not None:
        mid = son.save_midi(args.midi or wav.with_suffix(".mid"))
        print(f"MIDI {mid}")
    if args.html is not None:
        from .visual import save_html

        page = save_html(son, args.html or wav.with_suffix(".html"), title=f"Decision Final — {stem}")
        print(f"HTML {page}")
    roles = son.mapping.roles(data)
    print("Mapeo: " + ", ".join(f"{k}←{v}" for k, v in roles.items()))
    share = data.series[roles["level"]].level_share()
    print("Rangos: " + ", ".join(f"{k} {v:.0%}" for k, v in share.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
