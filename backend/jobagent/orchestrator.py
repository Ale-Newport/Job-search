from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from .db import decode_row, now
from .security import private_checkpoint


def settings_for(db):
    from .core import get_settings

    return get_settings(db)


class Orchestrator:
    preparation_timeout = 180
    submission_timeout = 60
    review_states = frozenset({'needs_review', 'ready_for_review', 'human_required', 'browser_question',
                               'section_review', 'section_approved'})

    def __init__(self, db, data_dir: Path, *, application_scope=None, recover=True):
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
        self._run_tasks: dict[str, asyncio.Task] = {}
        self.browser.on_step = self.on_step
        self.browser.on_disconnect = self.on_browser_disconnect
        # A previous process cannot attest whether an interrupted submit succeeded.
        for run in (db.query("SELECT * FROM automation_runs WHERE status NOT IN ('confirmed','reconciled','error','blocked','unconfirmed')") if recover else []):
            if application_scope and run['application_id'] != application_scope:
                continue
            checkpoint = json.loads(run['checkpoint'])
            if run['status'] == 'interrupted':
                # Older versions marked preparation interruptions as uncertain too.
                attempted = db.one("SELECT id FROM application_events WHERE application_id=? AND status='APPLYING' AND created_at>=? LIMIT 1",
                                   (run['application_id'], run['created_at']))
                if attempted:
                    continue
            elif run['status'] not in {'running', 'submitting'} and not (
                run['status'] in self.review_states and checkpoint.get('snapshot_id')
            ):
                continue
            self._fail_run(run['id'], 'The app restarted and the previous browser session expired. Start Auto Apply again to reopen and inspect the form.')
        # Older versions recorded failed runs as NEEDS_REVIEW in application
        # tracking. Correct only the latest failure and keep the original audit.
        for run in db.query("SELECT r.* FROM automation_runs r JOIN applications a ON a.id=r.application_id "
                            "WHERE a.status IN ('PREPARING','NEEDS_REVIEW') AND r.status IN ('error','blocked') "
                            "AND r.id=(SELECT latest.id FROM automation_runs latest WHERE latest.application_id=r.application_id ORDER BY latest.created_at DESC LIMIT 1)"):
            if application_scope and run['application_id'] != application_scope:
                continue
            checkpoint = json.loads(run['checkpoint'])
            error = run['error'] or checkpoint.get('error') or 'The previous form preparation failed. Start Auto Apply again to retry.'
            self._event(run['application_id'], 'ERROR', 'Recovered previous preparation failure: ' + str(error)[:900])

    def _fail_run(self, run_id, error):
        run = self.db.one('SELECT * FROM automation_runs WHERE id=?', (run_id,))
        if not run or run['status'] in {'confirmed', 'reconciled', 'error', 'blocked', 'unconfirmed'}:
            return run['status'] if run else 'error'
        uncertain = run['status'] in {'submitting', 'unconfirmed'}
        state = 'unconfirmed' if uncertain else 'error'
        if uncertain:
            error = 'Submission outcome is uncertain. Check the employer page and confirmation email before retrying. ' + error
        checkpoint = json.loads(run['checkpoint'])
        checkpoint.update(status=state, error=error[:1000], snapshot_id=None)
        if getattr(self.browser, '_active_application_id', None) == run['application_id']:
            self.browser._invalidate_approvals()
        self.db.execute('UPDATE automation_runs SET status=?,checkpoint=?,error=?,updated_at=? WHERE id=?',
                        (state, json.dumps(private_checkpoint(checkpoint)), error[:1000], now(), run_id))
        self._event(run['application_id'], 'ERROR', error[:1000])
        self.human_task(run['application_id'], 'SUBMISSION_UNCONFIRMED' if uncertain else 'AUTOMATION_ERROR', error[:1000])
        return state

    def _event(self, application_id, status, message):
        from .documents import SUBMITTED_STATUSES
        # Email/manual tracking can advance while a browser await is in flight.
        # Keep the audit event without rolling back that newer outcome.
        with self.db.transaction() as conn:
            current = conn.execute('SELECT status FROM applications WHERE id=?', (application_id,)).fetchone()
            self.db.event(application_id, status, message, origin='automation', conn=conn)
            if current and current['status'] in SUBMITTED_STATUSES - {'APPLYING'} and not (
                status == 'CONFIRMED' and current['status'] == 'APPLIED'
            ):
                conn.execute('UPDATE applications SET status=? WHERE id=?', (current['status'], application_id))

    def on_browser_disconnect(self, reason):
        application_id = getattr(self.browser, '_active_application_id', None)
        run = self.db.one('SELECT * FROM automation_runs WHERE id=?', (self.active_run,)) if self.active_run else None
        if not run and application_id:
            run = self.db.one('SELECT * FROM automation_runs WHERE application_id=? ORDER BY created_at DESC LIMIT 1', (application_id,))
        if not run or run['status'] not in self.review_states | {'running', 'submitting'}:
            return
        self._fail_run(run['id'], f'{reason} Start Auto Apply again to reopen and inspect the form.')
        task = self._run_tasks.get(run['id'])
        if task and not task.done():
            task.cancel()

    def reconcile_browser_state(self):
        """Polling also detects a lost idle session when no close event arrived."""
        if not self.running and not self.lock.locked() and getattr(self.browser, '_active_application_id', None):
            page = self.browser.page
            if not page or page.is_closed():
                self.on_browser_disconnect('The application browser was closed or disconnected.')

    async def cancel(self, application_id):
        self.application(application_id)
        run = self.db.one('SELECT * FROM automation_runs WHERE application_id=? ORDER BY created_at DESC LIMIT 1', (application_id,))
        if not run or run['status'] not in self.review_states | {'running', 'submitting'}:
            raise ValueError('There is no active form preparation to cancel')
        if self.active_run and self.active_run != run['id']:
            raise ValueError('A different application is using the browser')
        browser_application = getattr(self.browser, '_active_application_id', None)
        if browser_application and browser_application != application_id:
            raise ValueError('A different application is using the browser')
        self.browser.pause()
        invalidate = getattr(self.browser, '_invalidate_approvals', None)
        if invalidate:
            invalidate()
        state = self._fail_run(run['id'], 'Form preparation was stopped. Start Auto Apply again when you are ready.')
        task = self._run_tasks.get(run['id'])
        if task and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        try:
            async with asyncio.timeout(5):
                await self.prompt.close()
        except TimeoutError:
            pass
        return {'run_id': run['id'], 'status': state}

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
                json.dumps(private_checkpoint({k: step.get(k) for k in ('operation', 'target', 'confidence', 'label', 'url', 'executed', 'form_state', 'document_version_id', 'upload_evidence')})),
                now(),
            ),
        )
        existing = self.db.one('SELECT checkpoint FROM automation_runs WHERE id=?', (self.active_run,))
        checkpoint = json.loads(existing['checkpoint']) if existing else {}
        snapshot = step.get('observation', {})
        # Keep semantic progress, never browser cookies, tokens or DOM handles.
        if step.get('document_version_id'):
            self.db.execute('INSERT INTO document_uploads VALUES(?,?,?,?,?,?,?)',
                            (str(uuid4()), self.active_run, step['document_version_id'], step.get('label') or '',
                             'ATTACHED', json.dumps(step.get('upload_evidence') or {}), now()))
        checkpoint.update({
            "current_url": step.get("observation", {}).get("url"),
            "last_step": {k: step.get(k) for k in ('operation', 'label', 'executed', 'url', 'document_version_id', 'upload_evidence')},
            "snapshot_id": step.get("snapshot_id"),
            "form_state": step.get('form_state', 'FILL_FIELDS'),
            "step_identity": hashlib.sha256(json.dumps([snapshot.get('url'), [(e.get('label'), e.get('type')) for e in snapshot.get('elements', [])]], sort_keys=True).encode()).hexdigest(),
            "answers": step.get('answers', checkpoint.get('answers', [])),
            "completed_steps": step.get('completed_steps', checkpoint.get('completed_steps', [])),
            "documents": step.get('documents', checkpoint.get('documents', [])),
        })
        self.db.execute(
            "UPDATE automation_runs SET checkpoint=?,updated_at=? WHERE id=?",
            (json.dumps(private_checkpoint(checkpoint)), now(), self.active_run),
        )

    def pause(self):
        self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('automation_paused','true')")
        self.browser.pause()
        if self.active_run:
            self._fail_run(self.active_run, 'Automation was paused. Resume automation and start Auto Apply again when you are ready.')
            task = self._run_tasks.get(self.active_run)
            if task and not task.done():
                task.cancel()
        return {"paused": True}

    async def resume(self):
        self.db.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('automation_paused','false')")
        await self.browser.resume()
        return {"paused": False}

    def application(self, application_id):
        application = self.db.one(
            "SELECT a.*,j.title,j.company,j.location,j.application_url,j.url,j.url AS job_url,j.description,"
            "j.skills,j.metadata,j.source,j.updated_at AS job_updated_at,j.match_score,j.ats "
            "FROM applications a JOIN jobs j ON a.job_id=j.id WHERE a.id=?",
            (application_id,),
        )
        if not application:
            raise ValueError("Application not found")
        application = decode_row(application)
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
        from .intelligence.store import Knowledge
        knowledge_version = Knowledge(self.db).version
        return hashlib.sha256(json.dumps([facts, answers, documents, knowledge_version], sort_keys=True).encode()).hexdigest()

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
        previous_application = getattr(self.browser, '_active_application_id', None)
        if previous_application and previous_application != application_id:
            previous = self.db.one('SELECT * FROM automation_runs WHERE application_id=? ORDER BY created_at DESC LIMIT 1', (previous_application,))
            if previous and previous['status'] in self.review_states and json.loads(previous['checkpoint']).get('snapshot_id'):
                self._fail_run(previous['id'], 'Another application is now using the browser. Start Auto Apply again to reopen and inspect this form.')
        run_id = str(uuid4())
        self.db.execute(
            "INSERT INTO automation_runs(id,application_id,status,engine,checkpoint,created_at,updated_at) VALUES(?,?,'running',?,'{}',?,?)",
            (run_id, application_id, settings_for(self.db).get("browser_engine", "deterministic"), now(), now()),
        )
        self.running = True
        self.active_run = run_id
        task = asyncio.create_task(self._fill(run_id, application, resume, section_grant))
        self.tasks.add(task)
        self._run_tasks[run_id] = task
        def finished(task):
            self.tasks.discard(task)
            self._run_tasks.pop(run_id, None)
            if self.active_run == run_id:
                self.running = False
                self.active_run = None
        task.add_done_callback(finished)
        return {"run_id": run_id, "status": "running"}

    async def _fill(self, run_id, application, resume, section_grant=None):
        try:
            async with self.lock, asyncio.timeout(self.preparation_timeout):
                await self.prompt.close()
                self.active_run = run_id
                if self.paused() and not application.get("section_consent"):
                    raise ValueError("Automation was paused before this run started")
                # Starting a run is explicit permission to continue after a local stop.
                # The global pause gate above still applies.
                if resume or application.get("section_consent") or getattr(self.browser, '_paused', False):
                    await self.browser.resume()
                self._event(application["id"], "PREPARING", "Browser form preparation started")
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
                saved_run = self.db.one('SELECT checkpoint FROM automation_runs WHERE id=?', (run_id,))
                config['_resume_completed_steps'] = json.loads(saved_run['checkpoint']).get('completed_steps',[]) if saved_run else []
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

                config = await asyncio.to_thread(decision_settings, self.db, config, SecretStore())
                from .automation.job_context import prepare_job_context
                config['_job_context'] = await prepare_job_context(application)
                async def generate_required(kind):
                    from .documents import generate_required_draft
                    return await asyncio.to_thread(generate_required_draft, self.db, self.data_dir, application['job_id'], kind)
                config['_generate_required_document'] = generate_required
                if config['assisted_autofill'] and self.text_service:
                    config['_reason_answer'] = self.field_reasoner(config['_job_context'], facts, config)
                from .intelligence.engine import CandidateEngine
                intelligence = CandidateEngine(self.db, settings=config)
                disclosure = intelligence.knowledge.get_policy('personal_disclosures', application)
                if disclosure:
                    config['sensitive_policies']['personal_disclosures'] = disclosure['value']
                if intelligence.enabled:
                    config['_candidate_engine'] = intelligence
                result = await self.browser.fill_application(
                    application, facts, documents, application["mode"].upper(), config
                )
                annotate = getattr(self.browser, 'mark_inferred_answers', None)
                if annotate:
                    await annotate(result)
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
        except asyncio.CancelledError:
            self._fail_run(run_id, 'Form preparation was interrupted. Start Auto Apply again to reopen and inspect the form.')
            raise
        except TimeoutError:
            self._fail_run(run_id, 'Form preparation exceeded the time limit and was stopped. Start Auto Apply again to retry.')
        except Exception as exc:
            self._fail_run(run_id, str(exc)[:1000] or type(exc).__name__)
        finally:
            if self.active_run == run_id:
                self.running = False
                self.active_run = None

    def field_reasoner(self, application, facts, config):
        from .automation.answers import classify_question, SENSITIVE, normalize, option_for
        from .application_profile import draft_fact_ids
        from .automation.reasoning import derived, extractive_narrative
        import re

        async def reason(question, element):
            kind = classify_question(question)
            # Missing sensitive facts and commitments cannot be made true by an LLM.
            if kind in {*SENSITIVE, 'work_authorization', 'salary', 'contact', 'identity'} or re.search(
                r'passport|national insurance|social security|password|bank|\bpin\b|\botp\b|signatur|'
                r'commit|willing|relocat|travel|hybrid|office attend|notice period|start date|available|'
                r'current (?:employer|company|job)|\baddress\b|\bphone\b|\bemail\b', normalize(question)
            ):
                return None
            if element.get('type') in {'date', 'month', 'email', 'tel', 'url', 'file', 'password'}:
                return None
            ids = draft_fact_ids(facts, question)
            if not ids:
                return None
            choices = [o['label'] for o in element.get('options', []) if o.get('value') and not o.get('disabled')]
            def fallback():
                result = extractive_narrative(question, facts) if element.get('tag') == 'textarea' and not choices else None
                if result and element.get('max_length') and len(result['answer']) > element['max_length']:
                    return None
                return result
            try:
                draft = await self.text_service.infer_field(config, question, application, ids, {
                    'type': element.get('type'), 'multiline': element.get('tag') == 'textarea',
                    'options': choices, 'description': element.get('description', ''),
                    'max_length': element.get('max_length'),
                })
                if not draft:
                    return fallback()
                value = draft['answer']
                if choices and not option_for(value, [{'label': c, 'value': c} for c in choices]):
                    return None
                if element.get('type') == 'number' and not re.fullmatch(r'\d+(?:\.\d+)?', value):
                    return None
                if element.get('max_length') and len(value) > element['max_length']:
                    return None
                evidence = [f for f in facts if f['id'] in draft['facts_used']]
                return derived(question, value, evidence, 'AI inference from your general professional profile. Review wording and assumptions before submitting.', draft['confidence'])
            except (ValueError, TimeoutError):
                return fallback()
            except Exception:
                # A provider outage must not prevent deterministic fields being filled.
                return fallback()
        return reason

    async def show_browser_question(self, run_id, application, result, facts):
        from .application_profile import question_field, profile, draft_fact_ids, remember_answer
        from .automation.answers import classify_question, SENSITIVE
        from .automation.disclosures import personal_disclosure_kind
        eligible = [q for q in result.get('questions', []) if not q.get('skip_inline_question')]
        question = next((q for q in eligible if q.get('target') and q.get('kind') != 'document'), None)
        question = question or next(iter(eligible), None)
        question = question or next((q for q in result.get('questions', []) if q.get('skip_inline_question')), None)
        read_only_policy = bool(question and question.get('skip_inline_question'))
        if not question or not self.browser.page or self.browser.page.is_closed():
            return
        spec = None if read_only_policy else question_field(question['question'], application, question)
        suggested = None if read_only_policy else next((f for f in profile(self.db)['fields'] if spec and f['key'] == spec['key']), None)
        options = [o['label'] for o in question.get('options', []) if o.get('label') and not o.get('disabled') and o.get('value') != '']
        if not options and spec:
            options = spec['options']
        kind = classify_question(question['question'])
        can_draft = (question.get('multiline') or kind == 'free_text') and kind not in {*SENSITIVE, 'work_authorization', 'salary'}
        safe_reuse = kind != 'legal_certification' and (kind not in {'work_authorization', 'salary'} or bool(spec and spec.get('scope')))
        if personal_disclosure_kind(question['question'], question) or (spec and spec.get('sensitive')):
            can_draft = False
            safe_reuse = bool(spec)
        answer = question.get('current_value') or (suggested['value'] if suggested else '')
        manual = read_only_policy or not question.get('target') or question.get('kind') == 'document'
        if read_only_policy:
            options, answer = [], ''
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
            if read_only_policy:
                raise ValueError('Your personal-disclosure preference keeps this question blank. Use Recheck form or Pause.')
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
                            reusable=payload['reusable'] and safe_reuse, skip=action == 'skip', element=question)
            await self.prompt.close()
            self.launch(application['id'], resume=True)

        await self.prompt.show(self.browser.page, {
            'question': question['question'], 'company': application['company'], 'required': question.get('required', False),
            'remaining': len(result['questions']), 'options': options, 'answer': answer,
            'multiline': question.get('multiline') or kind == 'free_text' or bool(spec and spec['multiline']),
            'type': question.get('type'), 'can_draft': bool(can_draft), 'can_reuse': safe_reuse,
            'description': (question.get('reason') or 'Your saved personal-disclosure preference keeps this question blank.') if read_only_policy else question.get('description'),
            'manual': manual, 'read_only_policy': read_only_policy,
            'reuse_label': 'Remember for matching questions' + (' ('+spec['scope']+' only)' if spec and spec.get('scope') else ' in future applications'),
            'message': 'Suggested answer — confirm or edit it.' if suggested and suggested['state'] == 'suggested' and answer else '',
        }, handle)
        result['browser_question'] = True
        self.db.execute("UPDATE automation_runs SET status='browser_question',checkpoint=?,updated_at=? WHERE id=?", (json.dumps(private_checkpoint(result)), now(), run_id))

    def persist_result(self, run_id, application_id, result):
        for omitted in result.get('omitted', []):
            self.db.execute('DELETE FROM application_answers WHERE application_id=? AND question=?', (application_id, omitted['question']))
            self.db.execute("UPDATE human_tasks SET status='resolved',answer=?,updated_at=? WHERE application_id=? AND question=?",
                            ('Not applicable: '+omitted['reason'], now(), application_id, omitted['question']))
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
            self.db.execute(
                "UPDATE human_tasks SET status='resolved',answer=?,updated_at=? WHERE application_id=? AND question=? AND status='open'",
                ('Filled from profile; review inferred wording before submission' if answer.get('inferred') else str(answer.get('answer', '')),
                 now(), application_id, question),
            )
        # Steps are persisted immediately by on_step, including before a crash.
        for question in result.get("questions", []):
            self.human_task(
                application_id,
                question.get("kind", "UNKNOWN_QUESTION"),
                question.get("question", "Human input required"),
            )
        result["review_fingerprint"] = self.fingerprint(application_id)
        failed = result.get('status') in {'error', 'blocked', 'unconfirmed'}
        if failed:
            result['status'] = 'unconfirmed' if result.get('status') == 'unconfirmed' else 'error'
            result['snapshot_id'] = None
            result['error'] = result.get('error') or 'Form preparation could not finish. Start Auto Apply again to retry.'
            self.human_task(application_id, 'SUBMISSION_UNCONFIRMED' if result['status'] == 'unconfirmed' else 'AUTOMATION_ERROR', result['error'])
        else:
            self.db.execute("UPDATE human_tasks SET status='resolved',answer='A new preparation completed; inspect its current result',updated_at=? WHERE application_id=? AND kind IN ('AUTOMATION_ERROR','INTERRUPTED') AND status IN ('open','OPEN')",
                            (now(), application_id))
        self.db.execute(
            "UPDATE automation_runs SET status=?,checkpoint=?,error=?,updated_at=? WHERE id=?",
            (result.get("status", "human_required"), json.dumps(private_checkpoint(result)), result.get('error'), now(), run_id),
        )
        confirmed = result.get("status") == "confirmed"
        self._event(
            application_id,
            "CONFIRMED" if confirmed else "ERROR" if failed else "NEEDS_REVIEW",
            "Submission confirmed" if confirmed else result['error'] if failed else "Form preparation paused for review",
        )
        if confirmed:
            self.db.execute(
                "UPDATE human_tasks SET status='resolved',answer='Independent submission confirmation observed',updated_at=? WHERE application_id=? AND kind IN ('PREPARATION_REVIEW','AUTO_GATE','SUBMISSION_UNCONFIRMED','INTERRUPTED','AUTOMATION_ERROR')",
                (now(), application_id),
            )
        quality = 0 if failed else 100 if not result.get("questions") else max(0, 100 - 15 * len(result["questions"]))
        self.db.execute("UPDATE applications SET quality_score=? WHERE id=?", (quality, application_id))

    async def approve_section(self, application_id, snapshot_id):
        self.reconcile_browser_state()
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
        self.reconcile_browser_state()
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
        from .documents import document_path, SUBMITTED_STATUSES

        current = self.application(application['id'])
        if current['status'] in SUBMITTED_STATUSES:
            raise ValueError('This application has advanced or closed. Review its current outcome before any submission.')
        from .job_validity import eligible, verify_job
        job = decode_row(self.db.one('SELECT * FROM jobs WHERE id=?', (application['job_id'],)))
        if not eligible(job):
            raise ValueError('This job is closed or its published deadline has expired')
        if not os.environ.get('MERIDIAN_TEST') and await verify_job(self.db, job) != 'OPEN':
            raise ValueError('Current employer availability could not be verified. Check the position before submitting.')

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
        identity = hashlib.sha256(json.dumps([application['job_id'], application['url'], self.db.one('SELECT id FROM candidates LIMIT 1')['id']], sort_keys=True).encode()).hexdigest()
        receipt_id = str(uuid4())
        with self.db.transaction() as conn:
            conn.execute('INSERT INTO application_submissions(id,application_id,run_id,identity_key,state,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                         (receipt_id, application['id'], run_id, identity, 'SUBMISSION_PENDING', now(), now()))
            conn.execute("UPDATE automation_runs SET status='submitting',updated_at=? WHERE id=?", (now(), run_id))
        self.active_run = run_id
        operation_task = asyncio.current_task()
        owns_task = run_id not in self._run_tasks
        if owns_task:
            self._run_tasks[run_id] = operation_task
        self._event(
            application["id"], "APPLYING", "Submission approved; attempting one submission"
        )
        try:
            async with asyncio.timeout(self.submission_timeout):
                submitted = await self.browser.submit(result.get("snapshot_id"), settings=settings_for(self.db))
            submitted["evidence_at"] = result.get("evidence_at", now())
            submitted["facts_snapshot"] = result.get("facts_snapshot", [])
            if submitted.get("status") != "confirmed":
                submitted['status'] = 'unconfirmed'
                submitted['error'] = submitted.get('error') or 'Submission has no independent confirmation. Check the page and email before retrying.'
            self.persist_result(run_id, application["id"], submitted)
            self.db.execute('UPDATE application_submissions SET state=?,evidence=?,updated_at=? WHERE id=?',
                            ('CONFIRMED' if submitted['status'] == 'confirmed' else 'SUBMISSION_UNCONFIRMED', json.dumps(submitted.get('evidence', [])), now(), receipt_id))
            return submitted
        except asyncio.CancelledError:
            self._fail_run(run_id, 'Submission was interrupted.')
            raise
        except Exception as error:
            reason = 'Submission exceeded the time limit.' if isinstance(error, TimeoutError) else 'Submission was interrupted.'
            self._fail_run(run_id, reason)
            return json.loads(self.db.one('SELECT checkpoint FROM automation_runs WHERE id=?', (run_id,))['checkpoint'])
        finally:
            self.db.execute("UPDATE application_submissions SET state='SUBMISSION_UNCONFIRMED',updated_at=? WHERE id=? AND state='SUBMISSION_PENDING'", (now(), receipt_id))
            if owns_task:
                self._run_tasks.pop(run_id, None)
            if self.active_run == run_id and not self.running:
                self.active_run = None

    def status(self):
        self.reconcile_browser_state()
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
        self.browser.pause()
        for run_id, task in list(self._run_tasks.items()):
            if not task.done():
                self._fail_run(run_id, 'The app closed during form preparation. Start Auto Apply again to reopen and inspect the form.')
                task.cancel()
        tasks = set(self._run_tasks.values()) | self.tasks
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        try:
            async with asyncio.timeout(5):
                await self.prompt.close()
        except TimeoutError:
            pass
        for task in list(self.prompt.tasks):
            task.cancel()
        if self.prompt.tasks:
            await asyncio.gather(*self.prompt.tasks, return_exceptions=True)
        await self.browser.close()
