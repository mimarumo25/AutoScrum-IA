"""Tokens y estilos de la torre de control. Se mantienen locales y sin CDN.

El CSS vive en `static/app.css` por el mismo motivo que el JS en `static/app.js`:
eran 37 KB en 30 lineas dentro de un literal de Python, invisibles para cualquier
herramienta de CSS. Este modulo solo lo carga y conserva la interfaz publica (`CSS`).
"""
from pathlib import Path

ASSET = Path(__file__).resolve().parents[1] / "static" / "app.css"

with open(ASSET, "r", encoding="utf-8", newline="") as _fh:
    CSS = _fh.read()
