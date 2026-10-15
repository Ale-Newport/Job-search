# Meridian Assistant: browser extension design and contract

Status: **implemented** (Chrome MV3 and Safari Web Extension; reconciled with the code on 15 October 2026). This is the design record. User and admin guide: [EXTENSION.md](EXTENSION.md). Code is authoritative for shapes and numbers:

- `backend/jobagent/extension/schemas.py` and its mirror `extension/src/shared/protocol.ts` (bridge wire contract, protocol 1)
- `extension/src/shared/messages.ts` (content, worker and options messages; stored state)
- `extension/src/content/ui-api.ts` (logic and visual layer)
- `migrations/versions/0013_browser_extension.py` (tables)

If prose and code disagree, the code wins and the prose is fixed in the same change. Where this file states a limit that tests pin, the limit is in the code named in that section.

## 1. Product goal and principles

The user opens a job application in Chrome or Safari, clicks the Meridian toolbar icon and is walked through the form **field by field**: focusing a field shows what Meridian recommends for that field; **Tab inserts it and moves to the next question**, whose recommendation is already showing. Long personal questions are drafted in the background from the verified profile **plus research on the company and role**.

Principles, in priority order:

1. **Never wrong, never silent.** Only verified facts, labelled derivations and validated drafts are offered, each with its source and a trust colour (verified / check this / AI draft). Anything unknown stays blank and says so. The answer engine fails closed (section 5.4).
2. **Never acts for the user beyond the field.** No Next/Submit/Apply click, no password, one-time code, card, bank, ID-number, CAPTCHA or MFA field, no consent or legal box, no signature without Enter, no upload except the user's own approved CV. Submission is always the user's own click.
3. **Zero friction.** One toolbar click; Tab does the obvious thing; pairing happens once. Failures are calm, specific and name one next action.
4. **Private by construction.** Data stays on the Mac. The extension talks only to Meridian over loopback; suggestions render in a closed shadow root; typed text goes to Meridian only on an explicit save.
5. **Reuse, don't fork.** The answer engine (`CandidateEngine`, `ApplicationAnswerLLM`, disclosure policies, `OptionSelector`), the DOM observer (`automation/observation.js`) and the discovery/research primitives are reused. `resolver.py` reproduces `runner.py` decisions without refactoring it; a parity test guards drift.

