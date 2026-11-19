"""CLI: ``python -m sonification.ingest <archivo|URL> [opciones]``"""

from __future__ import annotations

import argparse
import json
import sys

from . import DataLoadError, describe_dataset, load_data, supported_formats


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="python -m sonification.ingest",
        description="Carga un archivo o URL en cualquier formato soportado y muestra un resumen.",
    )
    ap.add_argument("source", nargs="?", help="ruta o URL")
    ap.add_argument("-f", "--format", help="forzar formato (csv, xlsx, json, ...)")
    ap.add_argument("--sheet", help="hoja de Excel (nombre o índice)")
    ap.add_argument("--table", help="tabla de SQLite")
    ap.add_argument("--query", help="consulta SQL para SQLite")
    ap.add_argument("--record-path", help="ruta a la lista de registros en un JSON, p. ej. data.items")
    ap.add_argument("--sep", help="separador de CSV")
    ap.add_argument("--head", type=int, default=5, help="filas a mostrar (por defecto 5)")
    ap.add_argument("--list-formats", action="store_true", help="listar formatos soportados")
    args = ap.parse_args(argv)

    if args.list_formats:
        for fmt, exts in supported_formats().items():
            print(f"{fmt:8s} {' '.join(exts)}")
        return 0
    if not args.source:
        ap.error("falta la fuente (o usa --list-formats)")

    opts = {}
    if args.sheet is not None:
        opts["sheet"] = int(args.sheet) if args.sheet.isdigit() else args.sheet
    for key in ("table", "query", "record_path", "sep"):
        if getattr(args, key):
            opts[key] = getattr(args, key)

    try:
        df = load_data(args.source, fmt=args.format, **opts)
    except (DataLoadError, FileNotFoundError, OSError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(df.head(args.head).to_string())
    print()
    print(json.dumps(describe_dataset(df), indent=2, ensure_ascii=False, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
