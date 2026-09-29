"""Descriptive pipeline analytics from recorded milestones, with explicit denominators."""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import mean, median


SUBMISSION = {"APPLIED", "CONFIRMED"}
RESPONSE = {"RECRUITER_SCREEN", "ASSESSMENT", "TECHNICAL_TEST", "INTERVIEW", "FINAL_INTERVIEW", "OFFER", "REJECTED"}
MILESTONES = {
    "responded": RESPONSE,
    "rejected": {"REJECTED"},
    "assessments": {"ASSESSMENT", "TECHNICAL_TEST"},
    "interviews": {"INTERVIEW", "FINAL_INTERVIEW"},
    "offers": {"OFFER"},
}
RATE_NAMES = {
    "responded": "response_rate",
    "rejected": "rejection_rate",
    "assessments": "assessment_rate",
    "interviews": "interview_rate",
    "offers": "offer_rate",
}
EMAIL_STATUS = {
    "APPLICATION_CONFIRMATION": "CONFIRMED",
    "REJECTION": "REJECTED",
    "OFFER": "OFFER",
    "CODING_TEST": "TECHNICAL_TEST",
    "ASSESSMENT_INVITATION": "ASSESSMENT",
    "INTERVIEW_CONFIRMATION": "INTERVIEW",
    "INTERVIEW_REQUEST": "INTERVIEW",
    "RECRUITER_MESSAGE": "RECRUITER_SCREEN",
}
SMALL_SAMPLE = 20


def timestamp(value):
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def percentage(numerator, denominator):
    return round(100 * numerator / denominator, 1) if denominator else None


def summarize(rows):
    submitted = sum(row["submitted"] for row in rows)
    scores = [row["score"] for row in rows if row["score"] is not None]
    result = {
        "applications": len(rows),
        "submitted": submitted,
        "sample_size": submitted,
        "small_sample": submitted < SMALL_SAMPLE,
        "average_match": round(mean(scores), 1) if scores else None,
    }
    for milestone, rate in RATE_NAMES.items():
        result[milestone] = sum(row[milestone] for row in rows)
        result[rate] = percentage(result[milestone], submitted)
    return result


