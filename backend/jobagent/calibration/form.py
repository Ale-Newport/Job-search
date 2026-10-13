"""Real ATS calibration using the application's own observer and filler.

Never calls submission. Reports observed values independently of generated records.
Personal data and DOM snapshots belong in ignored .local-data, never in Git.
"""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from jobagent.automation.adapters import get_adapter
from jobagent.automation.job_context import prepare_job_context
from jobagent.core import get_settings
from jobagent.db import Database
from jobagent.intelligence.engine import CandidateEngine
from jobagent.application_answer_llm import ApplicationAnswerLLM, provider_config
from jobagent.security import SecretStore
from .browser import DryRunBrowser, audit_fields
from .answers import calibration_usage, configure_from_live


async def run(args):
    if args.database.resolve().is_relative_to(Path.home() / 'Library/Application Support/Meridian'):
        raise ValueError('Calibration requires a separate database copy')
    args.output.mkdir(parents=True, exist_ok=True)
    db = Database(args.database)
    usage_db = db
    if args.usage_database:
        usage_path = args.usage_database.resolve()
        if not usage_path.is_file() or usage_path == args.database.resolve():
            raise ValueError('--usage-database must point at the existing shared live database, not this QA profile copy')
        usage_db = Database(usage_path)
        configure_from_live(db, usage_db)
    job = db.one("SELECT * FROM jobs WHERE id=?", (args.job,)) if args.job else {}
    if args.job and not job:
        raise ValueError('The calibration database does not contain the exact target job')
    if job:
        from jobagent.discovery import ats_identity
        stored_kind, stored_identity = ats_identity(job['application_url'] or job['url'])
        target_kind, target_identity = ats_identity(args.url)
        if stored_identity and target_identity and (stored_kind, stored_identity) != (target_kind, target_identity):
            raise ValueError('Calibration URL identifies a different vacancy from the stored target job')
        if not stored_identity or not target_identity:
            from jobagent.discovery import canonical_url
            if canonical_url(job['application_url'] or job['url']) != canonical_url(args.url):
                raise ValueError('Calibration URL must match the stored target when its posting identity cannot be verified')
    job = {**(job or {}), "id": args.job or "answer-regression", "job_id": args.job or "answer-regression", "application_id": "answer-regression",
           "application_url": args.url, "url": args.url,
           "company": args.company or (job or {}).get("company", ""),
           "title": args.title or (job or {}).get("title", ""),
           "location": args.location or (job or {}).get("location", "")}
    settings = get_settings(db)
    remote = settings.get('text_provider', 'none') != 'none' and not provider_config(settings)[2]
    if args.fill and remote and not args.allow_remote:
        raise ValueError('Remote answer generation requires explicit --allow-remote authorization')
    if args.fill and remote and not args.usage_database:
        raise ValueError('Remote fill QA requires --usage-database pointing at the shared live database so profile copies cannot reset aggregate spending')
    engine = CandidateEngine(db, settings=settings, answer_llm=ApplicationAnswerLLM(db, SecretStore(), usage_db=usage_db))
    browser = DryRunBrowser(args.output)
    def progress(step):
        summary = {k: step.get(k) for k in ('operation', 'label', 'executed', 'form_state')}
        summary['diagnostics'] = [{k: d.get(k) for k in ('question_id', 'question', 'status', 'root_cause')}
                                  for d in step.get('answer_diagnostics', [])]
        (args.output / 'progress.json').write_text(json.dumps(summary, indent=2))
        print(json.dumps({k: v for k, v in summary.items() if k != 'diagnostics'}), flush=True)
    browser.on_step = progress
    record = {"job": job, "submitted": False, "inspection_only": not args.fill,
              "usage_ledger": str(args.usage_database.resolve()) if args.usage_database else str(args.database.resolve()),
              "usage_before": calibration_usage(usage_db)}
    try:
        await browser.start(headless=args.headless, channel="chrome")
        await browser.open(args.url)
        snapshot = await browser.wait_until_ready()
        (args.output / "initial-observation.json").write_text(json.dumps(snapshot, indent=2))
        adapter = get_adapter(args.url)
        fields = [{**e, "exact_question": adapter.question(e)} for e in snapshot["elements"]
                  if any(o in e["operations"] for o in ("TYPE_TEXT", "SELECT", "CHECK", "UPLOAD"))]
        record["initial_fields"] = fields
        # Native checkValidity observes constraints without submitting or triggering
        # requestSubmit. Custom validation errors are separately read by the observer.
        record["native_constraints"] = await browser.page.locator("input,select,textarea").evaluate_all(
            "nodes => nodes.map(n => ({name:n.name,id:n.id,type:n.type,required:n.required,"
            "multiple:!!n.multiple,pattern:n.pattern||'',min:n.min||'',max:n.max||'',"
            "maxLength:n.maxLength,valid:n.checkValidity(),message:n.validationMessage,"
            "options:n.options ? [...n.options].map(o=>({label:o.label,value:o.value,disabled:o.disabled})) : []}))")
        if args.fill:
            facts = db.query("SELECT * FROM facts WHERE verification_status='verified' OR locked=1")
            documents = db.query("SELECT v.*,CASE WHEN d.kind='import' THEN 'cv' ELSE d.kind END kind "
                                 "FROM document_versions v JOIN documents d ON d.id=v.document_id "
                                 "WHERE v.approved=1 AND d.kind IN ('import','cv','resume','base_cv') "
                                 "ORDER BY v.created_at DESC LIMIT 1")
            settings.update({"_candidate_engine": engine, "_job_context": await prepare_job_context(job),
                             "assisted_autofill": True, "section_consent": False,
                             "skip_optional_unknown": True, "browser_max_steps": 250,
                             "default_mode": "review", "auto_apply": False,
                             "browser_engine": "deterministic"})
            if args.local_options:
                from jobagent.automation.engines import SystemOneEngine
                settings['_local_option_engine'] = SystemOneEngine('laya', endpoint=settings.get('laya_endpoint'))
            result = await browser.fill_application(job, facts, documents, "REVIEW", settings)
            snapshot = await browser.observe()
        else:
            result = {"status": "inspection_only", "answers": [], "questions": []}
        record.update(result=result, fields=audit_fields(snapshot, adapter, engine, result, job),
                      blocked=snapshot["blocked"], errors=snapshot["errors"],
                      unresolved_required=snapshot.get("unresolved_required", []))
        (args.output / "final-observation.json").write_text(json.dumps(snapshot, indent=2))
        await browser.page.screenshot(path=str(args.output / "form.png"), full_page=True)
    except Exception as exc:
        record["failure"] = type(exc).__name__ + ": " + str(exc)[:1000]
    finally:
        await browser.close()
        record['usage_after'] = calibration_usage(usage_db)
        (args.output / "audit.json").write_text(json.dumps(record, indent=2))
    print(json.dumps({"status": record.get("result", {}).get("status"),
                      "controls": len(record.get("initial_fields", [])),
                      "answers": len(record.get("result", {}).get("answers", [])),
                      "observed_matches": sum(f.get("verified_in_final_dom", False) for f in record.get("fields", [])),
                      "blocked": record.get("blocked"), "failure": record.get("failure"), "submitted": False}), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--job")
    parser.add_argument("--company")
    parser.add_argument("--title")
    parser.add_argument("--location")
    parser.add_argument("--fill", action="store_true")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--allow-remote", action="store_true")
    parser.add_argument('--local-options', action='store_true', help='Use local Laya for supported observed option matching; never cloud demographics')
    parser.add_argument('--uncapped-validation', action='store_true', help='Explicitly authorize a ten-minute uncapped lease; restore previous controls on exit')
    parser.add_argument("--usage-database", type=Path, help="Existing live database supplying current nonsecret model settings and the shared monthly usage ledger")
    args = parser.parse_args(argv)
    if args.uncapped_validation:
        if not args.allow_remote or not args.usage_database:
            parser.error('--uncapped-validation requires --allow-remote and --usage-database')
        from .budget import uncapped_validation
        with uncapped_validation(args.usage_database):
            asyncio.run(run(args))
    else:
        asyncio.run(run(args))
