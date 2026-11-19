"""Genera data/samples/ con los mismos datos de sensor en todos los formatos.

Uso:  python scripts/make_samples.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sonification.ingest.samples import make_sensor_frame, write_all_formats  # noqa: E402

if __name__ == "__main__":
    paths = write_all_formats(make_sensor_frame(), ROOT / "data" / "samples")
    for label, path in paths.items():
        print(f"{label:8s} {path.relative_to(ROOT)}")
