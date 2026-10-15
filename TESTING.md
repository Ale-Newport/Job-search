# Testing and measured limits

Run backend tests with `MERIDIAN_TEST=1 .venv/bin/python -m pytest -q`. Browser and failure-injection tests require localhost sockets and a usable Chrome installation. Test data, browser profiles and documents are synthetic and temporary. Run `make lint`, `make typecheck`, and Rust tests before packaging.

Start the UI against an isolated data directory, never production:

```sh
MERIDIAN_TEST=1 MERIDIAN_DATA_DIR="$PWD/.local-data/upgrade-ui" make backend
make dev-web
make test-ui
```

The UI suite covers smoke/navigation, candidate/document/application workflows, onboarding, API reconnect/recovery controls and synthetic networking handoff. Networking interception prevents external navigation and sends no messages. `scripts/validate_packaged_workers.py` starts the signed bundle's frozen sidecar on an isolated root containing spaces, generates two documents in child processes, injects SIGSTOP into its browser worker, verifies recovery and checks API latency. It then hard-kills the owned backend while its worker is stopped, restarts it and checks orphan cleanup, recovery of the same task and preservation of completed steps.

The reliability suite runs three independent eight-step applications on Greenhouse/Ashby/Workday-style fixtures. It injects a Chrome crash or a stopped worker, a page reload/conditional question in a sibling, and a slow upload in another. All reach final review, the affected worker retries, siblings retain their first attempts, health requests stay below one second, and cancellation cannot invalidate a sibling review. Other regressions cover SQLite contention, pending-submission recovery, ambiguity/idempotence, provider cursors, immutable document formats, expired review resumption, Workday pagination and tall iframe hit testing.

Real-form preparation requires explicit destination-specific authorisation. `scripts/validate_real_forms.py` pins the ten approved URLs, verifies the approved CV hash and never calls submission. Unknown answers, legal consent, unsupported controls and authenticating remain human tasks. Upload selection is distinguished from independent site acknowledgement. Correct-answer accuracy and true external recovery are not inferred from resolved-answer counts.

For discovery, `scripts/validate_discovery.py` uses a private migrated copy and produces `docs/upgrade-source-health.json`. A failed or generic scan must not close existing jobs. Unknown deadlines remain unknown, and public-board caps must be disclosed. The original production database is never reset or used as a test fixture.

See `docs/UPGRADE_REPORT.md` for executed results and the remaining production limitations. A passing build is not evidence of universal ATS completion or live mailbox authorisation.

## Browser assistant (Chrome and Safari extension)

Everything here uses synthetic data, temporary profiles and loopback fixture servers. No test contacts an employer site, a model provider, the Keychain or an installed Meridian.app; the end-to-end suite finds its backend through a test-only port override stored in the extension (never the fixed bridge ports 47615-47624), and its one production-build scenario skips itself when something already listens there. Extension dependencies come from `extension/package-lock.json` (`npm --prefix extension ci`, or `make extension-deps`, which is a no-op when `node_modules` already matches).

| Layer | Command | Notes |
|---|---|---|
| Backend | `MERIDIAN_TEST=1 .venv/bin/pytest -q backend/tests/test_extension_*.py backend/tests/test_build_extension.py` (also part of `make test`) | Pairing state machine, bridge guard and limits, hello proof, sessions and polling, suggestion states and tiers, resolver parity with `automation/runner.py`, generation queue, research (offline fixtures, SSRF refusals), feedback persistence, documents, backup exclusion, log hygiene, shutdown |
| Answer safety | `MERIDIAN_TEST=1 .venv/bin/pytest -q backend/tests/test_engine_plain.py backend/tests/test_engine_metamorphic.py` | The plain-question gate and the held-out metamorphic gate (about 3,600 generated cases, about 2 minutes); the checker is itself tested with synthetic observations. `PYTHONPATH=backend/tests/fixtures/engine MERIDIAN_TEST=1 .venv/bin/python -m metamorphic --family sponsorship` runs one family |
| Type and lint | `make typecheck`, `make lint` | `typecheck` includes `npm --prefix extension run typecheck`; `lint` includes `ruff` over `extension/tests` and `extension/scripts` |
| Extension units | `npm --prefix extension test` | Node test runner: worker, bridge client, hello proof (against an independent HMAC), storage and credential placement, polling, activation, pairing, Safari-like engine, third-party notice check. Seconds |
| Extension quick loop | `make test-ext-fast` | Units + `extension/tests/content/run.py` (real keyboard in Playwright, mock background, Chromium and WebKit) + `extension/tests/ui/run.py` (visual layer in English and Spanish, light and dark, several widths) |
| Everything | `make test-ext` | Adds widget helper tests, the real-widget matrix (`extension/tests/widgets/run.py`), the production bundle report and the real-browser end-to-end suite. Builds the `e2e` variants first |
| Release gate | `make release-gate` or `make test-ext STRICT=1` | Backend, lint, typecheck, isolated UI suite and every extension suite; a missing runner, a blocked scenario or a known issue fails |
| Real browser e2e | `.venv/bin/python extension/tests/e2e/test_extension_e2e.py [-k a,b] [--repeat N] [--recipe chrome] [--headed] [--list] [--strict] [--junit out.xml]` | Real extension, real backend (temp data dir, in-memory secrets, fake loopback model), fixture pages. `--recipe chrome` loads the extension into branded Chrome through CDP `Extensions.loadUnpacked`. `MERIDIAN_E2E_SLOWDOWN` (default 3) scales waits on a busy machine. See `extension/tests/e2e/README.md` |
| Widgets | `cd extension && ../.venv/bin/python tests/widgets/run.py [--browser webkit] [--world main]`; `.venv/bin/python extension/tests/widgets/real_run.py` | Real widget libraries and ATS-widget replicas driven with the real keyboard; `real_run.py` uses the real worker, backend and UI on six pages. See `extension/tests/widgets/README.md` |
| Chromium smoke | `npm --prefix extension run smoke` | Worker, pairing against an independent Python fake bridge, options page, toolbar path, hint |
| Production build | `make extension` (runs `scripts/check-prod-build.mjs`), `npm --prefix extension run verify` | Fails if any `__E2E__` hook, `eval`, unsupported API or placeholder bundle remains |
| Desktop UI | `.venv/bin/python frontend/tests/extension_e2e.py --base-url http://127.0.0.1:1432 [--browser webkit]`, add `--live` for the real backend and bridge; both are part of `python frontend/tests/run_ui_suite.py` | Settings > Browser assistant, pairing approval, install guidance, activity |
| Safari | `make extension-safari` | Builds and signs the appex, then registers a throwaway copy to check that `pluginkit` and Safari's extension service see it, and removes it. Needs Xcode. It does not drive Safari |

The widget fixtures are committed bundles (`extension/tests/widgets/fixtures`); rebuild them with `cd extension/tests/widgets && npm ci && node build-fixtures.mjs`. After editing an extension fixture page, refresh the golden scan with `test_extension_e2e.py -k fixtures_scan --update-golden`. `MERIDIAN_E2E_EXTENSION_DIR` and `MERIDIAN_E2E_BACKEND_DIR` point the e2e suite at scratch copies of the product so a fix can be reverted there and the scenario that guards it shown to fail (mutation proof).

What the suites do not prove: they do not drive real Safari, do not contact live employer sites (widget replicas are not verified against live tenants), and run on one machine at a time. Results from a specific run are recorded, with their limits, in `docs/VERIFICATION.md`.
