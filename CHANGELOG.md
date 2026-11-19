# Changelog

All notable changes to Meridian are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html). Before 1.0.0, minor versions may change
behaviour and storage; every database change still ships with a forward Alembic migration.

## [Unreleased]

## [0.1.0] - 2026-11-19

First public release: a local-first macOS job-search workspace and its Chrome/Safari browser assistant.

### Added

- **Desktop app.** Tauri 2 shell that launches a bundled Python (FastAPI) service on authenticated loopback, with a menu bar
  presence, single-instance lock, `meridian://` URL scheme and automatic service restart (at most three per minute).
- **Verified candidate profile.** CV import (PDF, DOCX, text) proposes facts that stay unverified until you confirm them;
  locked facts, fact relationships as skill evidence, and a reviewed public-project importer with provenance.
- **Daily discovery.** Greenhouse, Lever, Ashby and SmartRecruiters APIs, JSON/CSV/RSS feeds, GitHub Markdown/HTML job
  lists, Trackr UK graduate programmes, company career pages, and LinkedIn/Indeed alert emails. Up to 50 ranked
  recommendations a day with graduate/junior focus, Europe-only and London-first presets, and duplicate grouping.
- **Tracker and email timeline.** Every opportunity and application in one table; read-only Gmail, Microsoft Graph and IMAP
  connections (OAuth with PKCE, tokens in Keychain) match recruitment emails to applications.
- **Apply with review.** A visible, isolated browser that fills application forms only after per-section approval bound to the
  observed DOM, with separate approvals for navigation and final submission, durable workers and crash-safe recovery.
- **Answer engine.** Evidence-grounded answers to application questions with a fail-closed plain-question gate, legal and
  jurisdiction parsing (work authorisation, sponsorship), English and Spanish support, and a metamorphic test gate.
- **Optional AI.** Local writing through Ollama and the Laya decision model; OpenAI-compatible, Anthropic, Gemini and DeepSeek
  providers with Keychain keys, exact token pricing (including cache hits and off-peak hours) and monthly budgets.
- **Meridian Assistant** Chrome and Safari extension: field-by-field suggestions with provenance and trust tiers, Tab to insert,
  background drafts for long questions, a 6-digit pairing code over a separate loopback bridge, and hard rules never to touch
  passwords, CAPTCHAs, payment fields, consent boxes or the Submit button.
- `make backup-data` keeps one checked backup of the data folder in `.local-data/backups`.

### Security

- Loopback-only API with a random per-launch bearer token; restricted origins and Host headers; strict desktop CSP.
- Secrets only in macOS Keychain; AES-GCM encrypted backup exports; SSRF-safe public fetching that rejects private
  destinations and redirects; credential redaction in logs and checkpoints.
- Dependencies updated to clear known advisories: certifi 2026.10.23, cryptography 50.0.2, pillow 13.0.0, pypdf 6.21.4,
  setuptools 84.2.1 and soupsieve 4.1.2 (Python); vite 7.3.8, minimatch 11.0.9 and source-map-js 1.2.4 (frontend tooling).

### Fixed

- Browser tests select text with `ControlOrMeta+A`, and the concurrent-recovery test reads full `ps` command lines, so
  the suite also passes on Linux CI runners.
- A background-drafting test no longer races the company research it reads.

### Repository

- MIT licence, code of conduct, contributing guide, security policy with private vulnerability reporting.
- GitHub Actions: lint, the backend suite in four shards, frontend and extension builds, Rust checks, dependency audits,
  CodeQL and a full-history gitleaks scan. Dependabot for pip, npm, Cargo and Actions.

[Unreleased]: https://github.com/Ale-Newport/Job-search/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Ale-Newport/Job-search/releases/tag/v0.1.0
