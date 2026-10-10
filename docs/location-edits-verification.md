# Searchable locations and direct form corrections

Verified 10 October 2026 on macOS arm64. The signed update is installed at `/Applications/Meridian.app`; the native Today view and local service loaded successfully after relaunch.

## Behavior

- A location menu opens its observed control, finds its linked search input when necessary, searches the verified city, and selects a currently observed matching city/country option. Saved `London (UK)`, `London UK` and full UK geocoder labels work when the next employer supplies a different representation. A Canadian London appearing first does not override a confirmed UK location.
- Trusted user input owns its field while automatic filling continues on other fields. Corrections and explicit clears survive DOM replacement. Text writes check user ownership synchronously in the same browser task as the write, covering edits made while the engine action is already pending.
- The isolated edit listener distinguishes engine actions and delayed blur/change events from human input. Website-generated events cannot forge a correction. Password, sign-in, MFA and challenge inputs are excluded. Search text is not persisted as a completed location.
- Corrections update application answers and durable checkpoints, expire old review approval, and become scoped reusable memory. Matching profile fields update where their formats allow. Employer-specific number formats remain saved even when the profile editor requires a different representation. An explicit clear overrides older structured inference and clears a prefilled optional text field in a later application.
- Existing company/country and legal-answer reuse restrictions remain in force. A user-selected filename never becomes an approved reusable document. Required blanks, unsupported controls and failed memory saves remain reviewable human tasks.

## Executed checks

| Check | Result |
| --- | --- |
| Full backend suite, excluding browser directory | 1,108 passed |
| Full browser suite | 85 passed |
| Final location/edit/future-memory checks after adding cross-format cases | 13 passed |
| Final candidate intelligence, profile reasoning and profile memory checks | 582 passed |
| API-to-browser acceptance and profile tests | 11 passed |
| Ruff, frontend lint and TypeScript typecheck | Passed |
| Frozen sidecar and signed macOS app build | Passed |
| Packaged location form, stopped worker and hard backend restart | Passed; see `location-edits-packaged.json` |
| DMG checksum and deep strict installed signature | Passed |
| Installed binaries match built release | Passed |
| Existing production record IDs retained | Passed; see `location-edits-installation.json` |

The test batches overlap; their counts are not a distinct-test total. Browser coverage includes inline and separate-menu location searches, fresh option identities, equivalent UK labels, Canadian disambiguation, manual text/checkbox/clear edits, DOM replacement, typing during a pending engine write, future inference priority, untrusted event rejection, entry Apply navigation, multi-page forms and submission guards. The full browser batch preceded the extra cross-format cases; the final focused batch executes those cases on the delivered formatter.

Reproduce packaged validation with:

```sh
MERIDIAN_TEST=1 .venv/bin/python scripts/validate_packaged_workers.py --location
```

All new acceptance runs used synthetic local forms and synthetic candidate data. This follow-up did not fill or upload information to any real employer and did not submit a real application. The previous Citi entry-navigation verification remains documented separately. Existing third-party login and CAPTCHA requirements still require user intervention.

## Installed data and artifacts

The pre-update snapshot contained 3,765 jobs, 6 applications, 263 facts, 1 document with 2 versions, 52 emails, 86 sources, 79 companies and 5 browser memories. Every existing ID was retained. Startup added three configured sources; no existing source or company was removed.

The old app and a coherent SQLite backup are retained under `.build-cache/install-backups/20261010T113952Z-location-edits/`. Binary and DMG hashes are recorded in `upgrade-release.json`. Signing uses the existing stable local identity; this is a locally signed build, not a notarization claim.
