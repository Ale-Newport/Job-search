# Implementation and acceptance plan

Work proceeds on complete functional paths. Every UI action calls a real API; unavailable integrations show a reason and configuration, never fabricated results.

1. **Foundation**: Python package/dependency lock; SQLite/Alembic migrations and FTS; authenticated loopback service; Tauri lifecycle; React app; logging and settings. Verify clean first startup/restart and access control.
2. **Candidate/documents**: facts, verification, editing, relationships, CV PDF/DOCX/text extraction, proposed imports, immutable versions, grounded document generation with preview/diff. Verify unverified facts cannot answer forms.
3. **Job pipeline**: canonical URLs and ATS identities, deduplication, explainable score/search profiles/feedback, pagination, sources/watchlist/public APIs and manual ingestion. Verify repeated ingest gives one job/application.
4. **Browser**: persistent Chrome, observed indexed controls, supported operations, stale/occlusion gates, Laya/Jev contracts, platform capabilities, form filling/uploads, sensitive/unknown tasks, approval, independent confirmation, crash recovery. Verify against local forms including multi-step and frames.
5. **Tracking and mail**: application events, review queue, timeline/kanban, read-only OAuth/IMAP, classification/linking/deadlines, notifications. Verify each fixture and no status regression.
6. **Automation**: scheduler, per-source health, explicit rules/rate limits/domain approval, immediate pause, takeover/resume, provider settings/cost accounting/benchmarks, encrypted export/restore. Verify gates and interruption recovery.
7. **Desktop UX**: onboarding, dashboard, all workspace sections, command palette, light/dark themes, accessible controls/error states, activity and analytics from persisted data.
8. **Delivery**: lint/typecheck/unit/integration/browser E2E; source/dependency audit; unsigned macOS bundle and DMG; startup test of packaged backend; README/CONTRIBUTING/SECURITY; record actual acceptance evidence and remaining account-dependent checks.

## Account-dependent acceptance

Real email authorization, candidate verification, platform login, provider credentials and explicit approval of real applications belong to the user. Development tests use temporary data and localhost forms. Real public job discovery is exercised read-only. Completion claims must separate automated fixture tests, packaged launch and real account workflows.
