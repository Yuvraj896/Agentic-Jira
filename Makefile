.PHONY: help setup venv install ui-install create-env ollama-check ollama-pull dev api ui build test serve

SHELL := /bin/bash

VENV_DIR ?= .venv
PYTHON := $(VENV_DIR)/bin/python
PIP := $(VENV_DIR)/bin/pip

help:
	@echo "AIM Jira Assistant — local read-only Jira intelligence"
	@echo ""
	@echo "  make setup         Create venv, install Python/npm dependencies, create .env"
	@echo "  make ollama-pull   Pull the configured local model (default qwen3.5:4b)"
	@echo "  make dev           Run FastAPI and Vite development servers"
	@echo "  make test          Run backend tests and production frontend build"
	@echo "  make serve         Build UI and serve the complete app on port 8000"

setup: venv install ui-install create-env ollama-check
	@echo ""
	@echo "Setup complete. Next:"
	@echo "  1. Install/start Ollama if the check above says it is missing."
	@echo "  2. Run: make ollama-pull"
	@echo "  3. Run: make dev"

venv:
	@test -x $(PYTHON) || python3 -m venv $(VENV_DIR)

install: venv
	$(PIP) install -r requirements.txt

ui-install:
	cd frontend && npm install

create-env:
	@if [ ! -f .env ]; then cp .env.example .env; echo "Created .env from .env.example"; else echo ".env already exists"; fi

ollama-check:
	@if command -v ollama >/dev/null 2>&1; then \
		echo "Ollama installed: $$(ollama --version)"; \
	else \
		echo "Ollama is not installed. Install it from https://ollama.com/download"; \
	fi

ollama-pull:
	@command -v ollama >/dev/null 2>&1 || (echo "Install Ollama first: https://ollama.com/download" && exit 1)
	@set -a; [ ! -f .env ] || . ./.env; set +a; ollama pull "$${OLLAMA_MODEL:-qwen3.5:4b}"

dev:
	@if ! command -v ollama >/dev/null 2>&1; then \
		echo "Warning: Ollama is not installed; Ask Jira will be unavailable, but manual JQL will still work."; \
	elif ! curl -fsS http://127.0.0.1:11434/api/tags >/dev/null; then \
		echo "Warning: Ollama is not running; start it with 'ollama serve' to enable Ask Jira."; \
	fi
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
