.PHONY: help setup venv install ui-install create-env dev api ui build test serve

SHELL := /bin/bash

VENV_DIR ?= .venv
PYTHON := $(VENV_DIR)/bin/python
PIP := $(VENV_DIR)/bin/pip

help:
	@echo "AIM Jira Assistant — local read-only Jira intelligence"
	@echo ""
	@echo "  make setup         Create venv, install Python/npm dependencies, create .env"
	@echo "  make dev           Run FastAPI and Vite development servers"
	@echo "  make test          Run backend tests and production frontend build"
	@echo "  make serve         Build UI and serve the complete app on port 8000"

setup: venv install ui-install create-env
	@echo ""
	@echo "Setup complete. Next:"
	@echo "  1. Add OPENAI_API_KEY to .env."
	@echo "  2. Run: make dev"

venv:
	@test -x $(PYTHON) || python3 -m venv $(VENV_DIR)

install: venv
	$(PIP) install -r requirements.txt

ui-install:
	cd frontend && npm install

create-env:
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env from .env.example"; else echo ".env already exists"; fi

dev:
	@backend_pid=""; \
		cleanup() { \
			trap - INT TERM HUP EXIT; \
			if [[ -n "$$backend_pid" ]] && kill -0 "$$backend_pid" 2>/dev/null; then \
				kill "$$backend_pid" 2>/dev/null || true; \
				wait "$$backend_pid" 2>/dev/null || true; \
			fi; \
		}; \
		trap cleanup INT TERM HUP EXIT; \
		$(PYTHON) -m uvicorn backend.server:app --host 127.0.0.1 --port 8000 & \
		backend_pid=$$!; \
		frontend_status=0; \
		cd frontend && npm run dev || frontend_status=$$?; \
		if [[ "$$frontend_status" -eq 129 || "$$frontend_status" -eq 130 ]]; then exit 0; fi; \
		exit "$$frontend_status"

api:
	$(PYTHON) -m uvicorn backend.server:app --reload --host 127.0.0.1 --port 8000

ui:
	cd frontend && npm run dev

build:
	cd frontend && npm run build

test:
	PYTHONDONTWRITEBYTECODE=1 $(PYTHON) -m pytest -q -p no:cacheprovider tests
	cd frontend && npm run build

serve: build
	SERVE_UI=true $(PYTHON) -m uvicorn backend.server:app --host 127.0.0.1 --port 8000
