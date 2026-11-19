"""Resolución de fuentes: rutas, URLs, bytes y objetos tipo archivo → bytes en memoria."""

from __future__ import annotations

import re
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

USER_AGENT = "sonification-ingest/0.1"
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024  # 200 MB
DOWNLOAD_TIMEOUT_S = 30

_CONTENT_TYPES = {
    "text/csv": ".csv",
    "application/csv": ".csv",
    "text/tab-separated-values": ".tsv",
    "application/json": ".json",
    "application/x-ndjson": ".jsonl",
    "application/jsonl": ".jsonl",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.ms-excel": ".xls",
    "application/vnd.oasis.opendocument.spreadsheet": ".ods",
    "application/xml": ".xml",
    "text/xml": ".xml",
    "application/vnd.apache.parquet": ".parquet",
    "application/x-sqlite3": ".sqlite",
    "text/plain": ".txt",
}

_GSHEETS_RE = re.compile(r"^https?://docs\.google\.com/spreadsheets/d/([\w-]+)")
_GSHEETS_GID_RE = re.compile(r"[#&?]gid=(\d+)")
_GITHUB_BLOB_RE = re.compile(r"^https?://github\.com/([^/]+)/([^/]+)/blob/(.+)$")


@dataclass
class RawSource:
    """Contenido crudo de una fuente, listo para que un lector lo interprete."""

    data: bytes
    name: str  # ruta, URL o nombre del archivo (para mensajes y detección)
    extension: str = ""  # ".csv", ".xlsx"… o "" si se desconoce
    content_type: str = ""


def is_url(value: Any) -> bool:
    return isinstance(value, str) and value.lower().startswith(("http://", "https://"))


def rewrite_url(url: str) -> str:
    """Convierte enlaces "de navegador" en enlaces de descarga directa.

    - Google Sheets (compartido como "cualquiera con el enlace") → export CSV de la hoja.
    - GitHub ``/blob/`` → ``raw.githubusercontent.com``.
    """
    m = _GSHEETS_RE.match(url)
    if m and "/export" not in url:
        gid = _GSHEETS_GID_RE.search(url)
        out = f"https://docs.google.com/spreadsheets/d/{m.group(1)}/export?format=csv"
        return out + (f"&gid={gid.group(1)}" if gid else "")
    m = _GITHUB_BLOB_RE.match(url)
    if m:
        user, repo, rest = m.groups()
        return f"https://raw.githubusercontent.com/{user}/{repo}/{rest.split('?')[0]}"
    return url


def _download(url: str) -> RawSource:
    url = rewrite_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=DOWNLOAD_TIMEOUT_S) as resp:
        data = resp.read(MAX_DOWNLOAD_BYTES + 1)
        content_type = resp.headers.get_content_type()
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"La descarga supera {MAX_DOWNLOAD_BYTES // 2**20} MB: {url}")
    ext = Path(urlparse(url).path).suffix.lower()
    if ext not in _known_extensions():
        ext = _CONTENT_TYPES.get(content_type, "")
    return RawSource(data, url, ext, content_type)


def _known_extensions() -> set[str]:
    from .readers import extension_map  # import tardío para evitar ciclo

    return set(extension_map())


def resolve_source(source: Any) -> RawSource:
    """Lee cualquier fuente soportada y devuelve sus bytes con metadatos.

    Acepta: ruta (``str``/``Path``), URL ``http(s)``, ``bytes``, o un objeto tipo
    archivo con ``getvalue()``/``read()`` (archivo abierto, ``io.BytesIO``,
    ``UploadedFile`` de Streamlit). Si el objeto tiene ``.name`` se usa su extensión.
    """
    if isinstance(source, (bytes, bytearray, memoryview)):
        return RawSource(bytes(source), "<bytes>")

    if is_url(source):
        return _download(source)

    if isinstance(source, (str, Path)):
        path = Path(source).expanduser()
        if not path.is_file():
            raise FileNotFoundError(f"No existe el archivo: {path}")
        return RawSource(path.read_bytes(), str(path), path.suffix.lower())

    if hasattr(source, "getvalue"):
        data = source.getvalue()
    elif hasattr(source, "read"):
        data = source.read()
    else:
        raise TypeError(f"Tipo de fuente no soportado: {type(source).__name__}")
    if isinstance(data, str):
        data = data.encode("utf-8")
    name = str(getattr(source, "name", "") or "<buffer>")
    return RawSource(bytes(data), name, Path(name).suffix.lower())
