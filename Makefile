UV ?= uv
PYTHON ?= python

.PHONY: help install run lock export clean

help:
	@echo "Targets:"
	@echo "  install  Sync dependencies from uv.lock"
	@echo "  run      Run the engine"
	@echo "  lock     Refresh uv.lock"
	@echo "  export   Export requirements.txt from lock"
	@echo "  clean    Remove local virtual environment"

install:
	$(UV) sync --frozen

run:
	$(UV) run $(PYTHON) -m src.main

lock:
	$(UV) lock

export:
	$(UV) export --format requirements-txt --no-hashes -o requirements.txt

clean:
	rm -rf .venv

