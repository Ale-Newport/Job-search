# Internal implementation contract

All JSON APIs live under `/api`, require bearer auth (managed by desktop), and return objects or arrays directly. Lists return `{items: [], total: n}`. Errors use FastAPI `detail`. IDs are UUID strings. UTC dates are ISO strings. Snake_case fields.

Core owns `backend/jobagent/{db,core,matching,discovery,documents}.py`, `migrations/`, core tests. Root owns `main.py`, config/security/mail/backup/automation integration and desktop packaging. Browser worker owns `automation/` and browser tests. Frontend owns `frontend/`.

Database interface: `Database(path)` initializes migrations; `.query(sql, params=()) -> list[dict]`, `.one(sql, params=()) -> dict|None`, `.execute(sql, params=())` commits and returns lastrowid; `.transaction()` context yields sqlite3 connection, row_factory sqlite3.Row. `.event(application_id, status, message, origin='manual')` append and update in transaction. JSON payload columns stored as JSON text. `get_db(request)` returns request.app.state.db. Core exports `router` without prefix; main includes prefix `/api`. App state also has `data_dir: Path`.

Core routes:
- GET /dashboard: stats, recent_events, deadlines, source_health
- GET/POST /facts; PATCH/DELETE /facts/{id}. fields category,key,value,source,verification_status,locked,notes. GET/POST /relationships
- POST /documents/import multipart file; returns document/version/proposed_facts. GET /documents; GET /documents/{id}/versions; GET /document-versions/{id}/download; POST /documents/generate {job_id,kind:'cv'|'cover_letter',fact_ids,approved:false} previews text/diff; approved true persists PDF.
- GET/POST /jobs; POST /jobs/ingest {url}; GET/PATCH /jobs/{id}; POST /jobs/{id}/prepare {mode,document_version_id?}; POST /jobs/{id}/feedback {interested,reason}; GET /jobs/{id}/match. Job fields id,title,company,location,url,application_url,description,source,ats,match_score,status,skills, salary_min/max,currency,remote,posted_at,created_at,match_details.
- GET/POST /sources; PATCH/DELETE /sources/{id}; POST /sources/run {source_id?}; source fields id,name,kind,url,enabled,config,poll_minutes,last_checked,last_error,jobs_found.
- GET/POST /companies; PATCH/DELETE /companies/{id}; fields name,careers_url,locations,preferred_roles,priority,notes,poll_minutes,auto_apply,salary_preference,blacklist_keywords.
- GET/POST /search-profiles; PATCH/DELETE /search-profiles/{id}; fields name,config (roles,keywords,negative_keywords,locations,remote,minimum_salary,experience_levels,companies,excluded_companies,technologies,mode,min_match,stretch_factor).
- GET /applications; GET/PATCH /applications/{id} (status,notes); detail includes job,events,answers,documents,emails,runs,tasks. POST /applications/{id}/answers {question,answer,fact_ids?,verified:true}.
- GET /activity; GET /analytics; GET /search?q=...; GET /human-tasks; POST /human-tasks/{id}/resolve {answer,save:true}.
- GET/PATCH /settings flat object (arbitrary validated settings managed core)

Root routes:
- GET /health; GET /integrations; PUT /integrations/{provider} {config,secret?}; POST /integrations/{provider}/connect; GET /oauth/callback; POST /email/sync; GET /emails; POST /emails/import multipart EML; POST /emails/{id}/link {application_id}; GET /emails/{id}.
- POST /automation/pause; POST /automation/resume; GET /automation; POST /applications/{id}/apply; POST /applications/{id}/approve; POST /applications/{id}/takeover; POST /applications/{id}/resume; GET /automation/runs/{id}; POST /browser/open {url}; GET /browser/status
- POST /ai/test {engine}; POST /ai/benchmark {engine}; POST /ai/draft {question,job_id,fact_ids}
- POST /backups/export {password}; POST /backups/restore multipart file/password; GET /backups/{name}/download

