# Contributing

Use Python 3.12 and the locked dependencies (`make setup`). Frontend source lives in `frontend/src`; local services are in `backend/jobagent`; the desktop lifecycle is in `src-tauri`; migrations live in `migrations`.

Keep changes narrow. Use descriptive commits without co-author trailers. Do not commit candidate documents, application data, browser profiles, OAuth tokens or `.env` files. New database changes require a forward Alembic migration. Application events are append-only; never rewrite history to hide an error.

Every visible control must call a functioning API or make a real local change. Use empty states rather than invented records. Do not describe an identified ATS as fully automated unless its concrete flow has evidence. Unknown data must create a human task; imported facts remain unverified. Generated answers must carry provenance and remain reviewable.

Run `make test`, `make lint`, `make typecheck` and `make build`. For browser changes, add a local fixture covering the failure and verify stale state, pause and review gates. Use no real recruitment submissions in tests. If changing provider integrations, use mock transports plus an explicitly configured read-only smoke test, and document what was actually verified.

Keep decision-model APIs separate from text-model APIs. Never grant either model executable browser input. Public fetches must reject local/private redirects. Store secrets only in Keychain. Preserve bounded polling, retries, pagination and human takeover.

Document new settings, data disclosures, failure recovery and packaging requirements. A build passing is necessary but not sufficient: launch the actual macOS bundle and exercise the changed workflow.

For the browser assistant (`backend/jobagent/extension`, `extension/`, `frontend/src/extension*`): change `schemas.py` and `extension/src/shared/protocol.ts` together; keep every extension string in English and Spanish (`extension/src/content/i18n.ts`, `extension/src/shared/i18n-core.ts`; the Spanish dictionaries are typed against the English ones); never loosen an insertion tier, a never-touch rule or the answer gate without a test that proves the case, and run `make test-ext-fast` (and the real-browser suite for keyboard, fill or pairing changes) plus `make extension` before a pull request. Do not add anything the extension fetches from the network itself: only Meridian fetches public pages. See `docs/EXTENSION.md` for commands and `docs/EXTENSION_DESIGN.md` for the contract.