def pipeline_analytics(db):
    applications = db.query(
        "SELECT a.*,j.title,j.company,j.source,j.id AS linked_job_id FROM applications a JOIN jobs j ON j.id=a.job_id"
    )
    events = defaultdict(list)
    emails = defaultdict(list)
    matches = defaultdict(list)
    cvs = defaultdict(list)
    quality = {
        "responses_without_submission": 0,
        "invalid_timestamps": 0,
        "responses_before_submission": 0,
        "submitted_without_valid_timestamp": 0,
        "missing_match_score": 0,
        "missing_cv_version": 0,
        "match_score_basis": "latest_recorded_at_or_before_submission",
        "small_sample_threshold": SMALL_SAMPLE,
    }
    for email in db.query(
        "SELECT application_id,classification,received_at FROM email_messages WHERE application_id IS NOT NULL AND confidence>=0.9"
    ):
        at = timestamp(email["received_at"])
        status = EMAIL_STATUS.get(email["classification"])
        if at and status:
            emails[(email["application_id"], status)].append(at)
    for event in db.query("SELECT application_id,status,origin,created_at FROM application_events"):
        at = timestamp(event["created_at"])
        if at is None:
            quality["invalid_timestamps"] += 1
        # Imported emails may arrive in the app weeks later; use their actual received date.
        email_dates = emails[(event["application_id"], event["status"])] if event["origin"] == "email" else []
        events[event["application_id"]].extend((event["status"], received) for received in email_dates or [at])
    for match in db.query("SELECT job_id,score,created_at FROM job_matches ORDER BY created_at"):
        at = timestamp(match["created_at"])
        if at is not None and 0 <= match["score"] <= 100:
            matches[match["job_id"]].append((at, match["score"]))
    for cv in db.query(
        "SELECT ad.application_id,v.id AS document_version_id,d.name AS document_name,v.filename,v.version FROM application_documents ad JOIN document_versions v ON v.id=ad.document_version_id JOIN documents d ON d.id=v.document_id WHERE lower(ad.kind) IN ('cv','resume')"
    ):
        cvs[cv.pop("application_id")].append(cv)

    rows, response_days = [], []
    days, weeks = Counter(), Counter()
    current_time = datetime.now(timezone.utc)
    for application in applications:
        history = events[application["id"]]
        submission_dates = [at for status, at in history if status in SUBMISSION and at is not None]
        submitted = any(status in SUBMISSION for status, _ in history)
        submitted_at = min(submission_dates) if submission_dates else None
        if submitted and submitted_at is None:
            quality["submitted_without_valid_timestamp"] += 1
        if not submitted and any(status in RESPONSE for status, _ in history):
            quality["responses_without_submission"] += 1
        before = [
            (status, at) for status, at in history if status in RESPONSE and at and submitted_at and at < submitted_at
        ]
        if before:
            quality["responses_before_submission"] += 1
        # Explicitly pre-submission activity cannot be a response to this submission.
        eligible = {
            status for status, at in history if submitted and (at is None or submitted_at is None or at >= submitted_at)
        }
        score_cutoff = submitted_at if submitted else current_time
        score_history = [
            (at, score)
            for at, score in matches[application["linked_job_id"]]
            if score_cutoff is not None and at <= score_cutoff
        ]
        score = max(score_history, key=lambda item: (item[0], item[1]))[1] if score_history else None
        selected_cv = cvs[application["id"]]
        cv = (
            selected_cv[0]
            if len(selected_cv) == 1
            else {"document_version_id": None, "document_name": "Unknown CV version", "filename": None, "version": None}
        )
        row = {
            "submitted": submitted,
            "score": score,
            "source": application["source"] or "Unknown",
            "company": application["company"] or "Unknown",
            "role": application["title"] or "Unknown",
            "cv": cv,
        }
        row.update({key: bool(statuses & eligible) for key, statuses in MILESTONES.items()})
        row["bucket"] = (
            "Unknown"
            if score is None
            else "0–39"
            if score < 40
            else "40–59"
            if score < 60
            else "60–79"
            if score < 80
            else "80–100"
        )
        rows.append(row)
        quality["missing_match_score"] += int(score is None)
        quality["missing_cv_version"] += int(cv["document_version_id"] is None)
        first_responses = [
            at for status, at in history if status in RESPONSE and at and submitted_at and at >= submitted_at
        ]
        if first_responses:
            response_days.append((min(first_responses) - submitted_at).total_seconds() / 86400)
        created = timestamp(application["created_at"])
        if created:
            days[created.date().isoformat()] += 1
            weeks[(created.date() - timedelta(days=created.weekday())).isoformat()] += 1
        else:
            quality["invalid_timestamps"] += 1

    def cohorts(key):
        groups = defaultdict(list)
        for row in rows:
            groups[row[key]].append(row)
        return [{key: value, **summarize(group)} for value, group in sorted(groups.items())]

    cv_groups = defaultdict(list)
    for row in rows:
        cv_groups[row["cv"]["document_version_id"]].append(row)
    cv_performance = [{**group[0]["cv"], **summarize(group)} for group in cv_groups.values()]
    overall = summarize(rows)
    discovered = db.one("SELECT COUNT(*) AS n FROM jobs")["n"]
    funnel = [
        {
            "stage": "Discovered",
            "count": discovered,
            "denominator": discovered,
            "rate": percentage(discovered, discovered),
        },
        {
            "stage": "Applied",
            "count": overall["submitted"],
            "denominator": discovered,
            "rate": percentage(overall["submitted"], discovered),
        },
    ]
    for label, key in [
        ("Response", "responded"),
        ("Assessment", "assessments"),
        ("Interview", "interviews"),
        ("Offer", "offers"),
    ]:
        funnel.append(
            {
                "stage": label,
                "count": overall[key],
                "denominator": overall["submitted"],
                "rate": percentage(overall[key], overall["submitted"]),
            }
        )
    sources = cohorts("source")
    return {
        **overall,
        "status_counts": db.query(
            "SELECT status,COUNT(*) AS count FROM applications GROUP BY status ORDER BY count DESC"
        ),
        "applications_over_time": [{"date": date, "count": count} for date, count in sorted(days.items())],
        "applications_by_week": [{"week_start": date, "count": count} for date, count in sorted(weeks.items())],
        "source_performance": sources,
        "applications_by_source": sources,
        "applications_by_company": cohorts("company"),
        "applications_by_role": cohorts("role"),
        "cv_version_performance": cv_performance,
        "match_score_performance": cohorts("bucket"),
        "funnel": funnel,
        "time_to_response": {
            "mean_days": round(mean(response_days), 2) if response_days else None,
            "median_days": round(median(response_days), 2) if response_days else None,
            "min_days": round(min(response_days), 2) if response_days else None,
            "max_days": round(max(response_days), 2) if response_days else None,
            "sample_size": len(response_days),
            "small_sample": len(response_days) < SMALL_SAMPLE,
            "pending_applications": overall["submitted"] - overall["responded"],
        },
        "skills": db.query(
            "SELECT s.name,COUNT(DISTINCT js.job_id) AS jobs FROM job_skills js JOIN skills s ON s.id=js.skill_id GROUP BY s.id ORDER BY jobs DESC LIMIT 20"
        ),
        "data_quality": quality,
        "definitions": {
            "submitted": "Distinct applications with an explicit APPLIED or CONFIRMED event; later status alone does not imply submission.",
            "rates": "Percent of submitted applications with the recorded milestone; events known to predate submission are excluded. Missing dates retain the milestone but exclude response-duration measurement. No denominator returns null.",
            "funnel": "Observed milestones, not inferred stages; an offer does not invent an assessment or interview event.",
            "distributions": "Application day/week counts use creation time in UTC; weeks start Monday. Cohorts include all prepared applications and expose submitted counts separately.",
            "response_time": "First recorded recruitment response minus first APPLIED/CONFIRMED date. Confirmation receipts are excluded; accepted email events use message received time. Pending applications are not assigned a zero delay.",
            "match_score": "Latest recorded match at or before submission; absent history is Unknown. Unsubmitted applications use the latest recorded score.",
            "cv_version": "Selected CV document version. Missing or ambiguous selections are Unknown.",
        },
        "note": "Descriptive associations only, not causal explanations. Cohorts with fewer than 20 submitted applications are small samples; compare cautiously and account for pending responses and unequal follow-up time.",
    }
