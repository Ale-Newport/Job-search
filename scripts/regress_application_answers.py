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
from jobagent.application_answer_llm import ApplicationAnswerLLM
from jobagent.security import SecretStore
from calibrate_intelligence import DryRunBrowser, audit_fields


async def run(args):
    if args.database.resolve().is_relative_to(Path.home() / 'Library/Application Support/Meridian'):
        raise ValueError('Calibration requires a separate database copy')
    args.output.mkdir(parents=True, exist_ok=True)
    db = Database(args.database)
    job = db.one("SELECT * FROM jobs WHERE id=?", (args.job,)) if args.job else {}
    job = {**(job or {}), "id": args.job or "answer-regression", "application_id": "answer-regression",
           "application_url": args.url, "url": args.url,
           "company": args.company or (job or {}).get("company", ""),
           "title": args.title or (job or {}).get("title", ""),
           "location": args.location or (job or {}).get("location", "")}
    settings = get_settings(db)
    engine = CandidateEngine(db, settings=settings, answer_llm=ApplicationAnswerLLM(db, SecretStore()))
    browser = DryRunBrowser(args.output)
    def progress(step):
        summary = {k: step.get(k) for k in ('operation', 'label', 'executed', 'form_state')}
        summary['diagnostics'] = [{k: d.get(k) for k in ('question_id', 'question', 'status', 'root_cause')}
                                  for d in step.get('answer_diagnostics', [])]
        (args.output / 'progress.json').write_text(json.dumps(summary, indent=2))
        print(json.dumps({k: v for k, v in summary.items() if k != 'diagnostics'}), flush=True)
    browser.on_step = progress
    record = {"job": job, "submitted": False, "inspection_only": not args.fill}
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
        (args.output / "audit.json").write_text(json.dumps(record, indent=2))
    print(json.dumps({"status": record.get("result", {}).get("status"),
                      "controls": len(record.get("initial_fields", [])),
                      "answers": len(record.get("result", {}).get("answers", [])),
                      "observed_matches": sum(f.get("verified_in_final_dom", False) for f in record.get("fields", [])),
                      "blocked": record.get("blocked"), "failure": record.get("failure"), "submitted": False}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--job")
    parser.add_argument("--company")
    parser.add_argument("--title")
    parser.add_argument("--location")
    parser.add_argument("--fill", action="store_true")
    parser.add_argument("--headless", action="store_true")
    asyncio.run(run(parser.parse_args()))
