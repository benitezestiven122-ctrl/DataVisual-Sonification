"""Fase 4 — Página HTML autocontenida: sketch p5.js + audio + datos.

``build_html(son)`` devuelve un único HTML (sketch, datos JSON y WAV en base64
incrustados; p5.js desde CDN) que sirve tanto para abrir en el navegador como
para ``st.components.v1.html`` en la app de Streamlit (fase 5).

    from sonification.visual import save_html
    save_html(son, "output/sensor.html")

El sketch vive en ``web/sketch.js`` (fuente única; editable con cualquier editor
de p5.js).
"""

from __future__ import annotations

import base64
import html
import json
import re
import warnings
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .sonify import Sonification

WEB_DIR = Path(__file__).resolve().parents[1] / "web"
SKETCH_PATH = WEB_DIR / "sketch.js"
P5_VERSION = "1.11.1"
P5_URL = f"https://cdnjs.cloudflare.com/ajax/libs/p5.js/{P5_VERSION}/p5.min.js"
MAX_POINTS_WARNING = 5000  # más puntos inflan el HTML sin mejorar el visual


def sketch_source() -> str:
    return SKETCH_PATH.read_text(encoding="utf-8")


def _json_for_script(obj: Any) -> str:
    """JSON seguro dentro de <script>: evita que '</script>' u otros cierres rompan la página."""
    return (
        json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
        .replace(" ", "\\u2028")
        .replace(" ", "\\u2029")
    )


_TEMPLATE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { color-scheme: dark; }
  html, body { margin: 0; background: #0b0d12; color: #dde3ee; font-family: system-ui, sans-serif; }
  #wrap { max-width: 1100px; margin: 0 auto; padding: 12px 16px; box-sizing: border-box; }
  #stage { width: 100%; line-height: 0; }
  #stage canvas { border-radius: 8px; }
  audio { width: 100%; margin-top: 8px; }
  .meta { font-size: 13px; opacity: .75; margin-top: 6px; }
  .err { padding: 24px; background: #2a1215; border-radius: 8px; line-height: 1.4; }
</style>
<script src="__P5_URL__"></script>
</head>
<body>
<div id="wrap">
  <div id="stage" role="img" aria-label="__ARIA__"></div>
  __AUDIO__
  <div class="meta">__META__</div>
</div>
<script>
__SKETCH__
</script>
<script>
(function () {
  var PAYLOAD = __PAYLOAD__;
  var OPTIONS = __OPTIONS__;
  var stage = document.getElementById("stage");
  var audio = document.getElementById("audio");
  if (typeof p5 === "undefined") {
    stage.innerHTML = '<div class="err">No se pudo cargar p5.js (¿sin conexión?). El audio sigue disponible abajo.</div>';
    return;
  }
  window.decisionSketch = createDecisionSketch(stage, PAYLOAD, audio, OPTIONS);
  document.addEventListener("keydown", function (e) {
    if (audio && e.code === "Space" && e.target === document.body) {
      e.preventDefault();
      if (audio.paused) audio.play(); else audio.pause();
    }
  });
})();
</script>
</body>
</html>
"""


_PLACEHOLDER_RE = re.compile(r"__(?:TITLE|P5_URL|ARIA|AUDIO|META|SKETCH|PAYLOAD|OPTIONS)__")


def build_html(
    son: "Sonification",
    *,
    title: str = "Decision Final",
    include_audio: bool = True,
    autoplay: bool = False,
    options: dict[str, Any] | None = None,
) -> str:
    """HTML autocontenido con visual + audio sincronizados.

    ``options`` se pasa al sketch: ``particles``, ``historyHeight`` (0–1),
    ``maxHeight`` (px), ``primary`` (serie a visualizar), ``reduceMotion``.
    Sin audio, el sketch recorre los datos en bucle (modo demo).
    """
    if len(son.data) > MAX_POINTS_WARNING:
        warnings.warn(
            f"{len(son.data)} puntos inflan el HTML; usa preprocess(..., target_points≈1000)",
            stacklevel=2,
        )
    payload = son.to_payload()
    roles = payload["roles"]
    primary = (options or {}).get("primary") or roles["level"]
    if primary not in payload["data"]["series"]:
        raise ValueError(f"Serie '{primary}' no está en los datos: {list(payload['data']['series'])}")

    audio_tag = ""
    if include_audio:
        b64 = base64.b64encode(son.to_wav_bytes()).decode("ascii")
        audio_tag = (
            f'<audio id="audio" controls preload="auto"{" autoplay" if autoplay else ""} '
            f'src="data:audio/wav;base64,{b64}"></audio>'
        )

    share = son.data.series[primary].level_share()
    meta = (
        f"Altura ← {roles['pitch']} · filtro ← {roles['cutoff']} · tempo ← {roles['tempo']} · "
        f"rango ← {roles['level']} — {len(son.events)} notas, {son.duration_s:g} s. "
        + " · ".join(f"{k} {v:.0%}" for k, v in share.items())
        + (". Espacio: reproducir/pausar." if include_audio else ". Sin audio: modo demo en bucle.")
    )
    aria = (f"Visualización generativa de la serie {primary}: "
            + ", ".join(f"{v:.0%} del tiempo en rango {k}" for k, v in share.items()))

    replacements = {
        "__TITLE__": html.escape(title),
        "__P5_URL__": P5_URL,
        "__ARIA__": html.escape(aria, quote=True),
        "__AUDIO__": audio_tag,
        "__META__": html.escape(meta),
        "__SKETCH__": sketch_source().replace("</script", "<\\/script"),
        "__PAYLOAD__": _json_for_script(payload),
        "__OPTIONS__": _json_for_script({**(options or {}), "primary": primary}),
    }
    # Una sola pasada: el texto insertado (datos del usuario) nunca se vuelve a escanear.
    return _PLACEHOLDER_RE.sub(lambda m: replacements[m.group(0)], _TEMPLATE)


def save_html(son: "Sonification", path: str | Path, **kwargs: Any) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(build_html(son, **kwargs), encoding="utf-8")
    return p
