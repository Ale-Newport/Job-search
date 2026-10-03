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
from jobagent.automation.runner import BrowserManager
from jobagent.automation.adapters import get_adapter
from jobagent.intelligence.engine import CandidateEngine
from jobagent.application_profile import application_knowledge
from jobagent.automation.reasoning import conditional_state
from jobagent.automation.answers import selected_choice_matches, normalize


class DryRunBrowser(BrowserManager):
    async def submit(self, *args, **kwargs):
        raise RuntimeError("Calibration never submits applications")

    async def execute(self, decision, snapshot=None, **kwargs):
        if kwargs.get("allow_submit"):
            raise RuntimeError("Submission permission is unavailable during calibration")
        return await super().execute(decision, snapshot, **kwargs)


def audit_fields(snapshot, adapter, intelligence, result, job):
    """Compare final observed values with grounded answers, including conditionals.

    Recorded answers alone do not prove a write succeeded. Radios use checked
    state, not the HTML value attribute; entry/login pages are not application
    coverage. This is a local audit and never interacts with the browser.
    """
    if adapter.manual_only:
        return []
    fields, seen = [], set()
    recorded = {normalize(a["question"]): a for a in result.get("answers", [])}
    for field in snapshot["elements"]:
        if (
            not any(op in field["operations"] for op in ["TYPE_TEXT", "SELECT", "CHECK", "UPLOAD"])
            and field["role"] != "combobox"
        ):
            continue
        key = (field["document_id"], field.get("form_id"), field.get("group") or field["node_id"])
        if key in seen:
            continue
        seen.add(key)
        question = adapter.question(field)
        condition = conditional_state(field, snapshot["elements"], adapter)
        context = {**field, "_condition": condition or {}}
        reasoning = intelligence.answer_question(question, context, job)
        answer = recorded.get(normalize(question))
        expected = answer.get("answer") if answer else reasoning["answer"]
        if condition and condition["applies"] is not True:
            expected = None
        value = field.get("selected_text") or field.get("value") or ""
        if field["role"] == "radio":
            siblings = [
                e
                for e in snapshot["elements"]
                if e["document_id"] == field["document_id"]
                and e.get("group") == field.get("group")
                and e["role"] == "radio"
            ]
            value = next((e["label"] for e in siblings if e.get("checked")), "")
        elif field["role"] == "checkbox":
            value = "Yes" if field.get("checked") else ""
        match = bool(expected is not None and value and selected_choice_matches(question, str(expected), str(value)))
        fields.append(
            {
                "question": question,
                "required": bool(field.get("required")),
                "role": field["role"],
                "value": value,
                "checked": field.get("checked"),
                "condition": condition,
                "intent": (answer or reasoning).get("canonical_intent", reasoning["canonical_intent"]),
                "expected_answer": expected,
                "verified_in_final_dom": match,
                "root_cause": None if match else reasoning.get("root_cause"),
                "description": field.get("description"),
                "options": field.get("options"),
                "derived": bool(answer and answer.get("inferred")),
                "narrative": bool(answer and field.get("tag") == "textarea"),
                "fact_ids": (answer or reasoning).get("fact_ids", []),
            }
        )
    # Several ATSs replace the upload input with a filename after completion.
    observed_questions = {normalize(f["question"]) for f in fields}
    for answer in result.get("answers", []):
        if (
            answer.get("document_version_id")
            and normalize(answer["question"]) not in observed_questions
            and answer["answer"] in snapshot.get("text", "")
        ):
            fields.append(
                {
                    "question": answer["question"],
                    "required": False,
                    "role": "upload",
                    "value": answer["answer"],
                    "checked": False,
                    "condition": None,
                    "intent": "DOCUMENT",
                    "expected_answer": answer["answer"],
                    "verified_in_final_dom": True,
                    "root_cause": None,
                    "description": "Uploaded filename remains visible after input replacement.",
                    "options": [],
                    "derived": False,
                    "narrative": False,
                    "fact_ids": [],
                    "document_version_id": answer["document_version_id"],
                }
            )
    return fields


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
