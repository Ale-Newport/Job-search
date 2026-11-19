# Contributing

Thanks for helping improve Meridian. This guide covers how to propose a change, how it is reviewed and the engineering rules
the project holds itself to. Everyone taking part follows the [code of conduct](CODE_OF_CONDUCT.md).

## How contributions are accepted

1. **Find or open an issue.** Issues labelled [`good first issue`](https://github.com/Ale-Newport/Job-search/labels/good%20first%20issue)
   are small and well scoped; [`help wanted`](https://github.com/Ale-Newport/Job-search/labels/help%20wanted) marks larger
   work the maintainer would welcome. Comment on the issue before you start so work is not duplicated. Questions and early
   ideas belong in [Discussions](https://github.com/Ale-Newport/Job-search/discussions).
2. **Fork the repository** and create a branch in your fork. Nobody except the maintainer can push to this repository.
3. **Open a pull request** against `main` and fill in the template. CI runs once the maintainer approves workflows for
   your fork (first-time and external contributors always need that approval).
4. **Review.** Every pull request needs an approving review from the code owner ([@Ale-Newport](https://github.com/Ale-Newport)).
   Pushing new commits dismisses earlier approvals, all review threads must be resolved, and you cannot approve your own
   pull request. The maintainer merges.

Report security problems privately as described in [SECURITY.md](SECURITY.md), never in an issue or pull request.

## Development setup

On an Apple Silicon Mac with macOS 15+, Python 3.12, Node.js, Rust and the Xcode Command Line Tools:

```sh
git clone https://github.com/<your-user>/Job-search.git && cd Job-search
make setup   # virtualenv from requirements.lock, npm installs, extension dependencies, bundled backend
make dev     # desktop app with hot reload
```

Without Tauri: `make backend` and `make dev-web` in two terminals. Checks that CI runs and you should run before pushing:

```sh
make lint typecheck
make test                       # backend (pytest, MERIDIAN_TEST=1) and desktop (cargo test)
npm --prefix extension test     # extension unit tests, seconds
make test-ext-fast              # if you touched the extension or its backend
```

See [TESTING.md](TESTING.md) for the full matrix (UI end-to-end suite, real-browser extension tests, release gate).

## Engineering rules

Use Python 3.12 and the locked dependencies (`make setup`). Frontend source lives in `frontend/src`; local services are in `backend/jobagent`; the desktop lifecycle is in `src-tauri`; migrations live in `migrations`.

Keep changes narrow. Use descriptive commits without co-author trailers. Do not commit candidate documents, application data, browser profiles, OAuth tokens or `.env` files. New database changes require a forward Alembic migration. Application events are append-only; never rewrite history to hide an error.

Every visible control must call a functioning API or make a real local change. Use empty states rather than invented records. Do not describe an identified ATS as fully automated unless its concrete flow has evidence. Unknown data must create a human task; imported facts remain unverified. Generated answers must carry provenance and remain reviewable.

Run `make test`, `make lint`, `make typecheck` and `make build`. For browser changes, add a local fixture covering the failure and verify stale state, pause and review gates. Use no real recruitment submissions in tests. If changing provider integrations, use mock transports plus an explicitly configured read-only smoke test, and document what was actually verified.

Keep decision-model APIs separate from text-model APIs. Never grant either model executable browser input. Public fetches must reject local/private redirects. Store secrets only in Keychain. Preserve bounded polling, retries, pagination and human takeover.

Document new settings, data disclosures, failure recovery and packaging requirements. A build passing is necessary but not sufficient: launch the actual macOS bundle and exercise the changed workflow.

For the browser assistant (`backend/jobagent/extension`, `extension/`, `frontend/src/extension*`): change `schemas.py` and `extension/src/shared/protocol.ts` together; keep every extension string in English and Spanish (`extension/src/content/i18n.ts`, `extension/src/shared/i18n-core.ts`; the Spanish dictionaries are typed against the English ones); never loosen an insertion tier, a never-touch rule or the answer gate without a test that proves the case, and run `make test-ext-fast` (and the real-browser suite for keyboard, fill or pairing changes) plus `make extension` before a pull request. Do not add anything the extension fetches from the network itself: only Meridian fetches public pages. See `docs/EXTENSION.md` for commands and `docs/EXTENSION_DESIGN.md` for the contract.
