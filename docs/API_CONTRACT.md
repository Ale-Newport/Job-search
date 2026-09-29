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
