"""Small declarative rules. Rules narrow preparation; they never override submission gates."""

from __future__ import annotations

from .db import decode_row


def matches_rule(job: dict, conditions: dict) -> bool:
    if float(job.get("match_score", 0)) < float(conditions.get("min_match", 0)):
        return False
    for field, condition in (("location", "location"), ("title", "role_contains"), ("ats", "ats")):
        expected = str(conditions.get(condition, "")).strip().casefold()
        actual = str(job.get(field, "")).casefold()
        if expected and (actual != expected if field == "ats" else expected not in actual):
            return False
    details = job.get("match_details", {})
    if isinstance(details, dict) and (
        details.get("excluded") or details.get("filtered_out") or not details.get("eligible", True)
    ):
        return False
    return job.get("status") != "IGNORED"


async def run_rules(db, data_dir, automation):
    from .core import get_settings, prepare_application

    settings = get_settings(db)
    if settings.get("automation_paused", True):
        return {"prepared": 0, "started": None}
    prepared = 0
    rules = [r for r in settings.get("rules", []) if r.get("enabled")]
    companies = {r["name"].casefold() for r in db.query("SELECT name FROM companies WHERE auto_apply=1")}
    profiles = {
        r["id"]: decode_row(r)["config"]
        for r in db.query("SELECT id,config FROM search_profiles WHERE json_extract(config,'$.mode')='auto'")
    }
    sources = {
        r["id"] for r in db.query("SELECT id FROM sources WHERE enabled=1 AND json_extract(config,'$.auto_apply')=1")
    }
    if not rules and not companies and not profiles and not sources:
        return {"prepared": 0, "started": None}
    jobs = db.query(
        "SELECT j.* FROM jobs j LEFT JOIN applications a ON a.job_id=j.id WHERE a.id IS NULL AND j.status!='IGNORED' ORDER BY j.priority_score DESC,j.match_score DESC LIMIT 200"
    )
    # Bounded batch; each created workspace is durable and unique per job.
    for raw in jobs:
        job = decode_row(raw)
        rule = next((r for r in rules if matches_rule(job, r.get("conditions", {}))), None)
        if not rule and matches_rule(job, {}):
            profile_matches = job.get("match_details", {}).get("profiles", [])
            profile_auto = any(
                p.get("id") in profiles
                and p.get("eligible")
                and p.get("score", 0) >= profiles[p["id"]].get("min_match", 85)
                for p in profile_matches
            )
            source_auto = bool(
                sources.intersection(
                    r["source_id"] for r in db.query("SELECT source_id FROM job_sources WHERE job_id=?", (job["id"],))
                )
            )
            if job["company"].casefold() in companies or profile_auto or source_auto:
                rule = {"action": "auto_apply"}
        if not rule:
            continue
        mode = "auto" if rule["action"] == "auto_apply" else "review"
        document_id = settings.get("default_document_version_id")
        application = prepare_application(db, job["id"], mode, document_id, data_dir)
        prepared += 1
        if mode == "auto" and settings.get("auto_apply") and document_id and not automation.running:
            try:
                return {"prepared": prepared, "started": automation.launch(application["id"])}
            except ValueError as exc:
                automation.human_task(application["id"], "AUTO_GATE", str(exc))
        if prepared >= 10:
            break
    return {"prepared": prepared, "started": None}
