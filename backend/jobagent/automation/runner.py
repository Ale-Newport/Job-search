"""Application-specific deterministic filling, review and independent submission verification."""

from __future__ import annotations

import asyncio
import inspect
import hashlib
import json
from pathlib import Path
import re

from .adapters import get_adapter
from .answers import normalize, classify_question, SENSITIVE, dropdown_search_value, question_identity
from ..intelligence.formatting import choice as option_for
from .reasoning import conditional_state, profile_resolution
from .disclosures import disclosure_resolution, disclosure_clear_operation, disclosure_memory_key, has_disclosure_value, inherit_disclosure_context, personal_disclosure_kind
from .browser import BrowserSession, confirmation_evidence
from .browser import PROGRESS, is_submit, application_entry_target, application_input_elements, lost_posting_target
from .engines import make_engine
from .job_context import enrich_job_context
from .consent import action_key, dropdown_open_target, radio_group, resolution_element, section_plan, selected_resolution_matches
from .types import AutomationError, Decision, HumanRequired, Operation, Paused, StaleState, UserEdit


class BrowserManager(BrowserSession):
    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        super().__init__(self.data_dir / "browser-profile")
        self._active_application_id: str | None = None
        self._review: dict[str, dict] = {}
        self._last_result: dict | None = None
        self._run_lock = asyncio.Lock()
        self.on_step = None
        self.decision_engine = None  # Optional injected engine, useful for integration tests.
        self._section_review = {}
        self._omitted = {}
        self.form_state = 'OPEN_APPLICATION'
        self.completed_steps = []
        self._checkpoint_answers = []
        self._checkpoint_documents = []
        self._answer_diagnostics = {}

    def _invalidate_approvals(self):
        super()._invalidate_approvals()
        self._review.clear()
        self._section_review.clear()

    async def authorize_section(self, application_id, snapshot_id):
        review = self._section_review.get(snapshot_id)
        if not review or review["application_id"] != application_id or application_id != self._active_application_id:
            raise ValueError("This section review expired. Inspect the application again.")
        await self._validate_snapshot(snapshot_id)
        self._section_review.clear()
        return review["keys"]

    def section_checkpoint(self, snapshot, element, keys, fields, answers, steps, adapter, kind="fill"):
        self._section_review = {snapshot["id"]: {"application_id": self._active_application_id, "keys": keys}}
        result = self._result("section_review", snapshot, answers=answers, steps=steps, adapter=adapter)
        result["section"] = {"kind": kind, "title": element.get("section_label", "Application details"),
                             "fields": fields, "destination": element.get("href") or element.get("frame_url") or snapshot["url"]}
        return result

    def _result(self, status: str, snapshot: dict | None, *, answers: list | None = None,
                questions: list | None = None, steps: list | None = None,
                evidence: list | None = None, adapter: str = "generic", error: str | None = None) -> dict:
        if snapshot:
            self._reconcile_diagnostics(snapshot)
        from ..intelligence.answer_evidence import ApplicationConsistencyReviewer
        narratives = [d for d in self._answer_diagnostics.values() if d.get('provider') in
                      {'openai', 'anthropic', 'gemini', 'ollama', 'openai-compatible'} and d.get('answer')]
        coherence = ApplicationConsistencyReviewer().review(narratives)
        from ..intelligence.form_consistency import review_observed_facts
        factual_review = review_observed_facts(snapshot, get_adapter(snapshot['url']) if snapshot else None,
            getattr(self, '_consistency_engine', None), getattr(self, '_consistency_job', {}))
        coherence['issues'].extend(factual_review['issues'])
        coherence['passed'] = coherence['passed'] and factual_review['passed']
        coherence['factual_fields_checked'] = factual_review['checked_fields']
        if status == 'needs_review' and not coherence['passed']:
            status = 'human_required'
            questions = [*(questions or []), {'question': 'Review repeated or inconsistent application answers.',
                         'kind': 'application_consistency', 'reason': json.dumps(coherence['issues'])}]
        result = {"status": status, "answers": answers or [], "questions": questions or [], "steps": steps or [],
                  "evidence": evidence or [], "omitted": list(self._omitted.values()), "snapshot_id": snapshot["id"] if snapshot else None,
                  "current_url": snapshot["url"] if snapshot else self.status()["current_url"],
                  "adapter": adapter, "error": error, "application_id": self._active_application_id,
                  "form_state": self.form_state, "completed_steps": self.completed_steps, "documents": self._checkpoint_documents,
                  "answer_diagnostics": list(self._answer_diagnostics.values()),
                  "consistency_review": coherence,
                  "answer_progress": [{"question_id": d.get("question_id"), "question": d.get("question"),
                                       "status": d.get("status")} for d in self._answer_diagnostics.values()],
                  "validation_errors": snapshot.get("errors", []) if snapshot else [],
                  "sensitive": any(a.get("kind") in {"disability", "ethnicity", "gender", "criminal_record",
                                                       "demographic", "legal_certification"} for a in answers or [])}
        self._last_result = result
        return result

    async def fill_application(self, application: dict, facts: list[dict], documents: list[dict],
                               mode: str = "REVIEW", settings: dict | None = None) -> dict:
        async with self._run_lock:
            result = await self._fill(application, facts, documents, mode, settings or {})
            if self.edits:
                await self.edits.flush()
                adapter = get_adapter(application.get('application_url') or application.get('url', ''), application.get('ats'))
                for record in self.edits.records.values():
                    if not record.get('changed') or record.get('pending'):
                        continue
                    question = adapter.question(record['element'])
                    result['answers'][:] = [a for a in result['answers'] if a['question'] != question]
                    result['answers'].append({'question': question, 'answer': record['answer'],
                        'fact_ids': record.get('fact_ids', []), 'verified': bool(record.get('fact_ids')),
                        'kind': classify_question(question), 'source': 'user_edit'})
                    if record.get('save_error'):
                        result['status'] = 'human_required'
                        result['questions'].append({'question': question, 'kind': 'manual_edit_save',
                            'reason': 'Your edit is preserved in this form, but could not be saved for future applications. Retry preparation to save it.'})
            return result

    async def _fill(self, application: dict, facts: list[dict], documents: list[dict], mode: str, settings: dict) -> dict:
        same_application = self._active_application_id == application.get("id")
        if not same_application and self.edits:
            await self.edits.close()
            self.edits = None
        self._active_application_id = application.get("id")
        self._review.clear()
        self._section_review.clear()
        self._omitted.clear()
        url = application.get("application_url") or application.get("url")
        if not url:
            return self._result("error", None, error="This application has no URL.")
        adapter = get_adapter(url, application.get("ats"))
        section_consent = bool(settings.get("section_consent"))
        skip_optional = bool(settings.get("skip_optional_unknown"))
        section_grant = {tuple(tuple(v) if isinstance(v, list) else v for v in key)
                         for key in settings.get("_section_grant", [])}
        if mode.upper() == "MANUAL":
            return self._result("needs_review", None, adapter=adapter.name,
                                questions=[{"question": "Manual mode: open and complete the prepared application yourself.", "kind": "manual"}])
        self.form_state = 'IDENTIFY_FORM'
        self.completed_steps = list(settings.get('_resume_completed_steps', []))
        self._checkpoint_documents = [{'id': d.get('id'), 'filename': d.get('filename'), 'content_hash': d.get('content_hash')} for d in documents]
        answers: list[dict] = []
        self._checkpoint_answers = answers
        questions: list[dict] = []
        steps: list[dict] = []
        snapshot = None
        policies = dict(settings.get("sensitive_policies", {}))
        policies["_candidate_engine"] = settings.get("_candidate_engine")
        self._consistency_engine = settings.get('_candidate_engine')
        self._consistency_job = settings.get('_job_context', application)
        policies["_job_context"] = settings.get("_job_context", application)
        reasoning_cache = {}
        self._answer_diagnostics = {}
        from .company_research import CompanyResearch
        research = CompanyResearch(self.data_dir / "cache" / "company-research")
        resumes = [d for d in documents if d.get('kind') in {'cv', 'resume', 'base_cv', 'import'}]
        if len(resumes) == 1:
            settings['_job_context'] = {**settings.get('_job_context', application),
                'resume_text': resumes[0].get('text', ''), 'resume_hash': resumes[0].get('content_hash', ''),
                'resume_document_id': resumes[0].get('id'), 'resume_version_id': resumes[0].get('id')}

        async def answer_progress(update):
            diagnostic = self._answer_diagnostics.get(update['question_id'])
            if diagnostic is None:
                return
            diagnostic.update(status=update['status'], generation_attempt=update['attempt'])
            if self.on_step:
                outcome = self.on_step({'operation': 'ANSWER_PROGRESS', 'executed': False,
                    'label': diagnostic['question'], 'application_id': self._active_application_id,
                    'observation': snapshot, 'form_state': self.form_state,
                    'answer_diagnostics': [dict(d) for d in self._answer_diagnostics.values()],
                    'answer_progress': [{'question_id': d['question_id'], 'question': d['question'], 'status': d['status']}
                                        for d in self._answer_diagnostics.values()]})
                if inspect.isawaitable(outcome):
                    await outcome
        answer_llm = getattr(settings.get('_candidate_engine'), 'answer_llm', None)
        if answer_llm is not None and hasattr(answer_llm, 'on_progress'):
            answer_llm.on_progress = answer_progress

        async def resolve(element):
            element = resolution_element(element, snapshot['elements'])
            field = adapter.question(element)
            question_id = question_identity(application.get('id'), element, field)
            # Context is copied per question. A checkbox group shares one selection
            # result but every distinct question has its own immutable identity.
            element = {**element, 'question_id': question_id,
                       '_related_answers': [dict(a) for a in answers if a.get('question_id') != question_id]}
            corrections = [f for f in facts if f.get('manual_correction')]
            if corrections:
                corrected = profile_resolution(field, corrections, element=element, policies={})
                if corrected['answer'] is not None or corrected['leave_blank']:
                    return self._location_resolution(corrected, facts, element)
            disclosure = disclosure_resolution(field, element, policies)
            if disclosure:
                return disclosure
            if re.search(r'\babove (?:university|institution|school)\b', field, re.I):
                preceding = [e for e in snapshot['elements'] if e['index'] < element['index'] and e.get('form_id') == element.get('form_id') and e.get('document_id') == element.get('document_id') and re.search(r'university|institution|school', adapter.question(e), re.I)]
                if preceding:
                    parent = preceding[-1]
                    element['_education_reference'] = parent.get('selected_text') or parent.get('value')
            intelligence = settings.get('_candidate_engine')
            if intelligence:
                job = settings.get('_job_context', application)
                job = await research.enrich(job, field, element)
                settings['_job_context'] = job
                policies['_job_context'] = job
                cache_key = hashlib.sha256(json.dumps([question_id, intelligence.knowledge.version,
                    job, {k: element.get(k) for k in ('type','multiple','options','description','placeholder',
                    'max_length','min_length','pattern','section_label','_education_reference','_condition')}],
                    sort_keys=True, default=str).encode()).hexdigest()
                if cache_key not in reasoning_cache:
                    self._answer_diagnostics[question_id] = {'question_id': question_id, 'question': field,
                                                            'status': 'GENERATING'}
                    if self.on_step:
                        progress = self.on_step({'operation': 'ANSWER_PROGRESS', 'executed': False,
                            'label': field, 'application_id': self._active_application_id,
                            'observation': snapshot, 'answer_diagnostics': list(self._answer_diagnostics.values())})
                        if inspect.isawaitable(progress):
                            await progress
                    reasoning_cache[cache_key] = await intelligence.answer(field, element, job)
                result = dict(reasoning_cache[cache_key])
                result = intelligence.confirmed_fallback(field, result, facts, policies)
                result.update(question_id=question_id, status='ANSWER_VALIDATED' if result['answer'] is not None or result['leave_blank']
                              else 'ANSWER_FAILED', company_context=job.get('company_context', []))
                previous = self._answer_diagnostics.get(question_id, {})
                if previous.get('status') == 'ANSWER_ACCEPTED_BY_FORM' and previous.get('answer') == result['answer']:
                    result['status'] = previous['status']
                self._answer_diagnostics[question_id] = dict(result)
                if result['canonical_intent'] != 'OTHER' or result['answer'] is not None:
                    return self._location_resolution(result, facts, element)
            result = profile_resolution(field, facts, element=element, policies=policies,
                                       hints=[element.get("name", ""), element.get("autocomplete", "")])
            callback = settings.get("_reason_answer")
            if result['answer'] is None and not result['leave_blank'] and not result.get('root_cause') and callback and settings.get('assisted_autofill') and not intelligence:
                key = (field, element.get('type'), tuple(o.get('label', '') for o in element.get('options', [])))
                if key not in reasoning_cache:
                    reasoning_cache[key] = await callback(field, element)
                result = reasoning_cache[key] or result
            return self._location_resolution(result, facts, element)

        self.allowed_uploads = {Path(d["path"]).resolve() for d in documents if d.get("path") and Path(d["path"]).is_file()}
        try:
            self._check_pause()
            if same_application and self.page and not self.page.is_closed():
                snapshot = await self.wait_until_ready()
            else:
                snapshot = await self.open(url)
            if lost_posting_target(url, snapshot):
                return self._result('human_required', snapshot, adapter=adapter.name,
                                    questions=[{'question': 'The vacancy redirected away from its application. Verify that this exact role is still accepting applications.', 'kind': 'job_unavailable'}])
            if adapter.manual_only:
                return self._result("human_required", snapshot, adapter=adapter.name,
                                    questions=[{"question": "This source uses manual handoff. Complete the application in the visible browser.",
                                                "kind": "platform_restriction"}])
            # Research all observed question links before any narrative is generated.
            # This keeps company context stable as later answers are inserted.
            initial_job = settings.get('_job_context', application)
            for field in snapshot['elements']:
                if any(op in field.get('operations', []) for op in ('TYPE_TEXT', 'SELECT', 'CHECK')):
                    initial_job = await research.enrich(initial_job, adapter.question(field),
                                                       resolution_element(field, snapshot['elements']))
            settings['_job_context'] = initial_job
            max_steps = min(500, max(1, int(settings.get("browser_max_steps", 100))))
            handled: set[tuple] = set()
            page_advances = 0
            stale_retries = 0
            form_seen = False
            dismissed_cookies = set()
            field_attempts = {}
            pending_dropdown = None
            failed_dropdowns = {}
            cleared_disclosures = set()
            while len(steps) < max_steps:
                self.form_state = 'ANALYSE_CURRENT_STEP'
                job_context = enrich_job_context(settings.get('_job_context', application), snapshot)
                settings['_job_context'] = job_context
                policies['_job_context'] = job_context
                self._consistency_job = job_context
                intelligence = settings.get('_candidate_engine')
                if intelligence:
                    intelligence.reload()
                    disclosure_policy = intelligence.knowledge.get_policy('personal_disclosures', job_context)
                    policies.pop('personal_disclosures', None)
                    if disclosure_policy:
                        policies['personal_disclosures'] = disclosure_policy['value']
                self._check_pause()
                snapshot['application_started'] = form_seen
                application_fields = application_input_elements(snapshot)
                form_seen = form_seen or bool(application_fields)
                snapshot['application_started'] = form_seen
                cookie_text = ' '.join(frame.get('text','') for frame in snapshot.get('frames',[]))
                if re.search(r'cookies?|privacy preferences', cookie_text, re.I):
                    declines = [e for e in snapshot['elements'] if e.get('role')=='button' and 'CLICK' in e['operations'] and (e['document_id'],e['node_id']) not in dismissed_cookies and re.fullmatch(r'(?:reject|decline|deny)(?: all(?: cookies)?| optional cookies| non-essential cookies)?|only necessary(?: cookies)?|necessary cookies only|rechazar(?: todas)?|rechazar (?:las )?cookies(?: opcionales)?',e.get('label','').strip(),re.I)]
                    if len(declines)==1:
                        try:
                            await self._record_step(await self.execute(Decision(Operation.CLICK,declines[0]['index']),snapshot),steps,snapshot)
                            dismissed_cookies.add((declines[0]['document_id'],declines[0]['node_id']))
                            snapshot = await self.wait_until_ready()
                            continue
                        except StaleState:
                            if stale_retries>=5:
                                raise
                            stale_retries+=1
                            snapshot = await self.wait_until_ready()
                            continue
                if snapshot["blocked"]:
                    reason = "Human verification required" if snapshot["challenge"] else "Complete sign-in or multi-factor authentication in the visible browser."
                    return self._result("human_required", snapshot, answers=answers, steps=steps, adapter=adapter.name,
                                        questions=[{"question": reason, "kind": "captcha" if snapshot["challenge"] else "authentication"}])
                if lost_posting_target(url, snapshot):
                    return self._result('human_required', snapshot, answers=answers, steps=steps, adapter=adapter.name,
                                        questions=[{'question': 'Navigation no longer identifies the intended vacancy. Verify the destination before continuing.', 'kind': 'job_unavailable'}])
                evidence = confirmation_evidence(snapshot)
                if evidence:
                    # A confirmation visible before any submission is evidence of the current page,
                    # not evidence that this run sent the active application's form.
                    return self._result("human_required", snapshot, answers=answers, steps=steps, evidence=evidence,
                                        adapter=adapter.name, questions=[{"question": "A confirmation page is already visible. Verify which application it belongs to.", "kind": "existing_confirmation"}])
                questions = []
                action = None
                value = None
                file_path = None
                upload_name = None
                upload_mime = None
                if pending_dropdown:
                    from ..intelligence.formatting import choice as enum_choice
                    current = next((e for e in snapshot['elements'] if e.get('manual_key') == pending_dropdown['manual_key']
                                    and e['frame_url'] == pending_dropdown['frame_url']), None)
                    if current and current.get('user_edit'):
                        pending_dropdown = None
                        continue
                    resolution = pending_dropdown["resolution"]
                    if current and not pending_dropdown.get('searched'):
                        visible_options = [e for e in snapshot['elements'] if e['role'] == 'option' and
                            'CLICK' in e['operations'] and e['frame_url'] == pending_dropdown['frame_url'] and
                            (not current.get('listbox_ids') or e.get('listbox_id') in current['listbox_ids'])]
                        visible_choice = enum_choice(resolution['answer'], visible_options,
                            question=resolution['question'], disclosure_concept=resolution.get('disclosure_concept'))
                        if visible_options and not visible_choice and settings.get('_candidate_engine'):
                            from ..intelligence.engine import NARRATIVE_INTENTS
                            if resolution.get('canonical_intent') not in NARRATIVE_INTENTS | {'OTHER', None}:
                                # Dynamic widgets expose their actual answer
                                # contract only after opening. Re-evaluate
                                # facts before filtering away those choices.
                                refined = await resolve({**current, 'options': visible_options})
                                if refined.get('answer') is not None and not refined.get('leave_blank'):
                                    resolution = pending_dropdown['resolution'] = refined
                                    visible_choice = enum_choice(resolution['answer'], visible_options,
                                        question=resolution['question'], disclosure_concept=resolution.get('disclosure_concept'))
                        search = current if 'TYPE_TEXT' in current['operations'] else next((e for e in snapshot['elements']
                            if e['document_id'] == current['document_id'] and e['node_id'] == current.get('dropdown_search_id')
                            and 'TYPE_TEXT' in e['operations']), None)
                        from .answers import canonical_key
                        needs_search = resolution.get('canonical_intent') == 'LOCATION' or canonical_key(resolution['question']) in {'location','city'} or not visible_choice
                        if search and needs_search:
                            try:
                                await self._record_step(await self.execute(Decision(Operation.TYPE_TEXT, search['index']),
                                    snapshot, value=dropdown_search_value(resolution)), steps, snapshot)
                            except UserEdit:
                                pending_dropdown = None
                                snapshot = await self.observe()
                                continue
                            pending_dropdown['searched'] = True
                            snapshot = await self.observe()
                            continue
                    for _ in range(20):
                        options = [e for e in snapshot["elements"] if e["role"] == "option" and "CLICK" in e["operations"]
                                   and e['frame_url'] == pending_dropdown['frame_url']
                                   and (not current or not current.get('listbox_ids') or e.get('listbox_id') in current['listbox_ids'])]
                        choice = enum_choice(resolution["answer"], options, question=resolution["question"],
                                             disclosure_concept=resolution.get("disclosure_concept"))
                        choices = [choice] if choice else []
                        if choices or snapshot["blocked"]:
                            break
                        await asyncio.sleep(.2)
                        snapshot = await self.observe()
                    if len(choices) != 1:
                        failed_dropdowns[(pending_dropdown['frame_url'], pending_dropdown['manual_key'] or
                                          (pending_dropdown['document_id'], pending_dropdown['node_id']))] = {
                            'question': resolution['question'], 'kind': 'option',
                            'reason': 'No unique visible dropdown option matches the verified answer.'}
                        # An unresolved choice must not prevent independent later
                        # fields from being completed. It remains a final review item.
                        pending_dropdown = None
                        continue
                    try:
                        await self._record_step(await self.execute(Decision(Operation.CLICK, choices[0]["index"]), snapshot), steps, snapshot)
                    except UserEdit:
                        pending_dropdown = None
                        snapshot = await self.observe()
                        continue
                    except StaleState:
                        if section_consent or stale_retries >= 5:
                            raise
                        stale_retries += 1
                        snapshot = await self.observe()
                        continue
                    snapshot = await self.observe()
                    selected = next((e for e in snapshot["elements"] if e["node_id"] == pending_dropdown["node_id"] and
                                     e["document_id"] == pending_dropdown["document_id"]), None)
                    if selected is None or selected.get("expanded") or normalize(selected.get("selected_text") or selected.get("value", "")) != normalize(choices[0]["label"]):
                        failed_dropdowns[(pending_dropdown['frame_url'], pending_dropdown['manual_key'] or
                                          (pending_dropdown['document_id'], pending_dropdown['node_id']))] = {
                            'question': resolution['question'], 'kind': 'option',
                            'reason': 'The selected dropdown value could not be independently verified.'}
                        pending_dropdown = None
                        continue
                    resolution['selection_verified'] = True
                    diagnostic = self._answer_diagnostics.get(resolution.get('question_id'))
                    if diagnostic:
                        diagnostic['selection_verified'] = True
                    self._accepted_answer(resolution, selected)
                    self._add_answer(answers, dict(resolution))
                    pending_dropdown = None
                    continue
                for element in snapshot["elements"]:
                    if (any(op in element.get('operations', []) for op in ('TYPE_TEXT', 'SELECT', 'CHECK', 'UPLOAD'))
                            and element not in application_fields):
                        continue
                    if not any(op in element.get('operations', []) for op in
                               ('TYPE_TEXT', 'SELECT', 'CHECK', 'UNCHECK', 'UPLOAD', 'CLEAR')) and element.get('role') != 'combobox':
                        continue
                    if element.get('user_edit'):
                        edit = element['user_edit']
                        field = adapter.question(element)
                        self._omitted.pop(field, None)
                        if edit.get('changed') or edit.get('pending'):
                            answers[:] = [a for a in answers if a['question'] != field]
                        if edit.get('pending') or not edit.get('answer') and element.get('required'):
                            questions.append({'question': field, 'kind': 'manual_edit', 'target': element['index'],
                                              'reason': 'Your edit is preserved. Finish this required value or selection in the browser.'})
                        elif edit.get('changed'):
                            self._add_answer(answers, {'question': field, 'answer': edit['answer'],
                                'fact_ids': edit.get('fact_ids') or [], 'verified': bool(edit.get('fact_ids')),
                                'kind': classify_question(field), 'source': 'user_edit'})
                        continue
                    unresolved = failed_dropdowns.get((element.get('frame_url'), element.get('manual_key') or
                                                       (element['document_id'], element['node_id'])))
                    if unresolved:
                        questions.append({**unresolved, 'target': element['index'],
                                          'required': bool(element.get('required'))})
                        continue
                    inherit_disclosure_context(element, snapshot["elements"], adapter)
                    disclosure = disclosure_resolution(adapter.question(element), element, policies)
                    if disclosure:
                        field = adapter.question(element)
                        self._omitted[field] = {"question": field, "reason": disclosure["reason"],
                                                "policy": disclosure["policy"], "required": bool(element.get("required")),
                                                "state": "clear_required" if has_disclosure_value(element) else "left_blank"}
                        identity = (element["document_id"], element["node_id"])
                        clear = disclosure_clear_operation(element)
                        if clear and identity not in cleared_disclosures:
                            operation, value = clear
                            action = Decision(Operation(operation), element["index"])
                            break
                        if has_disclosure_value(element):
                            self._omitted[field]['reason'] = "An existing personal disclosure could not be safely cleared; remove it in the browser."
                            questions.append({"question": field, "kind": "personal_disclosure_clear",
                                              "target": element["index"],
                                              "reason": "Your policy requires this field to be blank. Its existing selection could not be safely cleared; remove it in the browser.",
                                              "skip_inline_question": True})
                        elif element.get("required"):
                            questions.append({"question": field, "kind": "personal_disclosure_policy",
                                              "target": element["index"],
                                              "reason": disclosure["reason"], "skip_inline_question": True})
                        continue
                    condition = conditional_state(element, snapshot['elements'], adapter)
                    element['_condition'] = condition or {}
                    self._omitted.pop(adapter.question(element), None)
                    if condition and condition['applies'] is False:
                        self._omitted[adapter.question(element)] = {'question': adapter.question(element), 'reason': condition['reason']}
                    if condition and condition['applies'] is not True:
                        if condition['applies'] is None and element.get('required'):
                            questions.append({'question': adapter.question(element), 'kind': 'conditional', 'reason': condition['reason'], 'target': element['index']})
                        # Inapplicable fields must remain blank even when an exact saved answer exists.
                        elif condition['applies'] is False and element.get('value') and 'TYPE_TEXT' in element['operations']:
                            action, value = Decision(Operation.TYPE_TEXT, element['index']), ''
                            break
                        continue
                    if not element.get("required") and disclosure_memory_key(adapter.question(element), element) in settings.get("_skipped_questions", []):
                        continue
                    if settings.get('assisted_autofill') and not element.get('required') and (
                        personal_disclosure_kind(adapter.question(element), element)
                        or classify_question(adapter.question(element)) in {*SENSITIVE, 'work_authorization'}
                    ):
                        explicit = await resolve(element) if settings.get('_candidate_engine') else profile_resolution(adapter.question(element), facts, element=element, policies=policies)
                        if explicit['answer'] is None:
                            if has_disclosure_value(element):
                                questions.append({'question': adapter.question(element), 'kind': explicit['kind'],
                                                  'target': element['index'],
                                                  'reason': 'The existing personal answer has no confirmed supporting fact. Review it in the browser.'})
                            elif explicit.get('root_cause') == 'BAD_ENUM_MAPPING':
                                questions.append({'question': adapter.question(element), 'kind': explicit['kind'],
                                                  'target': element['index'],
                                                  'reason': 'None of the available choices matches your confirmed answer. Review this question in the browser.'})
                            else:
                                self._omitted[adapter.question(element)] = {'question': adapter.question(element), 'reason': 'Optional disclosure not provided; left blank without inferring personal data.'}
                            continue
                    operations = element["operations"]
                    if element["role"] == "combobox" and "CLICK" in operations and "SELECT" not in operations:
                        resolution = await resolve(element)
                        if resolution["leave_blank"] and not element.get("required") and not has_disclosure_value(element):
                            continue
                        if resolution["answer"] is None:
                            if element.get("required") or not skip_optional or element.get('selected_text') or element.get('value'):
                                questions.append({"question": adapter.question(element), "kind": resolution["kind"], "required": element.get("required", False), "target": element["index"]})
                            continue
                        if not element.get("expanded") and selected_resolution_matches(resolution, element.get("selected_text") or element.get("value", "")):
                            self._add_answer(answers, dict(resolution))
                            continue
                        pending_dropdown = {"resolution": resolution, "node_id": element["node_id"],
                                            "document_id": element["document_id"], 'manual_key': element.get('manual_key'),
                                            'frame_url': element['frame_url'], 'searched': False}
                        trigger = dropdown_open_target(element, snapshot["elements"])
                        if trigger:
                            action, value = Decision(Operation.CLICK, trigger["index"]), None
                        else:
                            action = Decision(Operation.TYPE_TEXT if "TYPE_TEXT" in operations else Operation.CLICK, element["index"])
                            value = dropdown_search_value(resolution)
                            pending_dropdown['searched'] = action.operation == Operation.TYPE_TEXT
                        break
                    if not any(op in operations for op in ("TYPE_TEXT", "SELECT", "CHECK", "UNCHECK", "UPLOAD")):
                        continue
                    field = adapter.question(element)
                    signature = (element["document_id"], element["node_id"], field, element["value"], element["checked"])
                    if signature in handled:
                        continue
                    if "UPLOAD" in operations:
                        candidates = self._documents_for(field, documents)
                        if len(candidates) != 1:
                            if element.get("required") or (not skip_optional and not element.get("value")):
                                generator = settings.get('_generate_required_document')
                                kind = 'cover_letter' if 'cover' in normalize(field) else 'cv' if re.search(r'resume|\bcv\b', normalize(field)) else None
                                draft_id = await generator(kind) if generator and kind and not candidates else None
                                questions.append({"question": field or "Select a document to upload", "kind": "document", "target": element["index"],
                                                  **({'document_draft_id': draft_id, 'reason': 'A grounded draft is saved in Documents. Review and approve it before attaching.'} if draft_id else {})})
                            continue
                        document = candidates[0]
                        filename = Path((document.get("filename") or document["path"]).replace("\\", "/")).name
                        if filename in element.get("value", ""):
                            handled.add(signature)
                            continue
                        action = Decision(Operation.UPLOAD, element["index"])
                        file_path = document["path"]
                        upload_name = filename
                        upload_mime = document.get("mime_type")
                        self._add_answer(answers, {"question": field, "answer": filename, "fact_ids": [],
                                                  "document_version_id": document.get("id"), "verified": True})
                        break
                    resolution = await resolve(element)
                    if resolution["leave_blank"]:
                        if element.get("required"):
                            questions.append({"question": field, "kind": resolution["kind"], "reason": "This field is required but your policy is to leave it blank.", "target": element["index"]})
                        elif has_disclosure_value(element):
                            explicit_blank = (resolution.get('confidence_level') == 'EXACT'
                                              and resolution.get('fact_ids') and not resolution.get('root_cause'))
                            if (resolution.get('source') == 'user_edit' or explicit_blank) and 'CLEAR' in operations:
                                self._add_answer(answers, dict(resolution))
                                action = Decision(Operation.CLEAR, element['index'])
                                value = None
                                break
                            questions.append({"question": field, "kind": resolution["kind"],
                                              "target": element["index"],
                                              "reason": "The existing answer has no confirmed supporting fact. Review it in the browser."})
                        else:
                            handled.add(signature)
                            self._accepted_answer(resolution, element)
                        continue
                    if resolution["answer"] is None:
                        if element.get("required") or not skip_optional or element.get('value'):
                            questions.append({"question": field, "kind": resolution["kind"], "target": element["index"],
                                              "current_value": element["value"], "required": element.get("required", False),
                                              "reason": "The current value needs verification against candidate evidence." if element.get('value') else resolution.get('root_cause')})
                        # Do not mark unknown fields handled: questions must survive the next observation.
                        continue
                    answer = resolution["answer"]
                    operation = None
                    if "TYPE_TEXT" in operations:
                        phone_equivalent = bool(re.search(r'\bphone|\bmobile|\btelephone', field, re.I)
                                                and selected_resolution_matches(resolution, element['value']))
                        if element["value"] != answer and not phone_equivalent:
                            operation, value = Operation.TYPE_TEXT, answer
                    elif "SELECT" in operations:
                        selected = resolution.get('form_value')
                        values = selected if element.get('multiple') and isinstance(selected, list) else [answer]
                        options = [option_for(item, element["options"], question=field,
                                             disclosure_concept=resolution.get("disclosure_concept")) for item in values]
                        if not options or any(option is None for option in options):
                            questions.append({"question": field, "kind": "option", "reason": "The verified answer does not match any observed option.", "target": element["index"]})
                            continue
                        wanted = [option['value'] for option in options]
                        if (set(element.get('selected_values', [])) != set(wanted) if element.get('multiple')
                                else element['value'] != wanted[0]):
                            operation, value = Operation.SELECT, wanted if element.get('multiple') else wanted[0]
                    elif element["role"] == "radio":
                        if normalize(answer) not in {normalize(element["label"]), normalize(element["value"])}:
                            # Resolve the group as a whole, not one radio independently.
                            siblings = radio_group(element, snapshot["elements"])
                            has_option = any(normalize(answer) in {normalize(e["label"]), normalize(e["value"])} for e in siblings)
                            if not has_option:
                                questions.append({"question": field, "kind": "option", "reason": "No radio option matches the verified answer.", "target": element["index"]})
                            else:
                                handled.add(signature)
                            continue
                        if not element["checked"]:
                            operation = Operation.CHECK
                    else:
                        truth = normalize(answer)
                        if element.get('multiple') and isinstance(resolution.get('form_value'), list):
                            truth = 'yes' if any(option_for(item, [{'label': element['label'], 'value': element['value']}],
                                question=field) for item in resolution['form_value']) else 'no'
                        if truth not in {"yes", "no", "true", "false", "1", "0"}:
                            questions.append({"question": field, "kind": "checkbox", "reason": "A verified yes/no answer is required.", "target": element["index"]})
                            continue
                        desired = truth in {"yes", "true", "1"}
                        if desired != element["checked"]:
                            operation = Operation.CHECK if desired else Operation.UNCHECK
                    self._add_answer(answers, dict(resolution))
                    if operation:
                        action = Decision(operation, element["index"])
                        break
                    handled.add(signature)
                    self._accepted_answer(resolution, element)
                if action:
                    self.form_state = 'FILL_FIELDS'
                    target = next(e for e in snapshot['elements'] if e['index']==action.target)
                    semantic_action = (snapshot['url'], len(self.completed_steps), adapter.question(target), target.get('name'), target.get('type'), action.operation.value, str(value), upload_name)
                    if field_attempts.get(semantic_action,0)>=3:
                        return self._result('human_required',snapshot,answers=answers,steps=steps,adapter=adapter.name,
                                            questions=[{'question':adapter.question(target),'kind':'validation','reason':'The website repeatedly reset or transformed this verified value. Review the control in the browser.'}])
                    if section_consent:
                        target = next(e for e in snapshot["elements"] if e["index"] == action.target)
                        key = action_key(target, action.operation, value, file_path, upload_name)
                        if key not in section_grant:
                            exact_resolutions = {}
                            for peer in snapshot['elements']:
                                if (peer.get('section_id', 'page') == target.get('section_id', 'page')
                                        and peer.get('document_id') == target.get('document_id')
                                        and any(op in peer.get('operations', []) for op in ('TYPE_TEXT', 'SELECT', 'CHECK'))):
                                    exact_resolutions[adapter.question(peer)] = await resolve(peer)
                            policies['_answer_resolutions'] = exact_resolutions
                            fields, keys = section_plan(snapshot, target, adapter, facts, documents, policies, self._documents_for)
                            if key not in keys:
                                raise HumanRequired("This field needs manual review before it can be filled.")
                            return self.section_checkpoint(snapshot, target, keys, fields, answers, steps, adapter.name)
                        section_grant.remove(key)
                    try:
                        result = await self.execute(action, snapshot, value=value, file_path=file_path,
                                                    upload_name=upload_name, upload_mime=upload_mime,
                                                    minimum_confidence=float(settings.get("browser_confidence_threshold", settings.get("min_confidence", .85))))
                    except StaleState as error:
                        # ATSs hydrate and validate asynchronously while fields are filled.
                        # The rejected operation made no write. Re-observe and resolve from
                        # verified facts again; never reuse an old target or section approval.
                        if not isinstance(error, UserEdit) and (section_consent or stale_retries >= 5):
                            raise
                        stale_retries += int(not isinstance(error, UserEdit))
                        handled.clear()
                        pending_dropdown = None
                        snapshot = await self.wait_until_ready()
                        continue
                    if action.operation == Operation.UPLOAD:
                        result['document_version_id'] = document.get('id')
                    field_attempts[semantic_action] = field_attempts.get(semantic_action,0)+1
                    await self._record_step(result, steps, snapshot)
                    cleared = next((e for e in snapshot["elements"] if e["index"] == action.target), None)
                    if cleared and disclosure_resolution(adapter.question(cleared), cleared, policies):
                        cleared_disclosures.add((cleared["document_id"], cleared["node_id"]))
                    snapshot = await self.observe()
                    if action.operation in {Operation.TYPE_TEXT, Operation.SELECT, Operation.CHECK, Operation.UNCHECK, Operation.CLEAR}:
                        observed = next((e for e in snapshot['elements'] if e['node_id'] == target['node_id']
                                         and e['document_id'] == target['document_id']), None)
                        if observed:
                            diagnostic = self._answer_diagnostics.get(question_identity(
                                application.get('id'), target, adapter.question(target)))
                            if diagnostic:
                                diagnostic['status'] = 'ANSWER_INSERTED'
                                self._accepted_answer(diagnostic, observed)
                    continue
                for hidden in snapshot.get("unresolved_required", []):
                    question = hidden["label"] or "Required hidden control"
                    disclosure = disclosure_resolution(question, {**hidden, "required": True}, policies)
                    questions.append({"question": question, "kind": "personal_disclosure_policy" if disclosure else "unsupported_widget",
                                      "reason": disclosure["reason"] if disclosure else "A required form control is hidden and needs manual handling.",
                                      "skip_inline_question": bool(disclosure)})
                if any(frame.get("unavailable") for frame in snapshot["frames"]):
                    questions.append({"question": "A frame could not be inspected. Check the complete application in the browser.",
                                      "kind": "unavailable_frame"})
                if questions:
                    for question in questions:
                        if question.get("target") is not None:
                            element = next((e for e in snapshot["elements"] if e["index"] == question["target"]), None)
                        else:
                            matches = [e for e in snapshot["elements"] if adapter.question(e) == question["question"]]
                            element = matches[0] if len(matches) == 1 else None
                        if element:
                            question.update(target=element["index"], required=bool(element.get("required")),
                                            current_value=element.get("value", ""), node_id=element["node_id"],
                                            document_id=element["document_id"], type=element.get("type"),
                                            form_id=element.get("form_id"), group=element.get("group"),
                                            multiple=bool(element.get('multiple')),
                                            role=element.get("role"), description=element.get("description", ""),
                                            context=element.get("context", ""), section_label=element.get("section_label", ""),
                                            _disclosure_parent_question=element.get("_disclosure_parent_question", ""),
                                            multiline=element.get("tag") == "textarea", options=element.get("options", []))
                            if element["role"] == "radio":
                                siblings = radio_group(element, snapshot["elements"])
                                question["options"] = [{"label": e["label"], "value": e["value"]} for e in siblings]
                                question["current_value"] = next((e["label"] for e in siblings if e["checked"]), "")
                            elif element["role"] == "checkbox":
                                if element.get('multiple'):
                                    group = resolution_element(element, snapshot['elements'])
                                    question['options'] = group['options']
                                    question['current_value'] = [e['label'] for e in radio_group(element, snapshot['elements']) if e.get('checked')]
                                else:
                                    question["options"] = [{"label": "Yes"}, {"label": "No"}]
                                    question["current_value"] = "Yes" if element["checked"] else ""
                    questions = self._dedupe_questions(questions)
                    return self._result("human_required", snapshot, answers=answers, questions=questions,
                                        steps=steps, adapter=adapter.name)
                if snapshot["errors"]:
                    return self._result("human_required", snapshot, answers=answers, steps=steps, adapter=adapter.name,
                                        questions=[{"question": e, "kind": "validation"} for e in snapshot["errors"]])
                has_inputs = bool(application_fields)
                # Entry and method-choice pages precede any candidate form.
                # A completed wizard may have a review page with no inputs.
                entry_only = not has_inputs and not form_seen
                submit = None if entry_only else adapter.submit_button(snapshot)
                if submit:
                    if self.edits:
                        await self.edits.flush()
                        refreshed = await self.observe()
                        if refreshed['fingerprint'] != snapshot['fingerprint']:
                            snapshot = refreshed
                            continue
                    self.form_state = 'FINAL_REVIEW'
                    self._review[snapshot["id"]] = {"application_id": self._active_application_id, "adapter": adapter.name,
                                                    "mode": mode.upper(), "answers": answers, "steps": steps,
                                                    "sensitive": any(a.get("kind") in {"disability", "ethnicity", "gender", "criminal_record", "demographic", "legal_certification"} for a in answers)}
                    return self._result("needs_review", snapshot, answers=answers, steps=steps, adapter=adapter.name)
                entry = application_entry_target(snapshot) if entry_only else None
                next_button = dict(entry) if entry else adapter.next_button(snapshot)
                if entry:
                    next_button['_decision'] = Decision(Operation.OPEN_TAB if entry['tag'] == 'a' else Operation.CLICK,
                                                        entry['index'])
                if next_button is None:
                    next_button = await self._decide_safe_progress(snapshot, settings)
                if next_button and page_advances < 30:
                    previous = snapshot["fingerprint"]
                    decision = next_button.pop("_decision", None) or Decision(Operation.CLICK, next_button["index"])
                    if section_consent:
                        key = action_key(next_button, decision.operation)
                        if key not in section_grant:
                            return self.section_checkpoint(snapshot, next_button, [key],
                                [{"question": "Open the employer's application" if entry else "Continue in the employer's form",
                                  "answer": next_button["label"], "operation": decision.operation.value}],
                                answers, steps, adapter.name, "start_application" if entry else "continue")
                        section_grant.remove(key)
                    self.form_state = 'OPEN_APPLICATION' if entry else 'CONTINUE_TO_NEXT_STEP'
                    await self._record_step(await self.execute(decision, snapshot), steps, snapshot)
                    if not entry:
                        self.completed_steps.append({'url': snapshot['url'], 'labels': [adapter.question(e) for e in snapshot['elements'] if e.get('role') not in ('button','link')]})
                    page_advances += 1
                    snapshot = await self._wait_for_change(previous)
                    snapshot = await self.wait_until_ready()
                    if snapshot["fingerprint"] == previous:
                        return self._result("human_required", snapshot, answers=answers, steps=steps, adapter=adapter.name,
                                            questions=[{"question": "The next step did not open. Check form validation in the browser.", "kind": "validation"}])
                    continue
                return self._result("human_required", snapshot, answers=answers, steps=steps, adapter=adapter.name,
                                    questions=[{"question": "No unambiguous next step or final Submit was observed. Continue in the browser.", "kind": "unsupported_widget"}])
            return self._result("blocked", snapshot, answers=answers, steps=steps, adapter=adapter.name,
                                error="The bounded browser step budget was reached.")
        except (HumanRequired, StaleState, Paused) as error:
            # A browser that never opened is a failed preparation, not a form
            # waiting for candidate input in an invisible review session.
            status = 'error' if snapshot is None and not isinstance(error, Paused) else 'human_required'
            return self._result(status, snapshot, answers=answers, steps=steps, adapter=adapter.name,
                                questions=[{"question": str(error), "kind": error.code}], error=str(error))
        except Exception as error:
            self._last_error = str(error)[:500]
            return self._result("error", snapshot, answers=answers, steps=steps, adapter=adapter.name, error=self._last_error)

    async def mark_inferred_answers(self, result):
        """Visible, non-interactive review badges; never modify field values/labels."""
        from importlib.resources import files
        source = files('jobagent').joinpath('automation/inference_badges.js').read_text()
        snapshot, refs = self._snapshots.get(result.get('snapshot_id'), ({}, {}))
        adapter = get_adapter(result.get('current_url', ''), result.get('adapter'))
        notes = {a['question']: a for a in result.get('answers', []) if a.get('inferred')}
        for frame in self.page.frames if self.page and not self.page.is_closed() else []:
            entries = []
            for element in snapshot.get('elements', []):
                answer = notes.get(adapter.question(element))
                ref = refs.get(element['index'])
                if answer and ref and ref[0] == frame:
                    entries.append({'node_id': ref[1], 'document_id': ref[2], 'reason': answer['reason']})
            try:
                await frame.evaluate(source, entries)
            except Exception:
                # Cosmetic annotations must not turn an unavailable frame into a failed run.
                pass

    async def submit(self, snapshot_id: str | None, settings: dict | None = None) -> dict:
        """Call only after root policy/approval checks; one-use approval is bound to a fresh form."""
        async with self._run_lock:
            review = self._review.get(snapshot_id or "")
            if not review or review["application_id"] != self._active_application_id:
                return self._result("human_required", None, error="Review the prepared form before submitting.")
            if review["mode"] == "MANUAL":
                return self._result("human_required", None, error="Manual mode does not permit browser submission.")
            snapshot = None
            try:
                self._check_pause()
                snapshot, _ = await self._validate_snapshot(snapshot_id)
                adapter = get_adapter(snapshot["url"], review["adapter"])
                if adapter.manual_only:
                    raise HumanRequired("This platform uses manual submission.")
                submit = adapter.submit_button(snapshot)
                if not submit:
                    raise HumanRequired("The final Submit control is no longer unambiguous.")
                if snapshot["errors"]:
                    raise HumanRequired("Resolve visible validation errors before submission.")
                if snapshot.get("unresolved_required"):
                    raise HumanRequired("A required hidden form control still needs manual handling.")
                if any(frame.get("unavailable") for frame in snapshot["frames"]):
                    raise HumanRequired("A frame could not be inspected. Review the complete page before submission.")
                # Never repeat a possibly successful submit after timeout or lost confirmation.
                self._review.pop(snapshot_id, None)
                step = await self.execute(Decision(Operation.CLICK, submit["index"]), snapshot, allow_submit=True)
                steps = [*review["steps"]]
                await self._record_step(step, steps, snapshot)
                for _ in range(30):
                    self._check_pause()
                    snapshot = await self.observe()
                    evidence = confirmation_evidence(snapshot)
                    if evidence:
                        return self._result("confirmed", snapshot, answers=review["answers"], steps=steps,
                                            evidence=evidence, adapter=adapter.name)
                    if snapshot["errors"] or snapshot["blocked"]:
                        break
                    await asyncio.sleep(.3)
                return self._result("human_required", snapshot, answers=review["answers"], steps=steps,
                                    adapter=adapter.name, questions=[{"question": "Submit was clicked, but no independent confirmation was observed. Verify the browser or confirmation email before retrying.", "kind": "unconfirmed_submission"}])
            except AutomationError as error:
                return self._result("human_required", snapshot, answers=review["answers"], steps=review["steps"],
                                    adapter=review["adapter"], questions=[{"question": str(error), "kind": error.code}], error=str(error))
            except Exception as error:
                return self._result("error", snapshot, answers=review["answers"], steps=review["steps"],
                                    adapter=review["adapter"], error="Submission outcome is uncertain: " + str(error)[:300])

    async def _wait_for_change(self, fingerprint: str) -> dict:
        for _ in range(15):
            self._check_pause()
            snapshot = await self.observe()
            if snapshot["fingerprint"] != fingerprint:
                return snapshot
            await asyncio.sleep(.15)
        return snapshot

    async def _record_step(self, step: dict, steps: list, snapshot: dict) -> None:
        steps.append(step)
        if self.on_step:
            outcome = self.on_step({**step, "application_id": self._active_application_id,
                                    "snapshot_id": snapshot["id"], "observation": snapshot,
                                    "form_state": self.form_state, "completed_steps": self.completed_steps,
                                    "answer_diagnostics": list(self._answer_diagnostics.values()),
                                    "answer_progress": [{"question_id": d.get('question_id'), "question": d.get('question'),
                                                         "status": d.get('status')} for d in self._answer_diagnostics.values()],
                                    "answers": self._checkpoint_answers, "documents": self._checkpoint_documents})
            if inspect.isawaitable(outcome):
                await outcome

    @staticmethod
    def _location_resolution(result, facts, element):
        from .answers import canonical_key, resolve_answer
        if (result.get('answer') and element.get('role') == 'combobox' and
                (result.get('canonical_intent') == 'LOCATION' or canonical_key(result.get('question', '')) in {'location','city'})):
            country = resolve_answer('country', facts)
            if country.get('answer') and not re.search(r'[,()]', result['answer']) and normalize(country['answer']) not in normalize(result['answer']):
                result = {**result, 'answer': result['answer'] + ', ' + country['answer'],
                          'fact_ids': list(dict.fromkeys([*result['fact_ids'], *country['fact_ids']]))}
        return result

    async def _decide_safe_progress(self, snapshot: dict, settings: dict) -> dict | None:
        # L3 resolves ambiguous safe navigation only. It never chooses field values,
        # submits, accepts terms, leaves the application or clicks unknown controls.
        candidates = [e for e in snapshot["elements"] if e.get("role") == "button" and
                      "CLICK" in e["operations"] and PROGRESS.match(e["label"].strip()) and not is_submit(e)]
        if not candidates:
            return None
        engine_name = settings.get("browser_engine", "laya")
        if engine_name == "deterministic" and self.decision_engine is None:
            return None
        engine = self.decision_engine or make_engine(engine_name, settings, settings.get("_jev_api_key"))
        scope = {**snapshot, "elements": candidates}
        decision = await engine.decide(scope, "Advance to the next page of this job application without submitting it.")
        if decision.operation != Operation.CLICK:
            raise HumanRequired("The decision engine could not select a safe next step. Continue in the browser.")
        target = next((e for e in candidates if e["index"] == decision.target), None)
        if target is None:
            raise HumanRequired("The decision engine selected a control outside safe application navigation.")
        return {**target, "_decision": decision}

    @staticmethod
    def _add_answer(answers: list[dict], answer: dict) -> None:
        key = answer.get('question_id') or answer['question']
        answers[:] = [a for a in answers if (a.get('question_id') or a['question']) != key]
        answers.append(answer)

    def _accepted_answer(self, resolution, element):
        """Advance only from values independently observed after a browser write."""
        diagnostic = self._answer_diagnostics.get(resolution.get('question_id'))
        if not diagnostic:
            return
        if element.get('multiple'):
            # A group is accepted only once all its members have been reconciled.
            return
        if resolution.get('leave_blank'):
            accepted = not has_disclosure_value(element)
        elif element.get('role') in {'checkbox', 'radio'}:
            accepted = (bool(element.get('checked')) and bool(option_for(str(resolution.get('answer')),
                        [{'label': element.get('label', ''), 'value': element.get('value', '')}],
                        question=resolution.get('question', '')))) if element.get('role') == 'radio' else (
                bool(element.get('checked')) == (normalize(str(resolution.get('answer'))) in {'yes','true','1'}))
        else:
            accepted = selected_resolution_matches(resolution, element.get('selected_text') or element.get('value', ''))
            if element.get('role') == 'combobox' and element.get('tag') != 'select':
                accepted = accepted and not element.get('expanded') and bool(
                    element.get('selected_text') or resolution.get('selection_verified'))
        validity = element.get('validity') or {}
        accepted = accepted and validity.get('valid', True)
        diagnostic.update(observed_value=element.get('selected_text') or element.get('value'),
                          observed_checked=element.get('checked'), website_validation=validity,
                          validation_message=element.get('validation_message', ''))
        if accepted:
            diagnostic['status'] = 'ANSWER_ACCEPTED_BY_FORM'
        elif diagnostic.get('status') == 'ANSWER_ACCEPTED_BY_FORM':
            diagnostic['status'] = 'ANSWER_INSERTED'
        for answer in self._checkpoint_answers:
            if answer.get('question_id') == resolution.get('question_id'):
                answer['status'] = diagnostic['status']

    def _reconcile_diagnostics(self, snapshot):
        adapter = get_adapter(snapshot.get('url', ''))
        for diagnostic in self._answer_diagnostics.values():
            if diagnostic.get('answer') is None and not diagnostic.get('leave_blank'):
                continue
            fields = [e for e in snapshot.get('elements', []) if adapter.question(e) == diagnostic['question']]
            if not fields:
                continue  # Earlier wizard pages retain their observed acceptance.
            if any(e.get('multiple') for e in fields):
                expected = diagnostic.get('form_value')
                if not isinstance(expected, list):
                    continue
                if fields[0].get('tag') == 'select':
                    actual = [o['label'] for o in fields[0]['options'] if o['value'] in fields[0].get('selected_values', [])]
                else:
                    actual = [e['label'] for e in fields if e.get('checked')]
                accepted = len(expected) == len(actual) and all(option_for(item,
                    [{'label': value, 'value': value} for value in actual], question=diagnostic['question'])
                    for item in expected)
                diagnostic.update(observed_choices=actual, website_validation={'selection_matches': accepted})
                if accepted:
                    diagnostic['status'] = 'ANSWER_ACCEPTED_BY_FORM'
                elif diagnostic.get('status') == 'ANSWER_ACCEPTED_BY_FORM':
                    diagnostic['status'] = 'ANSWER_INSERTED'
            else:
                element = next((e for e in fields if e.get('checked')), fields[0])
                self._accepted_answer(diagnostic, element)
            for answer in self._checkpoint_answers:
                if answer.get('question_id') == diagnostic.get('question_id'):
                    answer['status'] = diagnostic['status']

    @staticmethod
    def _dedupe_questions(questions: list[dict]) -> list[dict]:
        seen = set()
        result = []
        for question in questions:
            grouped = question.get('role') == 'radio' or question.get('role') == 'checkbox' and question.get('multiple')
            control = (question.get("group") or question.get("node_id")) if grouped else question.get("node_id")
            identity = (question.get("document_id"), question.get("form_id"), control)
            key = (disclosure_memory_key(question["question"], question), question["kind"], identity)
            if key not in seen:
                result.append(question)
                seen.add(key)
        return result

    @staticmethod
    def _documents_for(question: str, documents: list[dict]) -> list[dict]:
        valid = [d for d in documents if d.get("path") and Path(d["path"]).is_file()]
        label = normalize(question)
        if re.search(r"resume|\bcv\b", label):
            return [d for d in valid if d.get("kind", "cv") in {"cv", "resume", "base_cv", "swe_cv", "ai_cv", "data_cv"}]
        if "cover" in label:
            return [d for d in valid if d.get("kind") == "cover_letter"]
        if "transcript" in label:
            return [d for d in valid if d.get("kind") == "transcript"]
        if re.search(r'degree|certificate', label):
            return [d for d in valid if d.get('kind') in {'degree_certificate', 'certificate'}]
        if 'portfolio' in label:
            return [d for d in valid if d.get('kind') == 'portfolio']
        return []  # An arbitrary attachment is not necessarily a CV.
