SHELL := /bin/bash
PYTHON := .venv/bin/python
.PHONY: setup dev dev-web backend test test-ui test-ui-suite test-ext test-ext-fast release-gate lint typecheck extension extension-deps extension-safari extension-typecheck build build-app clean clean-dry
setup:
	./scripts/bootstrap_macos.sh
dev:
	npm run dev
# The development backend also opens the browser bridge (ports 47615 to 47624). With the installed Meridian.app running too, two
# programs answer like Meridian and the extension refuses to pair with either until one is gone: quit one, or start this one without
# the bridge: MERIDIAN_BRIDGE=0 make backend (see scripts/dev_backend.sh).
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
	$(PYTHON) frontend/tests/recovery_e2e.py
	$(PYTHON) frontend/tests/networking_e2e.py
# The same UI scripts plus the browser-assistant panel tests (mocked, and against the real backend and bridge), every one on its OWN
# isolated stack (temporary data directory, in-memory secrets, free ports, nothing left running). Needs no hand-started backend or
# Vite, unlike test-ui above. A script that is missing is an error here, never a skip. `npm --prefix frontend run test:ui` runs the
# same suite; `npm --prefix frontend run test:ui:chain` is the hand-started chain that test-ui runs.
test-ui-suite:
	$(PYTHON) frontend/tests/run_ui_suite.py
# Browser-extension tests. step LABEL PATH COMMAND... runs COMMAND only when PATH exists. A missing runner is reported and skipped
# (default: a checkout without every workstream still tests what it has) unless STRICT=1, the release gate, where a missing runner
# is a failure, so that a deleted or renamed runner can never pass a release unnoticed. A failing step fails the target after the
# others have run.
STRICT ?= 0
define EXT_STEP
status=0; \
step() { label="$$1"; path="$$2"; shift 2; \
  if [ -e "$$path" ]; then echo "== $$label"; "$$@" || { echo "== FAILED: $$label"; status=1; }; \
  elif [ "$(STRICT)" = "1" ]; then echo "== MISSING: $$label: $$path does not exist (STRICT=1)"; status=1; \
  else echo "== skipped $$label: $$path does not exist"; fi; }
endef
# Every extension test. Slow steps, on an idle machine: content script tests about 3 min, real-widget matrix about 2 min,
# panel UI tests about 2 min, real-browser end-to-end tests 3 to 4 min (twice that on a loaded one). The unit tests, the
# widget helper tests and the bundle report take seconds. Use test-ext-fast while iterating. The widget helper tests run from
# extension/ (their TypeScript loader is addressed relative to it); the bundle report works from any directory.
test-ext:
	$(PYTHON) scripts/build_extension.py --e2e
	@$(EXT_STEP); \
	step "extension unit tests" extension/tests/unit/register.mjs npm --prefix extension test; \
	step "widget helper unit tests" extension/tests/widgets/unit sh -c 'cd extension && node --import ./tests/unit/register.mjs --test "tests/widgets/unit/*.test.mjs"'; \
	step "content script tests (slow)" extension/tests/content/run.py $(PYTHON) extension/tests/content/run.py; \
	step "panel UI tests (slow)" extension/tests/ui/run.py $(PYTHON) extension/tests/ui/run.py; \
	step "real-widget compatibility matrix (slow)" extension/tests/widgets/run.py $(PYTHON) extension/tests/widgets/run.py; \
	step "production content bundle report" extension/tests/widgets/bundle-report.mjs node extension/tests/widgets/bundle-report.mjs; \
	step "real-browser end-to-end tests (slowest)" extension/tests/e2e/test_extension_e2e.py $(PYTHON) extension/tests/e2e/test_extension_e2e.py$(if $(filter 1,$(STRICT)), --strict); \
	exit $$status
# The quick extension loop: unit tests, content script tests and panel UI tests (the last two build the bundles they need
# themselves; the dependencies are installed first when stale). Leaves out the widget helper tests, the widget matrix, the bundle
# report and the real-browser end-to-end tests, which test-ext adds. The content and panel runs are still minutes, not seconds:
# run `npm --prefix extension test` alone for the seconds-long loop.
test-ext-fast: extension-deps
	@$(EXT_STEP); \
	step "extension unit tests" extension/tests/unit/register.mjs npm --prefix extension test; \
	step "content script tests (slow)" extension/tests/content/run.py $(PYTHON) extension/tests/content/run.py; \
	step "panel UI tests (slow)" extension/tests/ui/run.py $(PYTHON) extension/tests/ui/run.py; \
	exit $$status
# The release gate: the backend and desktop tests, lint and typecheck, the isolated UI suite, and EVERY extension suite strictly (a missing runner fails,
# and the real-browser suite counts BLOCKED or KNOWN ISSUE scenarios as failures). Slow: it is the long run before a release.
release-gate: lint typecheck test test-ui-suite
	$(MAKE) test-ext STRICT=1
# Python files are only checked, never rewritten (--no-fix); the extension's Python is its test harness and its icon script.
lint:
	.venv/bin/ruff check --no-fix backend scripts extension/tests extension/scripts
	npm --prefix frontend run lint
	cargo fmt --manifest-path src-tauri/Cargo.toml -- --check
typecheck:
	@test -x extension/node_modules/.bin/tsc || { echo "Extension dependencies are not installed: run make setup (or npm --prefix extension ci)." >&2; exit 1; }
	npm --prefix frontend run typecheck
	npm --prefix extension run typecheck
	cargo check --manifest-path src-tauri/Cargo.toml
# Install the extension's npm dependencies only when node_modules is missing or differs from package-lock.json (offline first).
extension-deps:
	$(PYTHON) scripts/build_extension.py --install-only
# Chrome + Safari web extension into extension/dist, type-checked and production-checked (needs Node.js).
extension:
	$(PYTHON) scripts/build_extension.py
# Also builds and signs the Safari app extension into build/safari and proves pluginkit sees it (needs Xcode).
extension-safari:
	$(PYTHON) scripts/build_extension.py --safari --registration-check
extension-typecheck:
	$(PYTHON) scripts/build_extension.py --typecheck-only
build: extension
	MERIDIAN_EXTENSION_READY=1 ./scripts/build_sidecar.sh
	npm run build
build-app: extension
	MERIDIAN_EXTENSION_READY=1 ./scripts/build_sidecar.sh
	npm run build:app
# Deletes the generated build outputs listed by clean-dry (never .local-data, node_modules or sources). Reads stdin only
# for the prompt that --yes skips, so `make clean` asks nothing: the target itself is the confirmation.
clean:
	$(PYTHON) scripts/clean_build.py --yes
# Shows what clean would delete and deletes nothing.
clean-dry:
	$(PYTHON) scripts/clean_build.py --dry-run
