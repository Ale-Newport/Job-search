## What and why

<!-- What does this change do, and which problem does it solve? Link the issue: "Closes #123". -->

## How it was verified

<!-- Commands you ran and what you checked by hand. "Tests pass" alone is not enough for UI or browser changes. -->

- [ ] `make lint` and `make typecheck`
- [ ] `make test` (backend and desktop)
- [ ] `make test-ext-fast` if `extension/` or `backend/jobagent/extension/` changed
- [ ] Exercised the changed workflow in the running app (`make dev`)

## Checklist

- [ ] No personal data, real applications, credentials or `.env` files are included (fixtures are synthetic).
- [ ] New settings, data disclosures or failure modes are documented.
- [ ] Database changes come with a forward Alembic migration.
- [ ] Extension strings exist in English and Spanish; `schemas.py` and `protocol.ts` changed together if the protocol changed.

<!-- All pull requests are reviewed and approved by the code owner before they can be merged. -->
