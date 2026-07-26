# Atajos. La aplicación es un solo paquete `sdd/` con un único comando `sdd`.
# Tras `pip install -e .`, `sdd <cmd>` funciona directo; aquí se usa por conveniencia.

PY ?= python

setup:
	$(PY) -m pip install -e .

demo:
	$(PY) -m sdd demo

web:
	$(PY) -m sdd web

test:
	$(PY) -m sdd test

doctor:
	$(PY) -m sdd doctor

gates:
	$(PY) -m sdd gates --node $(NODE) --workdir $(WORKDIR)

run:
	$(PY) -m sdd run --project $(PROJECT)

.PHONY: setup demo web test doctor gates run
