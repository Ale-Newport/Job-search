"""Inspect or fill ranked forms using Meridian's normal browser and reasoning engine.

Default is read-only inspection. --fill must only be used after authorization to
share the selected candidate data with the selected employers. Never submits.
"""

import argparse
import asyncio
import json
from pathlib import Path
from jobagent.db import Database
from jobagent.core import get_settings
from jobagent.tracking import daily_data
from jobagent.automation.adapters import get_adapter
from jobagent.intelligence.engine import CandidateEngine
from jobagent.application_profile import application_knowledge
from jobagent.calibration.browser import DryRunBrowser, audit_fields


async def calibrate(db_path, output, limit=10, fill=False, resume=False):
    output.mkdir(parents=True, exist_ok=True)
    if db_path.resolve().is_relative_to(Path.home() / "Library/Application Support/Meridian"):
        raise ValueError("Calibration requires a separate database copy")
    db = Database(db_path)
    config = get_settings(db)
    jobs = daily_data(db)["items"][:limit]
    selection = output / "selected-jobs.json"
    if resume and selection.exists():
        jobs = json.loads(selection.read_text())
    else:
        selection.write_text(json.dumps(jobs, indent=2))
    docs = (
        db.query(
            "SELECT v.*,CASE WHEN d.kind='import' THEN 'cv' ELSE d.kind END AS kind FROM document_versions v JOIN documents d ON d.id=v.document_id WHERE v.approved=1 AND d.kind IN ('import','cv','resume','base_cv') ORDER BY v.created_at DESC LIMIT 1"
        )
        if fill
        else []
    )
    facts = db.query("SELECT * FROM facts WHERE verification_status='verified' OR locked=1") if fill else []
    intelligence = CandidateEngine(db, settings=config)
    browser = DryRunBrowser(output)
    results = []
    try:
        await browser.start(headless=False, channel="chrome")
        for index, job in enumerate(jobs):
            existing = db.one("SELECT id FROM applications WHERE job_id=?", (job["id"],))
            job = {**job, "id": "calibration-" + job["id"], "application_id": existing["id"] if existing else None}
            scoped_facts, skipped = (
                application_knowledge(db, {**job, "id": job["application_id"] or job["id"]}, facts)
                if fill
                else ([], [])
            )
            print(
                json.dumps({"event": "start", "index": index + 1, "company": job["company"], "title": job["title"]}),
                flush=True,
            )
            config.update(
                {
                    "_candidate_engine": intelligence,
                    "_job_context": job,
                    "assisted_autofill": True,
                    "section_consent": False,
                    "skip_optional_unknown": True,
                    "inline_questions": False,
                    "browser_max_steps": 160,
                    "_skipped_questions": skipped,
                    "default_mode": "review",
                    "auto_apply": False,
                }
            )
            try:
                if fill:
                    result = await asyncio.wait_for(
                        browser.fill_application(job, scoped_facts, docs, "REVIEW", config), timeout=300
                    )
                    snapshot = await browser.observe()
                else:
                    await browser.open(job.get("application_url") or job["url"])
                    snapshot = await browser.wait_until_ready()
                    result = {"status": "inspection_only", "answers": [], "questions": []}
                adapter = get_adapter(job.get("application_url") or job["url"], job.get("ats"))
                fields = audit_fields(snapshot, adapter, intelligence, result, job)
                record = {
                    "job": job,
                    "result": result,
                    "fields": fields,
                    "blocked": snapshot.get("blocked"),
                    "errors": snapshot.get("errors"),
                    "unresolved_required": snapshot.get("unresolved_required"),
                    "required_fields": sum(f["required"] for f in fields),
                    "answered": len(result.get("answers", [])),
                    "submitted": False,
                    "inspection_only": not fill,
                }
                await browser.page.screenshot(path=str(output / f"form-{index + 1}.png"), full_page=True)
                (output / f"observed-{index + 1}.json").write_text(json.dumps(snapshot, indent=2))
            except Exception as exc:
                record = {
                    "job": job,
                    "failure": type(exc).__name__ + ": " + str(exc)[:400],
                    "submitted": False,
                    "inspection_only": not fill,
                }
            results.append(record)
            (output / "audit.json").write_text(json.dumps(results, indent=2))
            print(
                json.dumps(
                    {
                        "event": "complete",
                        "index": index + 1,
                        "company": job["company"],
                        "status": record.get("result", {}).get("status"),
                        "required": record.get("required_fields"),
                        "answers": record.get("answered"),
                        "failure": record.get("failure"),
                        "blocked": record.get("blocked"),
                    }
                ),
                flush=True,
            )
    finally:
        await browser.close()
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--fill", action="store_true")
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    asyncio.run(calibrate(args.database, args.output, args.limit, args.fill, args.resume))
