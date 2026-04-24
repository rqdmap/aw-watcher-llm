.DEFAULT_GOAL := help

PYTHON ?= python3
VENV_DIR ?= .venv
VENV_PYTHON := $(VENV_DIR)/bin/python

.PHONY: help venv install run run-once test build clean

help:
	@printf "Available targets:\n"
	@printf "  make venv      Create the local virtual environment in %s\n" "$(VENV_DIR)"
	@printf "  make install   Install aw-watcher-llm into %s in editable mode\n" "$(VENV_DIR)"
	@printf "  make run       Run the long-lived watcher service from the repo\n"
	@printf "  make run-once  Run one watcher cycle for a quick verification\n"
	@printf "  make test      Run the local test suite\n"
	@printf "  make build     Build sdist and wheel into dist/\n"
	@printf "  make clean     Remove local build artifacts\n"

$(VENV_PYTHON):
	$(PYTHON) -m venv $(VENV_DIR)

venv: $(VENV_PYTHON)

install: venv
	$(VENV_PYTHON) -m pip install -U pip
	$(VENV_PYTHON) -m pip install -e .

run: venv
	$(VENV_PYTHON) -m aw_watcher_llm

run-once: venv
	$(VENV_PYTHON) -m aw_watcher_llm run --iterations 1 --interval-seconds 0.1

test: venv
	$(VENV_PYTHON) -m unittest discover -s tests

build: venv
	$(VENV_PYTHON) -m pip install -U build
	$(VENV_PYTHON) -m build

clean:
	rm -rf $(VENV_DIR) build dist *.egg-info .pytest_cache
	find . -name "__pycache__" -type d -prune -exec rm -rf {} +
