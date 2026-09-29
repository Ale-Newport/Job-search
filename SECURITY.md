# Security and privacy

Meridian is a single-user local application. It binds its API only to loopback and requires a random per-launch bearer token delivered through a scoped Tauri command. Web origins and Host headers are restricted. The desktop CSP permits only local assets and its loopback API. There is no telemetry.

macOS Keychain holds email tokens, IMAP app passwords and provider keys. SQLite contains configuration and references, never authentication secrets. Browser cookies stay in the dedicated profile and are not logged, sent to text models or included in backups. Your Mac account and FileVault protect local candidate/email data at rest; the live SQLite database itself is not encrypted. Backup exports are authenticated-encrypted with AES-GCM and a scrypt-derived key.

Public source fetching validates HTTP(S) destinations and redirects and rejects local/private destinations. Remote HTML and email are parsed as data. They never execute inside the desktop renderer. React escapes displayed strings. No model output becomes JavaScript, a selector, shell command or executable code. Candidate uploads are size-limited, stored using generated names, and parsed without invoking embedded macros.

Each browser action uses an observed element reference and a document identity. Stale, detached, disabled or obstructed targets are rejected. The global pause gate is checked before input. CAPTCHA, MFA, restricted sources and unknown required data require human interaction. Sensitive fields require explicit policy; automatic submission never silently approves legal/demographic answers.

Review approval is bound to the prepared DOM state, candidate evidence, document versions and answers. Submission attempts are recorded before clicking. The same job cannot receive a second application row. A crash or missing success evidence does not permit blind resubmission. Browser/model confidence cannot override these guards.

Gmail/Graph access is read-only; IMAP uses TLS and readonly mailbox selection. No automatic email sending is implemented. OAuth uses PKCE, unpredictable one-use expiring state and Keychain token storage. Provider client registrations and user consent are required. Arbitrary external endpoint configuration can disclose the selected data to that endpoint; choose providers you trust. Laya and Ollama local modes enforce loopback endpoints.

Structured logs omit request bodies and access logs. Credential-like content is redacted. Browser observation history can include submitted candidate information and should be treated as private. Logs are local. Backups exclude session data and secret references; imported integrations reconnect after restore.

Development mode intentionally uses a fixed local test token. It must be explicitly enabled with `MERIDIAN_DEV=1`; production never silently enables it. CI/browser tests use temporary profiles and synthetic recruitment fixtures, never production accounts or job applications.

Report vulnerabilities privately to the repository owner with a minimal reproduction and no live credentials. If a secret was exposed, revoke it at the provider and remove it from Keychain. Deleting the application's data folder removes personal records and browser sessions; remove Keychain entries separately under service `com.meridian.jobagent`.

The packaged PyInstaller sidecar extracts its Python shared libraries at launch. Ad-hoc local signing needs `com.apple.security.cs.disable-library-validation` for these libraries to load; hardened runtime remains enabled. The same entitlement is applied by Tauri to the signed bundle. Distribution builds should review this requirement with a consistent Developer ID signature across the interpreter and native dependencies.