Frontend uses `invoke('connection_info') -> {base_url, token}` in Tauri; in browser dev base URL `/api` via Vite proxy and token `meridian-development-token` only for explicitly enabled dev service. Tauri uses bundled frontend without an external server.

## Browser assistant

Two separate HTTP surfaces. The shapes are code: `backend/jobagent/extension/api.py` (desktop side), `backend/jobagent/extension/schemas.py` and its mirror `extension/src/shared/protocol.ts` (bridge). Behaviour and limits: [EXTENSION_DESIGN.md](EXTENSION_DESIGN.md).

### Desktop side: `/api/extension/*`

Normal `/api` rules: bearer app token, FastAPI `detail` errors (bridge-style `BridgeError` here also carries a `code`). None of these returns a token, a token hash or a pairing secret; a pairing code is returned only for an open pairing.

- `GET /extension`: `ExtensionStatus`: `state` (`disabled > bridge_error > pairing > outdated > ready > detected > not_installed`), `server_time`, `app_version`, `bridge` (`state` `listening|port_in_use|error|stopped`, `port`, `candidate_ports`, `conflict_process`, `message`), `browsers.{chrome,safari}` (`browser_installed`, `extension_path`, `bundled_version`, `bundled`, `state` `connected|outdated|detected|not_installed`, `seen_unpaired_at`, `seen_version`; Safari also `needs_unsigned_toggle`), `clients[]` (`id`, `browser`, `label`, `version`, `paired_at`, `last_seen_at`, `last_error`, `outdated`, `stats {pages, suggestions, insertions}`), `pending_pairing` (`id`, `browser`, `code`, `extension_version`, `requested_at`, `expires_at`, `recognized`) or null, `settings` (`ExtensionSettings`) and `drafts` (writing-model availability, month, spend, limits).
- `POST /extension/pairings/{id}/approve`, `POST /extension/pairings/{id}/deny`: decide an open pairing (409 `conflict` if already answered, 410 `expired`); answer with a fresh `ExtensionStatus`.
- `DELETE /extension/clients/{id}`: revoke a paired browser.
- `PATCH /extension/settings`: any of `enabled`, `language` (`auto|en|es`), `auto_advance`, `skip_filled`, `tab_inserts_protected`, `save_corrections` (`ask|always|never`), `background_research`, `background_drafts`, `web_lookup`, `hint_on_ats`, `draft_budget_usd` (null or 0 to 1000; null means the 1 USD default), `always_on_sites` (at most 100 hostnames). Stored under the `extension_*` keys of the settings table.
- `POST /extension/install/{chrome|safari}` `{action: install|prepare|reveal|open_browser_page|copy_path, update?: bool}`: Chrome `install` prepares the folder (`~/Library/Application Support/Meridian/extensions/chrome`), copies its path, reveals it in Finder and opens `chrome://extensions` (`update: true` shortens it to refresh plus open); Safari `install` checks the extension is bundled and opens Safari. OS actions run in the backend (`open`, `pbcopy`).
- `GET /extension/activity?limit&offset`: `{items, total}` timeline of pairings and assisted pages (never values).
- `POST /extension/bridge/restart`: stop and start the listener again.
- `POST /extension/jobs/{job_id}/open`: open the exact application URL in the paired browser and start the research (409 when no browser is paired).
- Added to existing responses: tracker rows (`GET /tracker`) and the application detail carry `assistant {browser, fields_inserted, last_used_at}` or null; application answers and Profile memory rows carry `origin: "extension"` when the last save came from the assistant; the onboarding steps gain `extension`.

### Bridge side: `/v1/*` on `127.0.0.1:47615-47624`

Not part of `/api`. Own listener, own credential (`Authorization: Bearer mrd1.<client_id>.<secret>`, bound to the pairing `Origin`), errors `{detail, code}` with `code` one of `unauthorized origin host restoring invalid too_large rate_limited not_found expired disabled unavailable conflict internal`. Requests must have `Host` `127.0.0.1` or `localhost`, an extension `Origin` (required unless the request is a GET), JSON bodies of at most 1 MiB. The extension sends `X-Meridian-Version`; the bridge adds `Cache-Control: no-store`. Protocol version 1.

