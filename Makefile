.PHONY: test lint

VENV?=.venv
PYTHON?=python3

$(VENV)/bin/activate: requirements-dev.txt
	$(PYTHON) -m venv $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -r requirements-dev.txt

venv: $(VENV)/bin/activate

install-dev: venv

lint:
	@echo "No linters configured yet."

unit-test: venv
	$(VENV)/bin/pytest -q

test: unit-test
