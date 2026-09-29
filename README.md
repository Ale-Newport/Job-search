# Meridian

A private macOS workspace for a personal job search: a verified candidate knowledge base, versioned documents, public job discovery, explainable matching, review-first browser assistance, application history and read-only recruitment email tracking.

[Guía de inicio en español](docs/INICIO_RAPIDO.es.md) · [Verification and observed limitations](docs/VERIFICATION.md)

Meridian runs locally. Tauri 2 hosts the interface and launches a bundled Python service on authenticated loopback. SQLite, documents, browser sessions and logs remain on the Mac. There is no telemetry or project-owned remote server. Cloud AI is optional and requires explicit configuration.

## Start

Requirements for this Apple Silicon build: macOS 15+, Python 3.12, Node/npm, Rust, Xcode Command Line Tools. Google Chrome is preferred for the separate application browser; Playwright Chromium is an alternative. The packaged desktop application includes its Python backend, so Python is not required for ordinary use of the installed app. Optional Laya installation uses a separate Python runtime.

```sh
make setup
make dev
```

The first launch starts with an empty database and automation paused. No fabricated candidate facts, jobs or application counts are installed. Closing the window keeps the menu bar application running. Use **Quit Meridian** to stop the local service. Background polling runs while the desktop process is running; it does not wake a sleeping Mac.

For frontend development without Tauri, run `make backend` and `make dev-web` in two terminals. This explicitly uses the development token and `.local-data`; do not use development mode for a shared or network-facing deployment. Both services bind to loopback.

## First use

1. Import a PDF, DOCX or text CV in Documents. Review proposed facts in Profile, correct them and mark the facts verified. Import does not verify facts automatically.
2. Add contact details, education, experience, projects, skills and work authorization. A locked fact cannot be changed without unlocking it. Add relationships between facts as evidence for skills.
3. Configure search profiles and public sources. For a Greenhouse, Lever or Ashby source, provide its board URL and board identifier. SmartRecruiters, JSON, CSV, RSS, GitHub lists and company career pages are also supported. Company watchlist entries create monitored sources.
4. Run a search or paste a job URL. Inspect score components, missing evidence and exclusions. A missing skill is not automatically a rejection.
5. Select a job and prepare an application. Choose a document version or preview a grounded CV/cover letter and its difference from the base. Approve the resulting document before autonomous use.
6. Open the dedicated browser to sign into a platform yourself. Passwords never pass through Meridian's profile forms. Chrome sessions stay inside Meridian's separate browser profile.
7. Resume automation, then Apply in Review mode. Known, verified fields are filled. Unknown or sensitive questions create human tasks. Resolve a question, save the answer and resume. Review the exact answers and documents before **Approve & Submit**.
8. Connect recruitment email or import an `.eml` message. Confident links update the timeline; ambiguous links wait for your decision.

Manual mode prepares only. Review mode fills but requires submission approval. Auto mode additionally requires an explicit global opt-in, exact approved domain, sufficient match/confidence, approved documents, verified answers, no sensitive fields and rate limits. Rules can prepare matching jobs; automatic rules use the same submission gates. Select a default approved CV for rules that should proceed without document selection.

**Pause automation** prevents subsequent browser actions. It cannot undo a network request already sent to a website. **Take control** lets you interact with the visible browser. After an uncertain submission or process interruption, reconcile what actually happened before retrying. Meridian never assumes that clicking Submit means the application succeeded.

## Discovery and ATS support

Public ATS APIs are preferred over scraping. The supported application adapters identify Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Workable, Teamtailor, iCIMS and Taleo and reuse guarded semantic form handling. Adapter identification is not a promise that every employer's custom form can be completed unattended. Unsupported widgets, ambiguous navigation, validation errors, CAPTCHA and MFA stop for human takeover.

LinkedIn and Indeed use manual browser handoff. There is no stealth, CAPTCHA bypass, fingerprint spoofing or rate-limit evasion. Company pages without structured job data may require a direct public ATS source or manual job entry. Source health records the actual fetch result/error.

