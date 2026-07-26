"""Punto de entrada único.

Funciona igual como:
  sdd <cmd>              (script de consola, tras `pip install -e .`)
  python -m sdd <cmd>
  python sdd/cli.py <cmd>

Los módulos del paquete se invocan también como scripts sueltos (subprocesos del
orquestador y de los gates), así que insertamos el dir del paquete en sys.path y
usamos imports por nombre de hermano — un solo modelo para todo.
"""
import os
import sys


def _console():
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import cli
    return cli.main()


if __name__ == "__main__":
    _console()
