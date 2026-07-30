"""Comportamiento cliente de AutoScrum Control Tower.

El JavaScript vive en `static/app.js`, no en un literal de Python. Cuando estaba
embebido eran 53 KB en 157 lineas dentro de una cadena: ningun linter, formateador ni
resaltador lo veia como codigo, el diff de un cambio de una linea era ilegible, y el
conteo de lineas que usa la disciplina de tamano del proyecto medía el envoltorio en
lugar del contenido. Este modulo solo lo carga; la interfaz publica (`SCRIPT`) no
cambia, asi que `webpage.py` sigue incrustandolo en el HTML igual que antes.

Se lee con newline="" para no traducir saltos de linea: en Windows, dejar que Python
los convierta cambiaria los bytes servidos al navegador respecto al archivo en disco.
"""
from pathlib import Path

ASSET = Path(__file__).resolve().parents[1] / "static" / "app.js"

with open(ASSET, "r", encoding="utf-8", newline="") as _fh:
    SCRIPT = _fh.read()
