SHELL := /bin/bash
PYTHON := .venv/bin/python
.PHONY: setup dev dev-web backend test test-ui lint typecheck build build-app clean
setup:
	./scripts/bootstrap_macos.sh
dev:
	npm run dev
backend:
	./scripts/dev_backend.sh
dev-web:
	npm --prefix frontend run dev
test:
	MERIDIAN_TEST=1 .venv/bin/pytest -q
	cargo test --manifest-path src-tauri/Cargo.toml --quiet
test-ui:
	$(PYTHON) frontend/tests/ui_smoke.py
	$(PYTHON) frontend/tests/workflow_e2e.py
	$(PYTHON) frontend/tests/onboarding_e2e.py
lint:
	.venv/bin/ruff check backend scripts
	npm --prefix frontend run lint
	cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
typecheck:
	npm --prefix frontend run typecheck
	cargo check --manifest-path src-tauri/Cargo.toml
build:
	./scripts/build_sidecar.sh
	npm run build
build-app:
	./scripts/build_sidecar.sh
	npm run build:app
clean:
	$(PYTHON) scripts/clean_build.py