Out of scope: Firefox, Edge Web Store, Windows/Linux, mobile Safari, automatic submission, research on LinkedIn/Indeed/Glassdoor (never fetched), Chrome Web Store publication (the build is store-ready; publication needs the owner's developer account). Other Chromium browsers can load the same folder and are listed as "Chrome".

## 2. Architecture and trust boundaries

```
 web page (untrusted)        extension (user trusts)                         Meridian.app (trusted)
┌──────────────────┐   ┌──────────────────────────────────┐        ┌───────────────────────────────┐
│ job form DOM     │◄──┤ content script (isolated world)  │        │ Tauri shell + React UI        │
│ (any origin,     │   │  scan • keyboard • fill • chip   │        │   /api/extension/* (app token) │
│  iframes)        │   │  closed shadow-root UI           │        │                               │
└──────────────────┘   └───────────────▲──────────────────┘        │ Python sidecar                │
                                       │ runtime messages          │  main API  127.0.0.1:random   │
                       ┌───────────────┴──────────────────┐ HTTP   │  bridge    127.0.0.1:47615-24 │
                       │ service worker (stateless)       ├───────►│   pairing • sessions • poll   │
                       │  bridge client, credential, hello│ Bearer │   suggest • research • drafts │
                       │  proof per request, activation,  │ mrd1.. │   feedback • documents • track │
                       │  one multiplexed long-poll       │        │  SQLite • Keychain • text LLM │
                       └──────────────────────────────────┘        └───────────────────────────────┘
```

- The page can never reach the bridge usefully: content scripts do not fetch it; only the worker does, and the bridge rejects any request whose `Origin` is not an extension scheme. A page can fire a simple GET but cannot read the answer.
- The desktop UI keeps the main API with its per-launch token. The bridge uses a **separate credential** `mrd1.<client_id>.<secret>` created by an approved pairing, stored hashed (sha256), revocable.
- Before a credential is sent, the worker verifies an HMAC proof that the listener holds the key (4.3). A squatter on a port gets no token once a browser is paired; before the first pairing there is nothing to prove (known limit).
- Employer text (labels, descriptions, job text, fetched pages) is **untrusted data** end to end: never executed, never rendered as HTML (text nodes only), values inserted as plain text through native setters, and passed to the engine through its prompt-injection hygiene.

## 3. Repository layout

```
backend/jobagent/extension/
  __init__.py      PROTOCOL, BRIDGE_PORTS, CHROME_EXTENSION_IDS
  schemas.py       wire models                     server.py     BridgeServer (pre-bound socket, SystemExit-safe uvicorn)
  app.py           BridgeGuard, hello, pairing, disconnect, status, settings; mounts routes.py
  auth.py          mrd1 tokens, Client, current_client   pairing.py   pairing state machine, rate limits, hello proof
  approval.py      native code dialog (osascript) + notification    assets.py   install the Chrome folder from the bundle
  api.py           /api/extension/* for the desktop UI, status/settings helpers, audit pruning, tracker summary
  routes.py        /v1 sessions, suggest, poll, regenerate, feedback, track, research, documents (thin; logic in session.py)
  session.py       SessionRegistry: pages, event cursor, multiplexed poll, lazy rows, feedback, tracking
  suggest.py       controls -> suggestions (states, tiers, why/needs, option mapping)    resolver.py   FieldResolver (parity with runner)
  generation.py    background drafting queue           draft_basis.py / draft_limits.py   what a draft was based on; automatic-draft ledger
  research.py      ResearchManager, pinned fetch       places.py     where a job is (country)    urls.py   URL scrubbing, https rules
  documents_pick.py which approved CV/cover letter may be offered     feedback.py   what may be remembered     markup.py / text.py   bounded parsing
  state.py         lifecycle of the services on app.state
backend/jobagent/intelligence/{plain,jurisdiction,languages,academic,legal,salary}.py   readers the resolver relies on (section 5.4)
backend/jobagent/automation/dialcode.py   "which country's dial code" questions, shared with Auto Apply
extension/       package.json (+lock), build.mjs, manifest.template.json, ats-hosts.json, _locales/{en,es}, icons
  src/background/  index, router, activation, pairing, polling       src/hint/  ATS hint pill
  src/content/     controller, scan, state, keyboard, tabstops, fill, frames, session, submit-watch, page-report, anchors, popups,
                   visibility, sensitive, roots, widgets/, ui/* (chip, panel, notices, create, styles), i18n.ts, ui-api.ts
  src/shared/      protocol, messages, bridge-client, connection, hello-proof, storage, vault, tabs, hosts, permissions, i18n-core, badge
  src/options/     options and welcome page          safari/   Swift host and appex sources, no Xcode project
  tests/           unit, content, ui, widgets, e2e, fixtures
frontend/src/extension.tsx, extension-state.ts, extension-copy.ts, extension.css; edits to App, settings, onboarding, tracking, details
scripts/build_extension.py, build_safari_assistant.sh, build_signed_app.py, build_sidecar.sh; Makefile targets; src-tauri/Info.plist (meridian://)
docs/EXTENSION.md (EN guide), docs/INICIO_RAPIDO.es.md (ES section)
```

The Chrome extension id is pinned by `extension/manifest-key.txt` (public key only; the private key lives ONLY in the git-ignored `.local-data/extension/` of the developer's Mac and must be backed up offline: the fixed extension id, and with it the allow-list of origins the bridge recognises and every paired Chrome, depend on it, and a new key means a new id, so every paired Chrome would have to be paired again): **`ophngbnbiekjhemmabbpildjedfphlbc`**.

## 4. The bridge

### 4.1 Listener and guard

- A second `uvicorn.Server` in the sidecar's event loop serves a separate FastAPI app (`create_bridge_app(main_app)`) on a socket Meridian pre-binds (`SO_REUSEADDR`) on the first free port of `47615-47624`. `MERIDIAN_BRIDGE_PORT` overrides (`PORT`, `LOW-HIGH`, or `0` for tests). A bind failure never kills the main API (`serve()` is wrapped in `except SystemExit`); Settings shows `port_in_use` with the name of the process holding the first port when `lsof` can tell, and a restart button. Disabled under `MERIDIAN_TEST` unless `MERIDIAN_BRIDGE` or `MERIDIAN_BRIDGE_PORT` is set; `MERIDIAN_BRIDGE=0` always disables. `create_app(..., bridge=False)` is the default; `run()` passes `bridge_enabled()`. On shutdown, waiting polls are answered first so the quit takes milliseconds.
- `BridgeGuard` (pure ASGI middleware): `Host` must be `127.0.0.1` or `localhost` with an optional numeric port (400 `host`); an `Origin`, if present, must match `^(chrome|safari-web)-extension://[A-Za-z0-9-]{8,64}$` (403 `origin`); `OPTIONS` answers 204 only for such an origin and echoes it (`Access-Control-Allow-Private-Network` on request); a request without `Origin` is accepted only as GET/HEAD, every mutating request needs one; bodies are `application/json` (415) of at most 1 MiB, enforced while streaming (413 `too_large`; a malformed `Content-Length` is 400); 30 s deadline per request, 45 s for paths ending `/poll` (a poll waits at most 25 s); at most 64 concurrent requests; `Cache-Control: no-store`, `nosniff`, `no-referrer`; no access log; while `app.state.restoring` every endpoint answers 503 `restoring`; bridge requests are not counted in `active_requests` (polling would block a restore); websockets are refused. Below the ASGI middleware, the listener's own HTTP protocol (`server.guarded_protocol`) closes a connection that has not delivered a COMPLETE request head within 10 s (`HEADER_TIMEOUT_SECONDS`, also when the head arrives a byte at a time) and holds at most 16 such unfinished connections per peer address (`MAX_UNFINISHED_PER_PEER`; the oldest is closed to make room): uvicorn only times out the gap between requests, so without this 64 sockets that say nothing would hold every slot of `limit_concurrency` and every real request would be answered 503. It stops counting at the first complete head, so the 25 s long poll is unaffected. `X-Meridian-Version` is the extension's version header (hello with it from an unpaired extension marks the browser "detected" in the app for 15 minutes).
- A `chrome-extension://` origin whose id is not in `CHROME_EXTENSION_IDS` plus `MERIDIAN_EXTENSION_IDS` can still pair but is shown as **unrecognised** in red in the approval window and the app. Safari origins carry a per-install UUID and rely on the pairing approval alone.

### 4.2 Pairing (no password, no token typing)

1. The worker `GET /v1/hello` on the candidate ports and picks the one that answers as Meridian (if more than one does, it refuses to pair and shows "ambiguous", 4.3).
2. `POST /v1/pair/start {client_name, extension_version, locale}` (Origin required; the browser kind comes from the origin, never from the body) creates a pending pairing with a random 6-digit code and answers `{pairing_id, code, expires_in: 120, poll_after_ms}`. Limits: one pending per origin (a new start supersedes), 5 starts a minute per origin (case-insensitive) and 20 overall, 8 pending in total (429 `rate_limited`).
3. The extension shows the code. Meridian shows a **native code-entry dialog** (`osascript`, values passed as separate arguments; buttons Deny and Connect, only a correct typed code connects, an empty answer is ignored, three wrong codes deny, at most 90 s) **and** the in-app request with an Approve button (Settings > Browser assistant). The first decision wins and cancels the other. A macOS notification without the code, and `open <app bundle>` when `MERIDIAN_APP_BUNDLE` is set, bring the app forward. The dialog is skipped off macOS, under test, or when `extension_pairing_dialog` is false (in-app approval always remains).
4. The worker polls `GET /v1/pair/{id}` (same Origin when sent). On approval the token is returned **once** and the pairing becomes `claimed`; plaintext lives in memory for at most 2 minutes, only `sha256(secret)` is stored. A sidecar restart expires every unfinished pairing and revokes an approved but unclaimed client. Approving a pairing retires other credentials of the same origin idle for a day.
5. Revocation (app **Disconnect**, `POST /v1/disconnect` from the extension, or a restored backup) makes the next call 401 `unauthorized` and the hello proof stop; the extension returns to `unpaired`/`revoked`.

### 4.3 Hello proof (anti port-squatting) and credential storage

`GET /v1/hello?client=<id>&nonce=<b64url 16-64 chars>` answers `{app, bridge, app_version, latest_extension_version, port, proof}`. For an active client `proof = b64url(HMAC-SHA256(key = sha256(secret) [32 raw bytes], msg = "meridian-bridge-v1|" + nonce + "|" + port))`, bound to the port that answered. Unknown clients get no proof. A client that was **revoked** gets `revoked: true` and `revoked_proof` (same key and encoding, message prefix `"meridian-bridge-v1-revoked|"`, so it can never pass for an active proof): the extension believes "revoked" only with that proof, and silence is never read as "revoked". Additive hello field `latest_extension_digest` (sha-256 tree digest of the bundled extension, taken from the `build.json` stamp the chrome build writes last; `""` when none): an extension whose own stamp differs while the version is equal is offered the app's copy (a rebuild that kept its version number). The "outdated" verdict of the extension (`shared/version.ts`) is therefore digest-based as well as version-based: when BOTH the app and the loaded build carry a digest and they differ, the extension is outdated even at an equal version number; an unknown latest version or a missing digest on either side is never "outdated" (nothing to compare). The app side (`api.py`) still compares versions only (`assets.is_older`) for the clients it has seen.

The worker proves the listener **before every credentialed request**, not once: `proveConnection` is the only producer of credentials, requests that start together share one fresh hello, a proof is never reused for a later request, and `bridgeFetch` refuses credentials it was not handed by a verified proof (a unit test pins that only `connection.ts` marks them). Hello probes carry the client id only, never the token. A well-formed hello is required to count as Meridian (`isRecognisedHello`). **Residual limits:** the proof and the request are separate connections, so a local process that takes over the port between them could receive that one request; and before the first pairing no secret exists, so the first pairing relies on exactly one program answering and on the user typing the code into the window Meridian itself opens.

The credential is stored in `storage.local` with access restricted to trusted contexts where the engine supports it (Chrome); otherwise it is AES-GCM-encrypted under a non-extractable key in the extension's own IndexedDB (`vault.ts`); failing that it lives in `storage.session` only. Safari always takes the last tier: its worker opens IndexedDB but the transactions never answer (measured with a WKWebExtension harness), so the vault would hang the pairing; the credential lives in `storage.session` and the user connects again after Safari quits.

### 4.4 Errors

Every error is `{detail, code}` with `code` one of `unauthorized origin host restoring invalid too_large rate_limited not_found expired disabled unavailable conflict internal`. The worker maps transport conditions to `ConnectionState.phase`: `ready`, `connecting`, `no_bridge`, `unpaired`, `pairing`, `revoked`, `unknown_listener`, `proof_failed`, `disabled`, `restoring`, `error` (and the panel adds `ambiguous` and `orphaned`). `unknown_listener`: a credential is stored and a listener that looks like Meridian gives no proof of either kind. Pairing with it needs `pair:start {confirmUnknown: true}`, accepted only from the options page, where the user confirms after reading why; the stored credential is then kept as a fallback slot (`meridian.connection.previous`) and restored when the app that issued it proves itself again (the credential from the unproven pairing is then dropped), dropped when that app reports it revoked, and after a month; an older fallback is never replaced by a younger one (the one issued by the longest-known app is kept), and the same fallback slot is used when the user pairs again with a different Meridian while a credential is stored. Page scripts are told `proof_failed` instead (nothing is sent, they have no way to confirm). The UI shows a localized sentence plus one action, never a raw exception.

## 5. Bridge API, server behaviour and policies

All authenticated routes use `Authorization: Bearer mrd1.<client_id>.<secret>` (`auth.current_client`); a request that carries an `Origin` must equal the one recorded at pairing; the master switch off answers 403 `disabled`. `last_seen_at` and the reported version are written at most once a minute.

| Method + path | Auth | Purpose |
|---|---|---|
| `GET /v1/hello?client&nonce` | no | identity, versions, port, proof |
| `POST /v1/pair/start` · `GET /v1/pair/{id}` · `POST /v1/pair/{id}/cancel` | no (Origin) | pairing |
| `POST /v1/disconnect` | yes | the extension's own "Disconnect": revokes its credential too |
| `GET /v1/status` | yes | profile summary, generation availability, settings (cached about 60 s; never touches the Keychain) |
| `PATCH /v1/settings` | yes | only `language`, `auto_advance`, `skip_filled`, `tab_inserts_protected`, `save_corrections`, `hint_on_ats` |
| `POST /v1/sessions` · `DELETE /v1/sessions/{sid}` | yes | open or reuse the session for a page (starts research), release it (queued drafts cancelled after a 20 s grace) |
| `POST /v1/sessions/{sid}/suggest` | yes | `SuggestRequest` to `SuggestResponse` (deterministic phase; narratives come back `GENERATING`) |
| `POST /v1/sessions/{sid}/poll` | yes | one long-poll for ONE page |
| `POST /v1/poll` | yes | ONE long-poll for every page of a browser (`{sessions:[{session_id, cursor}], wait_ms}`, at most 16 pages, wait at most 25 s) |
| `POST /v1/sessions/{sid}/regenerate` | yes | draft, redo, shorter, longer, different; also for a short field on explicit request (carries its `element`) |
| `POST /v1/sessions/{sid}/feedback` | yes | what the user finally did |
| `POST /v1/sessions/{sid}/track` | yes | `submit_clicked` / `confirmation_seen` (audit only), user-confirmed `record`, and `save` ("Save to tracker") |
| `POST /v1/sessions/{sid}/research/company` · `/research/site` · `/research/refresh` | yes | user corrections: "this is not the company", pin the official website, re-run |
| `GET /v1/documents[?session_id=]` · `GET /v1/documents/{id}/content` | yes | approved CVs (with a session, also the one written for that job); content as base64, at most 10 MiB |

### 5.1 Sessions

`POST /v1/sessions` validates and normalises `PageReport` (control characters stripped; URLs scrubbed of fragments and secret-looking parameters by `urls.scrub_url` plus `security.private_checkpoint`; credential suffixes and `/apply` stripped for identity). It returns the existing session for `(client, canonical_url)` and shares research by `(tail_key(company), job_key)`; the answer is `{session_id, protocol, knowledge_version, job, research, generation, settings, cursor}`. A session row holds only company/title/host and the user's corrections; **browsing creates no `jobs` or `applications` row**. Memory caps: 48 pages per browser and 96 overall (least recently used goes first); a page idle 6 hours is dropped. `tracked` is derived from an existing job/application matching the page.

### 5.2 Event cursor and polling

Per session an in-memory append-only list with a monotonic cursor (cap 500; a client behind the dropped events gets a `knowledge` event and rescans). Event types: `suggestion`, `research`, `knowledge`, `generation_budget`, `drafts_paused` (this page used its automatic drafts), `tracked`. The worker holds **one** `POST /v1/poll` for all assisted pages instead of one connection per page (a browser allows about 6 connections per host); content scripts still ask per page and the worker keeps their requests as in-memory waiters, serves every one from the shared request (lowest cursor, per-tab waits), pauses it while every waiting tab is hidden, and falls back to per-session polls (`POST /v1/sessions/{sid}/poll`) against a bridge that answers 404 for `/v1/poll`. The bridge authorises every listed page against the calling client (a stranger's page is `unknown`, exactly like one that never existed) and answers per page `{session_id, status: ok|expired|unknown|released|superseded, cursor, events}`; a newer poll of a page ends the older one that listed it; `wait_ms` of 250 or less answers at once and never takes a page over from a long poll. After a Meridian restart every session route answers 410 `expired` and the client reopens its session (`POST /sessions` returns the same id). A page that neither polled nor asked for 10 minutes is considered gone and its queued drafts are cancelled (a visible page polls at least every 25 s); a hidden tab loses nothing because events are cursor-based. Requests for one frame of one session are served one at a time.

### 5.3 Suggestion state and insertion tiers (authoritative: `suggest.py`)

`decide()` normalises the engine result (the result dicts are inconsistent across paths, so it never trusts a single flag) and the first match wins:

| State | Condition | Colour | `insert` |
|---|---|---|---|
| `SKIP` | password/hidden/file without a document/button/readonly/disabled/auxiliary control, or the page reports `challenge`/`mfa`/`login` | none | never |
| `NOT_APPLICABLE` | conditional question whose parent answer excludes it (or is not given) | muted | never |
| `SENSITIVE_BLANK` | `leave_blank` and no answer (personal-disclosure policy) | grey lock | never |
| `CONSENT` | no answer and (`EXPLICIT_CONFIRMATION_REQUIRED` or intent in CONSENT, GDPR, CRIMINAL_HISTORY, SECURITY_CLEARANCE, BACKGROUND_CHECK, CONFLICT_OF_INTEREST, ROLE_CONFIRMATION) | grey lock | never |
| `GENERATING` | a draft is queued or being written | blue pulse | never |
| `NEEDS_YOU` | any other no-answer, any `root_cause`, or `requires_human_input` (including `PLAIN_QUESTION_REQUIRED`, `MISSING_*`, `BAD_ENUM_MAPPING`, format errors, `LLM_*`, budget, validation, `DRAFT_OUTDATED`) | grey | never |
| `GENERATED_DRAFT` | level `GENERATED`, `quality.passed` and `ANSWER_VALIDATED` | blue | `tab` (draft dwell) |
| `VERIFIED` | verified and level in EXACT, POLICY | green | `tab` |
| `INFERRED` | level DERIVED_HIGH/DERIVED_MEDIUM, or `inferred`, or `requires_review` | amber | `tab` |

**Protected answers** (`is_protected`: never-saved fields; personal-disclosure kinds; question class in the sensitive set or `work_authorization`; a typed signature; intent in DEMOGRAPHIC, WORK_AUTHORIZATION, SPONSORSHIP, VISA, REASONABLE_ADJUSTMENTS, GDPR; or a consent-type intent that has a confirmed answer) get `insert: "explicit"` (Enter or click). `settings.tab_inserts_protected` moves them to `tab` **only when VERIFIED** (an INFERRED protected answer stays `explicit`). A **typed signature** is always `explicit`, whatever the switch says, as is a draft whose independent review was switched off. Never insertable at all: no answer, `leave_blank`, any root cause, CONSENT, `applies is not True`, an unvalidated or outdated draft. A non-empty field whose value differs is never overwritten by Tab (the chip offers Replace). Stale suggestions (`knowledge_version` or element digest changed) are discarded client side.

`why.code`: `profile_fact derived policy saved_answer generated consent sensitive_blank not_applicable missing_fact format_error no_matching_option setup_required budget model_error validation_failed cv_attach`. `needs.code`: `setup_required budget model_error validation_failed missing_candidate_fact missing_fact format_error no_matching_option draft_outdated`. A draft's `based_on` carries `job_description`, `company_pages`, `research_state`, `knowledge_version`, `research_bundle` and, when out of date, `stale` with `research_changed` (still offered), or the state becomes `NEEDS_YOU/draft_outdated` for `facts_changed` and `company_rejected`.

### 5.4 Resolver pipeline and engine fidelity

`resolver.py` reproduces `automation/runner.py` `resolve_raw` + `resolve` for one element (manual corrections, disclosure policy, `CandidateEngine.answer(..., audit=False)`, `confirmed_fallback`, profile fallback for unknown `OTHER`, location completion, option second stage with `OptionSelector` deterministic mode, operation/format mapping). Dial-code questions go through `automation/dialcode.py` for both callers. `question = adapter.question(element)` and `question_id` are computed server side; client `question_id`, `_related_answers`, `_condition` and similar are dropped.

**Fail-closed answer gate.** A factual answer (right to work, sponsorship, notice, education, language level, location and the like) is given only when the whole question is accounted for by a canonical form of its family (`intelligence/plain.py`); extra words (an employer note, a qualifier, a second predicate, a follow-up) are residue and the question is `PLAIN_QUESTION_REQUIRED`, i.e. left to the user. Readers around it: `jurisdiction.py` (which place a question names; an answer true for the UK is not given for another jurisdiction), `languages.py` (levels are an order), `academic.py` (level, status, subject, institution, relation of dates), `legal.py`, `salary.py`. English and Spanish only. Answers depend on the job's country, so `knowledge_version` also moves when the country becomes known or changes. Spain and EU/EEA right-to-work, sponsorship and expected-salary answers come from their own Application profile fields (`*_es`, `*_eu`), never inferred from the UK ones.

The job dict given to the engine is `{job_id, application_id, company_id, title, company, location, country, countries, url, application_url, ats, source, description, skills, context_sources, company_context, resume_version_id, id}`. `country` is the English name the engine uses and is `''` when the page names none or several. `source` is how Meridian found the job (the stored discovery source) and is never "the assistant". A page may send `location_hint`; each element a `position` (document order within one request); partial `refresh`/`focus` requests are merged into what the frame knows. Engine patches (each tested): `CandidateEngine.answer(..., generate=False)` returns `ASYNC_GENERATION_REQUIRED` without calling the model; password/hidden types never reach the LLM; a typed `BudgetExceededError` with `code="AI_BUDGET_EXCEEDED"`; extension requests use `audit=False`.

### 5.5 Generation (long-form drafts, `generation.py`)

- Automatic drafting only for narrative intents on textarea/long-text fields, when `generation_available` (provider configured, cap > 0, prices known for cloud, last connection test succeeded), `background_drafts` is on and the question is not sensitive, legal, salary or personal-disclosure. A short field is drafted only on explicit request (`regenerate` with `element`, "Draft with AI").
- One task per content key `sha256(question, constraints, knowledge version, job context digest)` (coalescing). It reuses `app.state.text_ai.answer_llm` (its semaphore and budget accounting). Per page, drafts run sequentially in document order so earlier validated drafts feed `_related_answers`. Caps: 6 queued/running per page (explicit requests exempt) and 12 overall.
- **Automatic-draft ledger** (`draft_limits.py`): at most 8 drafts started on their own per page, 40 per browser and 80 overall in a rolling hour; counters and reference numbers in a question are read out of the cache key so a page that rewords its label cannot mint endless paid drafts. A page that hits its limit gets one `drafts_paused` event. Drafts the user asks for are never counted.
- Before drafting, wait up to 8 s for research and tag `based_on.research_state`. Research is frozen per session (`bundle_hash`): late research emits a `research` event and offers a rewrite, never a silent regeneration.
- Only queued work is cancelled on release/navigation; an in-flight provider call is never cancelled. The first `LLM_CONFIGURATION_REQUIRED`/`AI_BUDGET_EXCEEDED` stops the queue and sends `generation_budget`. Per-page spend cap `draft_budget_usd` (default 1.0 USD when unset; there is no "unlimited").
- Every draft carries its **basis** (digests of the facts it cites, the research bundle, the company): a draft whose cited fact was changed or un-verified, or whose company the user rejected, is withdrawn as `draft_outdated`; one based on older research is still offered, marked stale. Every draft is `requires_review`, validated by the deterministic and independent review stages, and the extension labels it "AI draft".

### 5.6 Research (company and role, `research.py`)

`ResearchManager` is LLM-free, deterministic and bounded (at most 14 requests cold, 8 s per request, 25 s soft / 40 s hard deadline, concurrency 2, one run per `(tail_key(company), job_key)`, run per page never per field). Stages: identify (company below 0.5 confidence parks the run in `needs_input`), role (JSON-LD, observed employer text, a stored job, a single-posting vendor API, or the fetched posting, accepted only when the title matches), site (official website verified by a homepage fetch before any page becomes model context; vendor, aggregator, registry and encyclopaedia domains never qualify), pages (about, mission, values, engineering, careers on the verified domain only, fixed GET-safe paths, robots.txt honoured, never LinkedIn/Indeed/Glassdoor, no browser fallback). Optional last-resort public lookup (`web_lookup` setting): only the company name, as `<name> official website`, to the no-JavaScript DuckDuckGo page, at most one per 3 s, 90-day cache; off under `MERIDIAN_TEST`.

All network I/O goes through `fetch_public`: http on 80 / https on 443 only, local and internal names refused, **every** resolved address must be public, connection to the resolved address with the original Host/SNI, redirects re-validated (at most 5), no cookies, credentials or proxies, text content types only, wall-clock, size and redirect bounds; page parsing runs in a killable worker process (`markup.py`, `parse_worker.py`; in the frozen sidecar `--meridian-parse`): at most 2 at once, niced, under a hard CPU limit (`RLIMIT_CPU`) and a wall limit, with a tag/depth budget; documents made of constructs that `html.parser` reads in quadratic time (`<![CDATA[` or unterminated `<` in bulk) are refused before any parse; a homepage is read from at most 300 KB; each page is one parse, and a host whose page could not be read safely is not parsed again in that run; the worker has no tokens or settings in its environment. A site named by a page must be plain https. Caches under `data_dir/cache/extension-research/` hold public data only (pages 7 d, role 3 d, robots 24 h, web lookup 90 d, failures 15 min). Output feeds `job['description']`, `context_sources` and `company_context` (verified-site pages only, at most 8), sanitised and tagged `trusted_as_instructions: false`. The user's corrections (`/research/company`, `/research/site`, `/research/refresh`) each start a new run, are kept on the session so a reload or restart reuses them, a new company drops the website given for the rejected one, and a website is saved for the company (`extension_company_sites`) only after a run actually read it. `ResearchSnapshot` is in `protocol.ts`.

### 5.7 Feedback and persistence

`POST /feedback` outcomes: `accepted` (audit only), `edited`/`manual` (the user typed or changed the value: saved like a direct edit in Auto Apply, `remember_answer(..., direct_edit=True)` with the same supersede/application-answer bookkeeping), `rejected`/`skipped` (audit only). `remember` is forced to `application` for narrative, company-specific, protected, legal and salary questions; `reuse` is honoured only for profile-mappable factual questions and promotes the value through `write_profile`. Never saved, whatever the client sends: password, hidden, file, one-time code, card, bank, ID number, security answer, another person's details under a bare label, anything under a label that names two questions of the page; every refusal returns a precise `code`. The extension sends `final_value` only here. The audit line of a saved correction names the answer it describes (the label "Inserted by assistant"); lines hold no values.

Lazy rows: the first persisting event (a saved answer or an explicit track action) calls `ensure_extension_application`: `ingest_job(source='extension')` with the scrubbed URL, a bare `applications` row (`status='PREPARING'`, event origin `extension`, no human task, no `prepare_application`) and a notification receipt so no "new strong match" fires. A sent application is never reopened. Auto Apply's prepare later adopts such a bare row (`assistant_bare_row`) instead of treating it as a duplicate.

### 5.8 Tracking

`submit_clicked` and `confirmation_seen` only add audit events (`noted`). Only user-confirmed `record` calls `tracking.record_application(..., origin='extension')` (the date may not be in the future); `save` ("Save to tracker") writes the bare PREPARING application and never says it was sent. On an already sent application both answer `already_recorded` and change nothing. The extension offers "Record this application?" only after it saw its own final-submit click classified by wording (English and Spanish) **and** confirmation text in this or the next document (held by the worker per tab so it survives a cross-origin thank-you page), or when the user chooses "I applied: record this".

### 5.9 Documents (`documents_pick.py`)

Only **approved** versions of the user's own documents. A CV may be offered if it is a base CV, the CV written for this very posting, or one attached to this application; never the newest CV of another job. Default order: attached to this application, written for this posting, the CV chosen in Settings, the newest base CV; other eligible CVs are returned as `alternatives` for a click-only "Use another CV". A cover letter only if written for this posting (or attached) and approved. When nothing is offered the field says why (not approved yet, none imported, not a document Meridian attaches). `ATTACH_FILE` suggestions are `VERIFIED`, `tab`; the content endpoint refuses unapproved versions and path escapes, at most 10 MiB.

### 5.10 Settings keys (`core.DEFAULT_SETTINGS`)

`extension_enabled`, `extension_language`, `extension_auto_advance`, `extension_skip_filled`, `extension_tab_inserts_protected`, `extension_save_corrections`, `extension_research_enabled`, `extension_background_drafts`, `extension_web_lookup`, `extension_hint_on_ats`, `extension_pairing_dialog`, `extension_draft_budget_usd`, `extension_always_on_sites`, `extension_research_concurrency`, `extension_company_sites` (pins). The mapping to `ExtensionSettings` is `api.translate_settings`. No credential lives in `settings`.

## 6. Desktop app integration (`/api/extension/*`, app token)

`GET /extension` returns `ExtensionStatus` (shape in `api.build_status` and `frontend/src/types.ts`): bridge state/port/candidates/conflict process, per-browser install info, clients with counters and last error, the pending pairing with its code, settings and drafts spend. `POST /extension/pairings/{id}/approve|deny`; `DELETE /extension/clients/{id}`; `PATCH /extension/settings`; `POST /extension/install/{chrome|safari}` with `{action: install|prepare|reveal|open_browser_page|copy_path, update?}` (OS actions in the backend: `open -R`, `open -b`, `pbcopy`; under test they are only recorded); `GET /extension/activity`; `POST /extension/bridge/restart`; `POST /extension/jobs/{job_id}/open` (opens the exact application URL in the paired browser and primes research). `GET /tracker` rows and the application detail gain `assistant`; answers and Profile memories gain `origin: "extension"`; onboarding gains the ninth evidence step. None of these returns tokens or hashes. State precedence: `disabled > bridge_error > pairing > outdated > ready > detected > not_installed`; `detected` means an unpaired extension said hello in the last 15 minutes.

The Chrome folder is `~/Library/Application Support/Meridian/extensions/chrome`, installed by the sidecar at start from the build embedded in the signed bundle (a frozen sidecar reads only its own bundle; a development run reads `MERIDIAN_EXTENSION_SRC`, then `<repo>/extension/dist`), accepted only if its manifest key derives a pinned id, replaced by two renames with the digest marker outside the folder and written last. `Info.plist` registers `meridian://`; `meridian://open` only raises the window and nothing in the URL is acted on.

## 7. The extension

### 7.1 Build and manifests

`extension/` is an npm package (esbuild, `tsc --noEmit`, `@types/chrome`; no runtime dependency). `build.mjs` produces `dist/<variant>/` for `chrome`, `safari` and the test variants `e2e`, `e2e-closed`, `e2e-top` (loopback host permissions, `__E2E__` hooks, open shadow root). IIFE bundles: `background.js`, `content.js`, `hint.js`, `options.js`. **The scanner is `observation.js` bundled verbatim** (called with `{widgets: true}` for the extra widget patterns), so descriptors are what the engine already understands. `__E2E__` is stripped from production and the production check fails the build if any remains.

Manifest (MV3): `name` "Meridian Assistant", `version` from `src-tauri/tauri.conf.json`, `action` without a popup, `background.service_worker`, `permissions: ["activeTab","scripting","storage","alarms"]`, `host_permissions`: `http://127.0.0.1/*` plus the curated vendor hosts of **`extension/ats-hosts.json`** (the single list, also the hint script's matches), `optional_host_permissions: ["https://*/*","http://*/*"]`, `options_ui` (opens in a tab), `_locales` en + es. Chrome variant: `key`, `minimum_chrome_version` 120 and `commands._execute_action` (default Ctrl+Shift+Y, Command+Shift+Y on a Mac). Safari variant: no `key`, no `commands` (Safari has no extension shortcuts), `browser_specific_settings.safari.strict_min_version` 18.0; no `webNavigation`, `sidePanel`, `downloads`, `idle`, `notifications`, `offscreen` or `runtime.getContexts` anywhere.

### 7.2 Service worker (stateless)

Registers every listener synchronously at top level; every handler re-reads storage. It owns the bridge client (port discovery, hello + proof per request, bearer calls, retry/backoff, error to `ConnectionState`), pairing (alarm-driven watcher), the single multiplexed poll (5.2), per-tab activation state in `storage.session`, badges (**ON** active, **!** attention, a green dot for the ATS hint), the frame relay, `meridian://open`, the update check and `onInstalled` (opens the welcome page). **Toolbar semantics:** `action.onClicked` on a tab where the assistant is not running injects the content scripts into all accessible frames and starts it; on a tab where it already runs the click is a **toggle** decided by the content script, which owns the panel (a visible panel switches the assistant off, a hidden or collapsed one shows it) and reports its on/off state back. Pages the browser forbids (browser pages, stores, PDFs) open the options page with an explanation. A tab that stays on the same site, a curated host or an always-on site keeps the assistant across page loads (6 hours); a submit awaiting its confirmation follows the tab. Always-on sites are decided at page load from the list in Meridian (refreshed when older than 30 s) and need the browser's site permission. The worker never logs tokens or page text.

### 7.3 Content script modules

- Idempotent (`globalThis.__meridianAssist`); every runtime call is guarded, an extension reload orphans the script and the UI says "Reload this page".
- `page-report.ts` (top frame): JSON-LD `JobPosting` texts, `og:*`, canonical, `h1`, company hint, description text from employer sections only, location hint, up to 80 links; never form values or full HTML.
- `scan.ts` (+ `visibility.ts`, `sensitive.ts`): runs the observer, prunes to `ElementIn` (text controls send `has_value`, never the value), groups radio/checkbox groups, computes an element digest, observes mutations (300 ms debounce) and SPA step changes, open shadow roots and every frame. An **assist field** is something a person can see and click (ancestor opacity, clipping, ghost text, a hit test must reach the control, its label or proxy). The hit test has a second, **paint-cover pass** (SEC-01): it is repeated with `pointer-events: auto !important` forced, through a constructed stylesheet that exists for the length of the pass only, on every tree involved (the document, the trees of the probes and their hosts, the page's open shadow roots), so a cover that a pointer would fall through (`pointer-events: none` on a div, an `::after`, an image, a veil in the top layer) still counts when it paints (opaque background, image, border) above the control, its label or its proxy; a control with its own `pointer-events: none` is rejected by the natural pass. A control that passed the pass at a scan skips it at the scans of the next 10 s (`PAINT_TTL_MS`), and a page of more than 8000 nodes skips the scan-time pass altogether (`PAINT_MAX_NODES`); the write-time checks below are never skipped. One-time codes, card, bank and ID numbers, security answers and passwords are never assist fields and nothing about them is sent.
- `fill.ts`: the only code that writes to the page: native setters plus `input`/`change` for text, `beforeinput`/`insertText` for contenteditable, `click()` for radios and checkboxes (never `.checked =`), select by option label, custom combobox open/search/click/verify (else leave untouched and report "check this one"), file via `DataTransfer`, national-only phone inputs strip the dial code. Every fill is read back; a failed verification reverts. Three gates stand before a write. **Chip time:** the chip of a field is drawn only while a fresh visibility check of that field passes (`Controller.chipVisible`, remembered for a moment so that a render per keystroke does not cost a style recalculation each; a popup the page calls a dialog or listbox over the field does not remove the chip, only the write). **Write time:** a fresh visibility check with the strong paint pass (`Scanner.stillVisible`, no memory of the last scan) runs right before every write; for a file and for every write the person is looking at (Tab, Enter, Replace, a click) it is strict, i.e. the control must be on screen and hit-tested now (a control judged off screen by its styles only does not pass); the field above's own popup over a control the person has already seen is tolerated, any other cover refuses the write. **On top:** the assistant's own chip and panel, which are what the person decides on, must be drawn above everything the page paints (`UiHandle.ensureOnTop`: a popover or dialog of the page opened after them is answered by putting the host back on top; if that cannot be done nothing is written and the field reports "hidden"). Plain text, never HTML. **Corrections need a trusted origin (LOW-1):** a value counts as the person's own, and may be offered to "Save this answer in Meridian?", only when a trusted key, pointer, paste, drop or composition event reached the same field just before the `input` (`Controller.onOrigin`); a script's `document.execCommand("insertText")` raises trusted `input` events, so the event alone proves nothing, and a value written by the page or by a script is never stored or sent as a correction. Undo window 8 s, batch undo 10 s.
- `keyboard.ts`, `tabstops.ts`, `popups.ts`, `controller.ts`, `state.ts`, `session.ts`, `frames.ts`, `submit-watch.ts`: controller, traversal and the learning loop (7.4). `ui/*` (chip, panel, notices): a **closed** shadow root host (`all: initial`, top layer), every string from `i18n.ts` (en/es), text nodes only, `isTrusted` on every click, chip controls `tabindex=-1`, panel at the end of the document.

### 7.4 Interaction model

Assist mode is on after the toolbar click until deactivated (panel menu, toolbar click again, navigation away). **Focus: chip.** On focus/click of an assist field the chip appears next to it (placed beside, below or above, away from the next fields and the field's options; it folds to a one-line pill when the form leaves no room; a quiet one-liner for `NEEDS_YOU`; nothing for `SKIP`).

Keys (capture phase on `window`, `isTrusted` only, never during IME composition; the decision to prevent a native Tab is **synchronous**, from the prefetched cache, never after an `await`):

| Key | Behaviour |
|---|---|
| `Tab` that inserts (and Tab while an asynchronous fill runs) | prevent default and insert if eligible, then focus the next assist field (honours `skip_filled` and `auto_advance`), scrolled into view near an edge |
| `Tab` / `Shift+Tab` with nothing to insert | bubble phase, after the page's own handlers (a widget that owns Tab keeps it). Own traversal reaches checkboxes, radios and selects (Safari's native Tab skips them) but returns the key to the browser when a link, a button or a plain control lies in between, a native date input has segments, a code editor indents, a page popup next to the field is open (the answer is still inserted, the Tab is left to the page), or nothing follows; the end of the form is left to the browser |
| `Enter` | only when the chip shows `insert: "explicit"` and the value is insertable; never hijacks submission |
| `Esc` | hides the chip for this field until refocus; from inside the panel it returns focus to the form |
| `Alt/Option+R`, `Alt/Option+Z` | rewrite a draft, undo the last insertion (the letter the key prints, any layout) |
| `Alt/Option+Shift+Z` | undo the whole last Fill verified (while Undo all shows, 10 s) |
| `Alt/Option+Shift+M` | switch into the panel, and back; relayed to the top frame from a frame |
| `Control+Option+1..7` (Mac), `Alt+Shift+1..7` (elsewhere) | the numbered chip buttons (Replace, Shorter, Longer, Different angle, Show more, sources); chords that type no character, swallowed only when they performed an action |

Keys pause while a UI text input has focus, in IME composition, when a native popup is open, and with the panel switch **Use Tab to insert & move on** off.

**Eligibility to insert on Tab (`state.ts`):** suggestion ready, `state` in VERIFIED/INFERRED/GENERATED_DRAFT, `insert == "tab"` (or `"explicit"` with the protected switch where 5.3 allows it), field empty or equal, element digest and `knowledge_version` unchanged, not dismissed, no duplicate-application block; a `GENERATED_DRAFT` must have been **visible 500 ms** (`DRAFT_DWELL_MS`) so holding Tab never inserts unseen text; `GENERATING` makes Tab advance. A field with different text is never overwritten (Replace offered).

**Insertion feedback:** outline and mark by trust tone; INFERRED and drafts keep a marker until the user touches the field; 8 s Undo. **Fill verified:** lists the fields and values first, writes only after "Fill now"; includes only VERIFIED, `tab`, non-protected, non-legal, non-consent fields that are currently visible. **Learning loop:** a value the user's own gesture produced and that differs from the suggestion triggers the save prompt per `save_corrections` (`ask`, `always`, `never`); reuse only when `flags.reusable`; never for never-saved classes. **Cross-frame:** each frame scans and renders its own chip; the panel (top frame) receives field digests through the worker relay; Tab and Shift+Tab hand focus across frame boundaries. **Duplicate guard:** an application already recorded as sent (rejected, withdrawn, interview and offer included) shows a banner and nothing is inserted until "Continue anyway".

### 7.5 Panel and calm states (copy in `i18n.ts`, EN + ES)

Panel: bottom right, dockable left/right, collapsible to a pill: company and role, connection dot, research strip with sources, "Not this company?", "Add the company website" and "Research again", progress by section, "Fill verified (n)", drafts on their way, the keys toggle, "Save to tracker", "I applied: record this", start-automatically and hide-hint switches, footer (spend this month, Settings, Help); a first-use card and an end-of-form "Before you submit" reminder. Calm states, each with one primary action: no form yet; `no_bridge` (Open Meridian, auto-retry); `unpaired` (Connect); `pairing` (the code, Cancel; says Meridian itself must open a window); `proof_failed`; ambiguous listeners (nothing is sent, quit the extra one); `revoked`; `disabled`; `restoring`; empty profile; no writing model; budget reached; cross-origin frame not covered (Allow all sites); update ready; orphaned (Reload page). Visual identity: forest `#173F36`, ivory `#F3F3EA`, mint `#A8E4CE`, sand `#D6B782`, trust tones green/amber/blue, at least 4.5:1 contrast, system font, never inherits page CSS.

### 7.6 Options and welcome page

Connection (status, connect/disconnect, Open Meridian), behaviour switches and language, permissions (**Allow on all websites**, per-site allow), site rules (always on, hide hint), update status, privacy summary, help (keys and colours), the CV the assistant attaches, diagnostics (ports, versions, last error: no tokens, with the first-pairing and duplicate-copy notes). Opens after install.

### 7.7 Safari differences

Same sources, `safari` manifest, built into a sandboxed `.appex` by `extension/safari/` and `scripts/build_safari_assistant.sh` (`swiftc` + `codesign`, no Xcode project), embedded in `Meridian.app/Contents/PlugIns` by `build_signed_app.py` when the Swift toolchain exists, signed with the Apple Development identity and sandbox-only entitlements. User steps: launch Meridian from /Applications, Safari > Settings > Extensions, website access (recommend Always Allow on Every Website), possibly Develop > Allow Unsigned Extensions. Meridian cannot read Safari's enablement or website access (`pluginkit` only says whether it is registered). The credential is sealed in IndexedDB where `storage.local` access cannot be restricted. No toolbar shortcut. **Not verified in real Safari by any test** (WebKit in Playwright stands in).

## 8. Privacy and security checklist (reviewed before release)

- Content scripts never fetch; the credential never leaves the worker and storage; a fresh hello proof precedes every credentialed request.
- No typed value leaves the page except `final_value` on an explicit save; `has_value` for text controls; sensitive classes never scanned.
- Page and employer text are untrusted data in the UI (text nodes) and in the engine.
- Pairing: a code the user types into a window Meridian opens, no default approve, rate limits, expiry, origin shown (unrecognised flagged), revocation, backups purge the extension tables, tokens redacted from logs (`mrd1.*`).
- Research: public GET by Meridian only, pinned addresses, redirects re-checked, fixed paths, robots, no cookies, company name only for the optional lookup, verification before use.
- The shipped extension holds no secrets (public key in the manifest only). The private key exists only in `.local-data/extension/` and must be backed up offline (see 3).
- Residual limits of the content layer's cover detection are listed under "Known limits" in `docs/EXTENSION.md` (IntersectionObserver v2 not used, `!important` and `@layer` covers, off-screen non-file bulk fill, pages over 8000 nodes).

## 9. Testing and acceptance

Commands and layout are in [EXTENSION.md](EXTENSION.md) and [../TESTING.md](../TESTING.md); evidence and its limits in [VERIFICATION.md](VERIFICATION.md).

- **Backend** (`backend/tests/test_extension_*.py`, `test_build_extension.py`): pairing, guard, auth, sessions and events, suggest tiers, resolver parity, generation, research (offline, SSRF refusals, injection), feedback and refusals, documents, backup exclusion, log hygiene, shutdown, tracker summary, prepare-after-assist. The engine's `test_engine_plain.py`, `test_engine_metamorphic.py` (held-out, about 3,600 cases) and `test_universal_candidate_semantics.py` guard the answer gate.
- **Extension:** `tsc --noEmit`; production build checks; Node unit tests; content-script rig (Playwright, real keyboard, Chromium and WebKit); panel UI rig (English/Spanish, themes, widths); real-widget matrix and its helper tests; a real-browser end-to-end suite (bundled Chromium, plus branded Chrome through CDP `Extensions.loadUnpacked`) against the real backend with a fake loopback model and fixture pages, including keyboard-only journeys, a production-build smoke journey with the closed shadow root, and a metamorphic sample through the real page; a Chromium smoke test against an independent fake bridge.
- **Frontend:** `typecheck`, `lint`, `frontend/tests/extension_e2e.py` (mocked) and `--live` (real backend and bridge).
- **Safari:** build, `codesign --verify --deep --strict`, `pluginkit` and SafariServices visibility from a staged throwaway copy, WebKit logic tests. Real Safari is manual and unverified here.

Release gate: `make release-gate` (every suite strict) and the adversarial review passes (security and privacy, engine-policy fidelity, keyboard and UX, packaging) with every confirmed finding fixed or listed under "Known limits" in `docs/EXTENSION.md`.

## 10. Decisions that resolved conflicting recommendations

1. Separate fixed-port bridge, not extra origins on the main API (the main API's port and token change every launch).
2. Source in `extension/src` with esbuild; `extension/dist/**` is generated (git-ignored); the sidecar embeds `extension/dist/chrome` (`--add-data`), the backend installs it to the stable data-dir folder.
3. Activation by toolbar click without a popup (`activeTab` + `scripting`); curated vendor hosts from `ats-hosts.json` as static host permissions; broad access an *optional* permission, needed only for cross-origin frames on other sites.
4. Scanner is `observation.js` verbatim; the resolver replicates, not refactors, `runner.py`, with a parity test.
5. Own Tab traversal in assist mode (consistent across Chrome and Safari), returning the key to the browser wherever the browser has a better claim, with an escape hatch (pause Tab).
6. Safari: `swiftc` + `codesign` appex embedded in `Contents/PlugIns`; the converter's Xcode project is not used (it does not build as emitted by Xcode 27).
7. The desktop UI stays English; extension strings are English and Spanish; the Spanish quick start has a section.
8. This repository's CONTRIBUTING.md forbids co-author trailers in commits; no commit is made without the user's request.
9. The proof is made before every credentialed request, accepting the cost of one extra loopback round trip, instead of caching a verification.
10. Failing closed beats answering: a question the plain-question gate cannot fully account for is the user's, even when a keyword matches a stored fact.