Search profiles include editable scoring weights and explicit role, technology, industry, arrangement, contract, posting-age and sponsorship preferences. Unknown evidence is reported rather than invented. Salary minimums are annual and compared only when the posting explicitly specifies an annual period in the same configured currency. Analytics uses recorded submission and response milestones, with denominators, response times and source/company/role/CV/match cohorts; small samples are marked.

## Email setup

Meridian reads mail; it has no automatic email-send function.

- **Gmail:** register a desktop OAuth client in your Google Cloud project, enable Gmail API and add yourself as a test user when applicable. Configure the client ID and, if supplied by Google, the client secret in the secret field. Connect opens Google's consent page with the `gmail.readonly` scope. Tokens live in macOS Keychain. Desktop OAuth clients allow a loopback callback on the local service's ephemeral port.
- **Outlook:** register a public/native Microsoft identity application with delegated `Mail.Read` and `offline_access`. Register `http://localhost/api/oauth/callback` under Mobile and desktop applications. Meridian uses that path with an ephemeral port, which Microsoft ignores when matching localhost redirects. Tenant policy may require administrator consent. The authorization-code flow uses PKCE. See [Microsoft redirect URI rules](https://learn.microsoft.com/entra/identity-platform/reply-url).
- **IMAP:** configure a TLS server, port (normally 993), username and folder. Put an app password in the separate secret field, then Connect. IMAP reads with `BODY.PEEK` and does not mark messages read.

First OAuth sync covers 30 days; later syncs use incremental windows. IMAP uses UID/UIDVALIDITY and bounded batches. Email messages are deduplicated, conservatively linked and classified. Relative assessment deadlines use the received date; uncertain links remain human tasks. A late confirmation cannot regress a later interview status. Provider authorization and real account delivery must be tested using your own account.

## Local AI and optional providers

Browser decision models and text generation models are separate. Laya/Jev see a scoped indexed DOM observation and choose only a permitted operation/observed element. They cannot supply JavaScript, selectors or shell commands. Deterministic form resolution runs first. Text generation uses selected verified facts and always returns a draft with fact references for review.

In Settings → AI, install/start/test the optional local Laya runtime. Model downloads require internet and disk space. The setup helper is also available:

```sh
./scripts/setup_laya.sh
```

The runtime uses MLX on Apple Silicon and PyTorch on Intel. It is pinned to a reviewed upstream revision, isolated from the main backend, and serves `http://127.0.0.1:8791`. If it is unavailable, deterministic functionality still works and uncertain actions request human input. Test and Benchmark report actual inference; an available model is not evidence of reliable decisions on every form. See [dependency research](docs/DEPENDENCIES.md) and the recorded benchmark evidence.

Jev is a hosted TypeSafe service. Store its key in Keychain and select Jev or explicitly enable the hybrid hosted fallback. That sends the scoped page observation to TypeSafe. Hosted usage requires a provider account.

For local text generation, install the official [Ollama macOS app](https://ollama.com) in `~/Applications` or `/Applications`. Meridian manages a loopback-only service when provider `ollama` and `http://127.0.0.1:11434/v1` are selected, disables cloud inference, and stores models under its own `models/ollama` directory. The bundled runtime configuration uses `llama3.2:3b-instruct-q4_K_M` with an 8192-token context; download that model into the Meridian cache before use. Meridian never stops or takes over an existing service on port 11434. Its own service and runner stop when Meridian quits. Test connection uses a synthetic candidate, not your CV.

OpenAI, Anthropic, Gemini and other OpenAI-compatible endpoints are also configurable. The DeepSeek preset supplies its endpoint, model and token prices but neither supplies a key nor enables spending. Remote text generation requires a Keychain API key, configured token prices and a positive monthly budget. Only selected candidate facts and limited job context are sent. A citation to an existing fact is not proof of every generated sentence; generated text remains subject to review.

## Evidence-based onboarding

Setup progress comes from saved evidence: an imported document, reviewed facts, configured roles, explicit browser-login confirmation, an authorized mailbox with a successful sync, actual AI probes, reviewed automation boundaries and a successful source fetch. Dismissing the guide only hides it. Changing a provider, credential or automation boundary invalidates the corresponding evidence.

DOCX import preserves hyperlinks, table order, list items and complete education, employment and project entries. Skills are deduplicated and languages remain separate. Every proposal includes its source version and line context; importing does not automatically verify claims. Dates and metrics are retained exactly, while work rights, total experience and missing identity details are never inferred. Re-extracting a document creates a new immutable version and supersedes only older unreviewed, unlocked proposals.

## Storage and backup

Production data is under `~/Library/Application Support/Meridian/`:

- `database/meridian.sqlite3`: SQLite WAL database, Alembic migrations and FTS5 index.
- `documents/`: immutable versioned files.
- `browser-profile/`: isolated Chrome session, potentially including authenticated cookies.
- `models/`, `cache/`, `logs/`, `backups/`: isolated runtime data.

Settings provides encrypted export/restore using scrypt and AES-GCM. Use a password of at least 12 characters and keep it safely; there is no recovery service. Backups exclude browser credentials and Keychain secrets. Restoring pauses automation and requires reconnecting email/AI integrations. A pre-restore encrypted backup is retained. Export size is currently bounded to 512 MB.

## Development and verification

```sh
make test       # unit, SQLite/API integration, email and local browser tests
make lint       # Python, frontend and Rust formatting
make typecheck  # TypeScript and Rust
make build     # Python sidecar + frontend + macOS app/DMG
```

Tests use temporary databases and localhost ATS fixtures. No test submits a real employment application. Browser tests need Chrome or Playwright Chromium and permission to listen on loopback. `requirements.lock`, npm lockfiles and `Cargo.lock` record resolved versions. Rebuild the sidecar after backend changes before testing production packaging.

For `make test-ui`, run an isolated QA backend (`MERIDIAN_TEST=1 MERIDIAN_DATA_DIR="$PWD/.local-data/qa" make backend`) and `make dev-web` in separate terminals first. The UI tests create synthetic local records and must not target production data.

Build output: `src-tauri/target/release/bundle/macos/Meridian.app` and `src-tauri/target/release/bundle/dmg/`. Copy the app into Applications or run it from the build directory. Local builds are ad-hoc signed, not notarized. The embedded Python runtime requires the narrowly scoped `disable-library-validation` entitlement to load its extracted native libraries; hardened runtime otherwise stays enabled. The bundled Homebrew Python requires macOS 15 or newer. For distribution, configure an Apple Developer signing identity and notarization credentials through the documented Tauri process; never commit certificates or credentials. See [Tauri signing documentation](https://v2.tauri.app/distribute/sign/macos/).

## Troubleshooting

- **Service unavailable:** allow a few seconds for the bundled Python service to start, retry, then inspect `logs/backend.log`. The port/token are regenerated each launch.
- **Browser unavailable/profile in use:** close another Meridian browser instance using that profile. Do not point it at your ordinary Chrome profile. Install Chrome or run `.venv/bin/python -m playwright install chromium`.
- **Form stopped:** inspect the run and human task. Complete CAPTCHA, login, MFA or unsupported controls yourself, then resume. An uncertain submission needs reconciliation first.
- **No jobs found:** inspect source health and the board identifier. Career websites may change structure or require human access. Public sources need network connectivity.
- **Email not linked:** open the email and choose the application. Similar roles at the same company intentionally remain ambiguous.
- **Keychain unavailable:** Meridian does not fall back to plaintext credential storage. Unlock the login Keychain and approve the operating-system prompt if requested.
- **Offline:** local profile, documents, jobs, history and analytics remain available. Network operations report their errors without removing saved data.

See [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md), [CONTRIBUTING.md](CONTRIBUTING.md) and [verification evidence](docs/VERIFICATION.md) for boundaries, implementation details and observed test results.
