from __future__ import annotations

import asyncio
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from .db import decode_row, now


def settings_for(db):
    from .core import get_settings

    return get_settings(db)


class Orchestrator:
    def __init__(self, db, data_dir: Path):
        from .automation import BrowserManager

        self.db, self.data_dir = db, data_dir
        self.browser = BrowserManager(data_dir)
        from .automation.browser_prompt import BrowserPrompt
        self.prompt = BrowserPrompt()
        self.text_service = None
        self.lock = asyncio.Lock()
        self.tasks: set[asyncio.Task] = set()
        self.running = False
        self.active_run: str | None = None
        self.browser.on_step = self.on_step
        # A previous process cannot attest whether an interrupted submit succeeded.
        for run in db.query("SELECT * FROM automation_runs WHERE status IN ('running','submitting')"):
            db.execute(
                "UPDATE automation_runs SET status='interrupted',error=?,updated_at=? WHERE id=?",
                ("Interrupted automation detected; review before resuming", now(), run["id"]),
            )
            self.human_task(
                run["application_id"],
                "INTERRUPTED",
                "Interrupted automation detected. Check the browser/email before resuming; submission is uncertain.",
            )

    def human_task(self, application_id, kind, question):
        existing = self.db.one(
            "SELECT id FROM human_tasks WHERE application_id=? AND question=? AND status IN ('open','OPEN')",
            (application_id, question),
        )
        if not existing:
            self.db.execute(
                "INSERT INTO human_tasks(id,application_id,kind,question,status,created_at,updated_at) VALUES(?,?,?,?,'open',?,?)",
                (str(uuid4()), application_id, kind, question, now(), now()),
            )

    def paused(self):
        return bool(settings_for(self.db).get("automation_paused", True))

    def on_step(self, step):
        if not self.active_run:
            return
        self.db.execute(
            "INSERT INTO automation_steps(id,run_id,operation,target,confidence,details,created_at) VALUES(?,?,?,?,?,?,?)",
            (
                str(uuid4()),
                self.active_run,
                step.get("operation", "OBSERVE"),
                str(step.get("target", "")),
                step.get("confidence"),
                json.dumps(step),
                now(),
            ),
        )
        checkpoint = {
            "current_url": step.get("observation", {}).get("url"),
            "last_step": step,
            "snapshot_id": step.get("snapshot_id"),
        }
        self.db.execute(
            "UPDATE automation_runs SET checkpoint=?,updated_at=? WHERE id=?",
            (json.dumps(checkpoint), now(), self.active_run),
        )

    def pause(self):
        self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('automation_paused','true')")
        self.browser.pause()
        return {"paused": True}

    async def resume(self):
        self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('automation_paused','false')")
        await self.browser.resume()
        return {"paused": False}

    def application(self, application_id):
        application = self.db.one(
            "SELECT a.*,j.title,j.company,j.location,j.application_url,j.url,j.match_score,j.ats FROM applications a JOIN jobs j ON a.job_id=j.id WHERE a.id=?",
            (application_id,),
        )
        if not application:
            raise ValueError("Application not found")
        application["url"] = application["application_url"] or application["url"]
        return application

    def fingerprint(self, application_id):
        facts = self.db.query("SELECT id,value,verification_status,locked,updated_at FROM facts ORDER BY id")
        answers = self.db.query(
            "SELECT question,answer,verified FROM application_answers WHERE application_id=? ORDER BY id",
            (application_id,),
        )
        documents = self.db.query(
            "SELECT d.id,d.content_hash,d.approved FROM document_versions d JOIN application_documents a ON d.id=a.document_version_id WHERE a.application_id=? ORDER BY d.id",
            (application_id,),
        )
        return hashlib.sha256(json.dumps([facts, answers, documents], sort_keys=True).encode()).hexdigest()

    def rate_gate(self, application):
        settings = settings_for(self.db)
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        # Count submission attempts as well as confirmed outcomes, never only successes.
        today = self.db.one(
            "SELECT COUNT(DISTINCT application_id) AS n FROM application_events WHERE status IN ('APPLYING','APPLIED','CONFIRMED') AND created_at>=?",
            (date,),
        )["n"]
        if today >= int(settings.get("max_applications_day", 5)):
            raise ValueError("Daily application limit reached")
        company_count = self.db.one(
            "SELECT COUNT(DISTINCT e.application_id) AS n FROM application_events e JOIN applications a ON a.id=e.application_id JOIN jobs j ON j.id=a.job_id WHERE j.company=? AND e.status IN ('APPLYING','APPLIED','CONFIRMED') AND e.created_at>=?",
            (application["company"], date),
        )["n"]
        if company_count >= int(settings.get("max_applications_company", 2)):
            raise ValueError("Daily company application limit reached")
        latest = self.db.one(
            "SELECT created_at FROM application_events WHERE status='APPLYING' ORDER BY created_at DESC LIMIT 1"
        )
        if latest:
            elapsed = (datetime.now(timezone.utc) - datetime.fromisoformat(latest["created_at"])).total_seconds()
            if elapsed < int(settings.get("minimum_interval_seconds", 120)):
                raise ValueError("Minimum interval between submissions has not elapsed")

    def auto_gate(self, application, result):
        settings = settings_for(self.db)
        if not settings.get("auto_apply", False):
            raise ValueError("Enable autonomous applications explicitly in Settings")
        hostname = urlparse(application["url"]).hostname
        if hostname not in settings.get("allowed_domains", []):
            raise ValueError("Application domain is not in the automatic submission allowlist")
        if float(application["match_score"]) < float(settings.get("min_match", 85)):
            raise ValueError("Job match is below the automatic application threshold")
        job = self.db.one("SELECT match_details FROM jobs WHERE id=?", (application["job_id"],))
        if job and not json.loads(job["match_details"]).get("eligible", True):
            raise ValueError("The job does not satisfy the configured search profile rules")
        if result.get("questions"):
            raise ValueError("Unresolved questions require human input")
        sensitive = {
            "disability",
            "ethnicity",
            "gender",
            "criminal_record",
            "demographic",
            "medical",
            "legal_certification",
        }
        if result.get("sensitive") or any(
            x.get("sensitive") or x.get("kind") in sensitive for x in result.get("answers", [])
        ):
            raise ValueError("Sensitive questions require explicit human review")
        documents = self.db.query(
            "SELECT d.approved FROM document_versions d JOIN application_documents a ON a.document_version_id=d.id WHERE a.application_id=?",
            (application["id"],),
        )
        if not documents or any(not d["approved"] for d in documents):
            raise ValueError("Automatic submission requires explicitly approved documents")
        if any(not x.get("verified") for x in result.get("answers", [])):
            raise ValueError("Every automatic answer must be verified")
        if any(
            float(x.get("confidence", 1)) < float(settings.get("min_confidence", 0.9)) for x in result.get("steps", [])
        ):
            raise ValueError("Browser confidence is below the required threshold")
        if urlparse(result.get("current_url", application["url"])).hostname != hostname:
            raise ValueError("The application navigated to a different domain; review it first")

    def launch(self, application_id, resume=False, section_grant=None):
        application = self.application(application_id)
        if application["mode"].lower() == "manual":
            raise ValueError("Manual mode prepares documents only. Change this application to Review to fill its form.")
        if self.paused() and not application.get("section_consent"):
            raise ValueError("Automation is paused. Resume it before filling a form.")
        if application["status"] in (
            "APPLIED",
            "CONFIRMED",
            "RECRUITER_SCREEN",
            "ASSESSMENT",
            "TECHNICAL_TEST",
            "INTERVIEW",
            "FINAL_INTERVIEW",
            "OFFER",
            "REJECTED",
            "WITHDRAWN",
        ):
            raise ValueError("This application is already submitted or closed; duplicate execution is blocked")
        if self.running or self.lock.locked():
            raise ValueError("A browser application is already running. Pause or finish it first.")
        uncertain = self.db.one(
            "SELECT id FROM automation_runs WHERE application_id=? AND status IN ('submitting','unconfirmed','interrupted')",
            (application_id,),
        )
        if uncertain:
            raise ValueError("A previous run has uncertain submission state. Reconcile it manually before any retry.")
        self.running = True
        run_id = str(uuid4())
        self.db.execute(
            "INSERT INTO automation_runs(id,application_id,status,engine,checkpoint,created_at,updated_at) VALUES(?,?,'running',?,'{}',?,?)",
            (run_id, application_id, settings_for(self.db).get("browser_engine", "deterministic"), now(), now()),
        )
        task = asyncio.create_task(self._fill(run_id, application, resume, section_grant))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return {"run_id": run_id, "status": "running"}

    async def _fill(self, run_id, application, resume, section_grant=None):
        try:
            async with self.lock:
                await self.prompt.close()
                self.active_run = run_id
                if self.paused() and not application.get("section_consent"):
                    raise ValueError("Automation was paused before this run started")
                if resume or application.get("section_consent"):
                    await self.browser.resume()
                self.db.event(application["id"], "PREPARING", "Browser form preparation started", origin="automation")
                facts = self.db.query("SELECT * FROM facts WHERE verification_status='verified' OR locked=1")
                from .application_profile import application_knowledge
                facts, skipped = application_knowledge(self.db, application, facts)
                source_fingerprint = self.fingerprint(application["id"])
                evidence_at = now()
                documents = self.db.query(
                    "SELECT d.*,a.kind FROM document_versions d JOIN application_documents a ON d.id=a.document_version_id WHERE a.application_id=?",
                    (application["id"],),
                )
                for doc in documents:
                    from .documents import document_path

                    document_path(self.db, self.data_dir, doc["id"])
                    path = Path(doc["path"]).resolve()
                    if not path.is_relative_to((self.data_dir / "documents").resolve()) or not path.is_file():
                        raise ValueError("Selected document is missing or outside the document vault")
                config = settings_for(self.db)
                config["resume_existing"] = resume
                config["assisted_autofill"] = bool(application.get("section_consent") and application.get("assisted_autofill"))
                config["section_consent"] = bool(application.get("section_consent") and not config["assisted_autofill"])
                config["skip_optional_unknown"] = bool(application.get("section_consent"))
                config["inline_questions"] = bool(config.get("browser_inline_questions", True) and config["assisted_autofill"])
                if config["inline_questions"]:
                    config["skip_optional_unknown"] = False
                config["_skipped_questions"] = skipped
                config["_section_grant"] = section_grant or []
                config["has_filled_sections"] = bool(self.db.one(
                    "SELECT s.id FROM automation_steps s JOIN automation_runs r ON r.id=s.run_id WHERE r.application_id=? AND s.operation IN ('TYPE_TEXT','SELECT','UPLOAD','CHECK') LIMIT 1",
                    (application["id"],)))
                # An explicit answer authorizes only this question in this application.
                # Retain the verified manual fact as the browser's evidence source.
                from .automation.answers import normalize

                policies = copy.deepcopy(config.get("sensitive_policies", {}))
                question_policies = policies.setdefault("questions", {})
                trusted_by_id = {fact["id"]: fact for fact in facts}
                for answer in self.db.query(
                    "SELECT question,answer,fact_ids FROM application_answers WHERE application_id=? AND verified=1 ORDER BY created_at",
                    (application["id"],),
                ):
                    for fact_id in json.loads(answer["fact_ids"]):
                        fact = trusted_by_id.get(fact_id)
                        if (
                            fact
                            and fact["category"] == "saved_answer"
                            and fact["source"].startswith("manual_answer:")
                            and fact["value"] == answer["answer"]
                            and normalize(fact["key"]) == normalize(answer["question"])
                        ):
                            question_policies[normalize(answer["question"])] = {
                                "action": "saved_answer", "fact_id": fact_id
                            }
                config["sensitive_policies"] = policies
                from .security import SecretStore
                from .usage import decision_settings

                config = decision_settings(self.db, config, SecretStore())
                result = await self.browser.fill_application(
                    application, facts, documents, application["mode"].upper(), config
                )
                used = {identifier for answer in result.get("answers", []) for identifier in answer.get("fact_ids", [])}
                result["facts_snapshot"] = [fact for fact in facts if fact["id"] in used]
                result["evidence_at"] = evidence_at
                if source_fingerprint != self.fingerprint(application["id"]):
                    result["status"] = "human_required"
                    result.setdefault("questions", []).append(
                        {
                            "kind": "EVIDENCE_CHANGED",
                            "question": "Candidate facts, answers or documents changed while filling. Resume to refill and review the updated evidence.",
                        }
                    )
                    for answer in result.get("answers", []):
                        answer["verified"] = False
                self.persist_result(run_id, application["id"], result)
                if config["inline_questions"] and result.get("status") == "human_required" and not any(q.get('kind') == 'EVIDENCE_CHANGED' for q in result.get('questions', [])):
                    await self.show_browser_question(run_id, application, result, facts)
                if (
                    result.get("status") in ("needs_review", "ready_for_review")
                    and application["mode"].lower() == "auto"
                    and not application.get("section_consent")
                ):
                    try:
                        self.auto_gate(application, result)
                        await self._submit(run_id, application, result)
                    except ValueError as exc:
                        self.human_task(application["id"], "AUTO_GATE", str(exc))
        except Exception as exc:
            error = str(exc)[:1000]
            current = self.db.one("SELECT status FROM automation_runs WHERE id=?", (run_id,))
            state = "unconfirmed" if current and current["status"] in ("submitting", "unconfirmed") else "error"
            self.db.execute(
                "UPDATE automation_runs SET status=?,error=?,updated_at=? WHERE id=?", (state, error, now(), run_id)
            )
            self.db.event(application["id"], "ERROR", error, origin="automation")
            self.human_task(application["id"], "AUTOMATION_ERROR", error)
        finally:
            self.running = False
            self.active_run = None

    async def show_browser_question(self, run_id, application, result, facts):
        from .application_profile import question_field, profile, draft_fact_ids, remember_answer
        from .automation.answers import classify_question, SENSITIVE
        question = next((q for q in result.get('questions', []) if q.get('target') and q.get('kind') != 'document'), None)
        question = question or next(iter(result.get('questions', [])), None)
        if not question or not self.browser.page or self.browser.page.is_closed():
            return
        spec = question_field(question['question'], application)
        suggested = next((f for f in profile(self.db)['fields'] if spec and f['key'] == spec['key']), None)
        options = [o['label'] for o in question.get('options', []) if o.get('label') and not o.get('disabled') and o.get('value') != '']
        if not options and spec:
            options = spec['options']
        kind = classify_question(question['question'])
        can_draft = (question.get('multiline') or kind == 'free_text') and kind not in {*SENSITIVE, 'work_authorization', 'salary'}
        safe_reuse = kind != 'legal_certification' and (kind not in {'work_authorization', 'salary'} or bool(spec and spec.get('scope')))
        answer = question.get('current_value') or (suggested['value'] if suggested else '')
        manual = not question.get('target') or question.get('kind') == 'document'
        if manual:
            can_draft = safe_reuse = False
        # The original question remains visible alongside the browser-owned guide.
        snapshot, refs = self.browser._snapshots.get(result['snapshot_id'], ({}, {}))
        ref = refs.get(question.get('target'))
        if ref:
            frame, node_id, document_id = ref
            await frame.evaluate("([id,doc]) => { const s=window.__meridianObservedControlsV1; if(s?.documentId===doc) s.nodes.get(id)?.scrollIntoView({block:'center'}); }", [node_id, document_id])

        async def handle(payload):
            latest = self.db.one('SELECT id FROM automation_runs WHERE application_id=? ORDER BY created_at DESC LIMIT 1', (application['id'],))
            if not latest or latest['id'] != run_id or self.running or self.lock.locked():
                raise ValueError('This browser question has expired')
            action = payload['action']
            if action == 'pause':
                self.browser.pause()
                await self.prompt.close()
                self.db.execute("UPDATE automation_runs SET status='human_required',updated_at=? WHERE id=?", (now(), run_id))
                return
            if action == 'rescan':
                await self.prompt.close()
                self.launch(application['id'], resume=True)
                return
            self.browser._check_pause()
            if manual:
                raise ValueError('Complete this step in the form, then choose Recheck form')
            await self.browser._validate_snapshot(result['snapshot_id'])
            if action == 'draft':
                if not can_draft or not self.text_service:
                    raise ValueError('This question needs your own answer')
                draft = await self.text_service.draft(settings_for(self.db), question['question'], application, draft_fact_ids(facts, question['question']))
                return {'answer': draft['answer'], 'message': 'AI draft — review and edit before saving. Based on '+str(len(draft['facts_used']))+' verified facts. Nothing has been filled yet.'}
            if action == 'skip' and question.get('required'):
                raise ValueError('This field is required by the employer')
            value = payload['answer'].strip()
            if action == 'answer' and not value:
                raise ValueError('Enter an answer first')
            if action == 'answer' and options and value not in options:
                raise ValueError('Choose one of the available answers')
            if action == 'answer' and question.get('type') == 'number':
                import math
                try:
                    if not math.isfinite(float(value)):
                        raise ValueError()
                except ValueError:
                    raise ValueError('Enter a finite number') from None
            remember_answer(self.db, application, question['question'], value if action == 'answer' else '',
                            reusable=payload['reusable'] and safe_reuse, skip=action == 'skip')
            await self.prompt.close()
            self.launch(application['id'], resume=True)

        await self.prompt.show(self.browser.page, {
            'question': question['question'], 'company': application['company'], 'required': question.get('required', False),
            'remaining': len(result['questions']), 'options': options, 'answer': answer,
            'multiline': question.get('multiline') or kind == 'free_text' or bool(spec and spec['multiline']),
            'type': question.get('type'), 'can_draft': bool(can_draft), 'can_reuse': safe_reuse,
            'description': question.get('description'),
            'manual': manual,
            'reuse_label': 'Remember for matching questions' + (' ('+spec['scope']+' only)' if spec and spec.get('scope') else ' in future applications'),
            'message': 'Suggested answer — confirm or edit it.' if suggested and suggested['state'] == 'suggested' and answer else '',
        }, handle)
        result['browser_question'] = True
        self.db.execute("UPDATE automation_runs SET status='browser_question',checkpoint=?,updated_at=? WHERE id=?", (json.dumps(result), now(), run_id))

    def persist_result(self, run_id, application_id, result):
        for answer in result.get("answers", []):
            question = answer.get("question", answer.get("label", "Field"))
            self.db.execute(
                "DELETE FROM application_answers WHERE application_id=? AND question=?", (application_id, question)
            )
            self.db.execute(
                "INSERT INTO application_answers(id,application_id,question,answer,fact_ids,verified,created_at) VALUES(?,?,?,?,?,?,?)",
                (
                    str(uuid4()),
                    application_id,
                    question,
                    str(answer.get("answer", "")),
                    json.dumps(answer.get("fact_ids", [])),
                    int(bool(answer.get("verified", False))),
                    result.get("evidence_at", now()),
                ),
            )
        # Steps are persisted immediately by on_step, including before a crash.
        for question in result.get("questions", []):
            self.human_task(
                application_id,
                question.get("kind", "UNKNOWN_QUESTION"),
                question.get("question", "Human input required"),
            )
        result["review_fingerprint"] = self.fingerprint(application_id)
        self.db.execute(
            "UPDATE automation_runs SET status=?,checkpoint=?,updated_at=? WHERE id=?",
            (result.get("status", "human_required"), json.dumps(result), now(), run_id),
        )
        confirmed = result.get("status") == "confirmed"
        self.db.event(
            application_id,
            "CONFIRMED" if confirmed else "NEEDS_REVIEW",
            "Submission confirmed" if confirmed else "Form preparation paused for review",
            origin="automation",
        )
        if confirmed:
            self.db.execute(
                "UPDATE human_tasks SET status='resolved',answer='Independent submission confirmation observed',updated_at=? WHERE application_id=? AND kind IN ('PREPARATION_REVIEW','AUTO_GATE','SUBMISSION_UNCONFIRMED','INTERRUPTED','AUTOMATION_ERROR')",
                (now(), application_id),
            )
        quality = 100 if not result.get("questions") else max(0, 100 - 15 * len(result["questions"]))
        self.db.execute("UPDATE applications SET quality_score=? WHERE id=?", (quality, application_id))

    async def approve_section(self, application_id, snapshot_id):
        if self.running or self.lock.locked():
            raise ValueError("Browser is busy")
        async with self.lock:
            application = self.application(application_id)
            run = self.db.one("SELECT * FROM automation_runs WHERE application_id=? ORDER BY created_at DESC LIMIT 1", (application_id,))
            if not application.get("section_consent") or not run or run["status"] != "section_review":
                raise ValueError("Inspect the current section before approving it")
            result = json.loads(run["checkpoint"])
            if result.get("snapshot_id") != snapshot_id or result.get("review_fingerprint") != self.fingerprint(application_id):
                raise ValueError("The section or candidate evidence changed. Inspect it again.")
            grant = await self.browser.authorize_section(application_id, snapshot_id)
            self.db.execute("UPDATE automation_runs SET status='section_approved',updated_at=? WHERE id=?", (now(), run["id"]))
            self.db.event(application_id, "PREPARING", "Approved section: " + result["section"]["title"], "manual")
        return self.launch(application_id, resume=True, section_grant=grant)

    async def approve(self, application_id):
        if self.running or self.lock.locked():
            raise ValueError("Browser is busy")
        async with self.lock:
            application = self.application(application_id)
            if application["mode"] == "manual":
                raise ValueError("Manual mode does not permit browser submission")
            run = self.db.one(
                "SELECT * FROM automation_runs WHERE application_id=? ORDER BY created_at DESC LIMIT 1",
                (application_id,),
            )
            if not run or run["status"] not in ("needs_review", "ready_for_review"):
                raise ValueError("Prepare and review the current form before approval")
            result = json.loads(run["checkpoint"])
            if result.get("questions"):
                raise ValueError("Resolve questions and resume the application before submitting")
            if result.get("review_fingerprint") != self.fingerprint(application_id):
                raise ValueError("Candidate facts, answers or documents changed. Refill and review again.")
            return await self._submit(run["id"], application, result)

    async def _submit(self, run_id, application, result):
        if self.paused() and not application.get("section_consent"):
            raise ValueError("Automation is paused")
        from .documents import document_path

        for document in self.db.query(
            "SELECT document_version_id FROM application_documents WHERE application_id=?", (application["id"],)
        ):
            document_path(self.db, self.data_dir, document["document_version_id"])
        self.rate_gate(application)
        if self.db.one(
            "SELECT id FROM application_events WHERE application_id=? AND status IN ('APPLIED','CONFIRMED') LIMIT 1",
            (application["id"],),
        ):
            raise ValueError("Previous submission evidence exists; duplicate submission blocked")
        self.db.execute("UPDATE automation_runs SET status='submitting',updated_at=? WHERE id=?", (now(), run_id))
        self.active_run = run_id
        self.db.event(
            application["id"], "APPLYING", "Submission approved; attempting one submission", origin="automation"
        )
        try:
            submitted = await self.browser.submit(result.get("snapshot_id"), settings=settings_for(self.db))
            submitted["evidence_at"] = result.get("evidence_at", now())
            submitted["facts_snapshot"] = result.get("facts_snapshot", [])
            self.persist_result(run_id, application["id"], submitted)
            if submitted.get("status") != "confirmed":
                self.db.execute(
                    "UPDATE automation_runs SET status='unconfirmed',updated_at=? WHERE id=?", (now(), run_id)
                )
                self.human_task(
                    application["id"],
                    "SUBMISSION_UNCONFIRMED",
                    "Submission has no independent confirmation. Check the page and email before retrying.",
                )
            return submitted
        except Exception:
            self.db.execute(
                "UPDATE automation_runs SET status='unconfirmed',error='Submission uncertain; manual reconciliation required',updated_at=? WHERE id=?",
                (now(), run_id),
            )
            self.human_task(
                application["id"],
                "SUBMISSION_UNCONFIRMED",
                "Submission interrupted. Review the browser/email; do not retry blindly.",
            )
            raise

    def status(self):
        return {
            "paused": self.paused(),
            "running": self.running,
            "browser": self.browser.status(),
            "runs": [
                decode_row(row)
                for row in self.db.query(
                    "SELECT r.*,j.company,j.title FROM automation_runs r JOIN applications a ON a.id=r.application_id JOIN jobs j ON j.id=a.job_id ORDER BY r.created_at DESC LIMIT 100"
                )
            ],
            "settings": settings_for(self.db),
        }

    async def close(self):
        await self.prompt.close()
        for task in list(self.prompt.tasks):
            task.cancel()
        if self.prompt.tasks:
            await asyncio.gather(*self.prompt.tasks, return_exceptions=True)
        self.browser.pause()
        for task in self.tasks:
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        await self.browser.close()
