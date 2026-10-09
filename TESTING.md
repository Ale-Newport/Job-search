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
