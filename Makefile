PYTHON ?= python3
PYTHONPATH := src
RUN_ARGS ?= --help

.PHONY: test run

test:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m unittest discover -s tests

run:
	PYTHONPATH=$(PYTHONPATH) $(PYTHON) -m workipy $(RUN_ARGS)