| Method and path | Auth | Purpose |
|---|---|---|
| `GET /v1/hello?client&nonce` | no | `{app: "Meridian", bridge, app_version, latest_extension_version, port, proof}`; `proof` only for an active client |
| `POST /v1/pair/start` `{client_name, extension_version, locale}` | Origin | pending pairing: `{pairing_id, code, expires_in, poll_after_ms}` (429 `rate_limited`) |
| `GET /v1/pair/{id}` | same Origin | `{state: pending|approved|denied|expired|claimed, token?, client_id?}`; the token is delivered once |
| `POST /v1/pair/{id}/cancel` | same Origin | cancel a pending pairing |
| `POST /v1/disconnect` | yes | revoke the caller's own credential |
| `GET /v1/status` | yes | profile summary, writing availability, settings; cached about 60 s |
| `PATCH /v1/settings` | yes | only `language`, `auto_advance`, `skip_filled`, `tab_inserts_protected`, `save_corrections`, `hint_on_ats` |
| `POST /v1/sessions` | yes | open or reuse the session of a page (`PageReport`); starts research; returns `{session_id, protocol, knowledge_version, job, research, generation, settings, cursor}` |
| `DELETE /v1/sessions/{sid}` | yes | release it (queued drafts are cancelled after a 20 s grace) |
| `POST /v1/sessions/{sid}/suggest` | yes | `SuggestRequest` (scan, refresh or focus of one frame) to `SuggestResponse {knowledge_version, results[], skipped[], cursor}` |
| `POST /v1/sessions/{sid}/poll` `{cursor, wait_ms<=25000}` | yes | long poll for one page |
| `POST /v1/poll` `{sessions: [{session_id, cursor}], wait_ms}` | yes | ONE long poll for up to 16 pages of a browser; per-page `status` `ok|expired|unknown|released|superseded`, `cursor`, `events`; a `wait_ms` of 250 or less answers at once |
| `POST /v1/sessions/{sid}/regenerate` | yes | draft, redo, shorter, longer or different (`instruction`), also for a short field when the request carries its `element` |
| `POST /v1/sessions/{sid}/feedback` | yes | what the user did (`accepted|edited|manual|rejected|skipped`); `final_value` only on an explicit save; refusals carry a precise `code` |
| `POST /v1/sessions/{sid}/track` `{event: submit_clicked|confirmation_seen|record|save}` | yes | the first two are audit only; `record` records the application as sent (user-confirmed); `save` adds it to the tracker as Preparing; answers `noted|recorded|already_recorded|saved` |
| `POST /v1/sessions/{sid}/research/company` `{company}`, `/research/site` `{domain}`, `/research/refresh` | yes | the user's corrections; each starts a new run and answers with the research snapshot |
| `GET /v1/documents[?session_id=]` | yes | approved CVs, default first; with a session also the CV written for that job |
| `GET /v1/documents/{id}/content` | yes | an approved document as base64 (at most 10 MiB) |

Poll events (`events[]`): `suggestion`, `research`, `knowledge` (profile or policy changed: rescan), `generation_budget` (writing unavailable), `drafts_paused` (this page used its automatic drafts), `tracked` (the application was recorded inside Meridian). After a Meridian restart every session route answers 410 `expired` and the extension opens the page's session again.

Suggestion states: `SKIP`, `NOT_APPLICABLE`, `SENSITIVE_BLANK`, `CONSENT`, `GENERATING`, `GENERATED_DRAFT`, `VERIFIED`, `INFERRED`, `NEEDS_YOU`; insertion tiers: `tab`, `explicit` (Enter or click), `never`.

Cross-cutting rule for existing routes: `POST /jobs/{id}/prepare` completes an application row the assistant created and nothing else has touched, instead of treating it as a duplicate.
