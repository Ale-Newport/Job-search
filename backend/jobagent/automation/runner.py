"""Application-specific deterministic filling, review and independent submission verification."""

from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
import re

from .adapters import get_adapter
from .answers import normalize, option_for, resolve_answer
from .browser import BrowserSession, confirmation_evidence
from .browser import PROGRESS, is_submit
from .engines import make_engine
from .types import AutomationError, Decision, HumanRequired, Operation, Paused, StaleState


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

    def _result(self, status: str, snapshot: dict | None, *, answers: list | None = None,
                questions: list | None = None, steps: list | None = None,
                evidence: list | None = None, adapter: str = "generic", error: str | None = None) -> dict:
        result = {"status": status, "answers": answers or [], "questions": questions or [], "steps": steps or [],
                  "evidence": evidence or [], "snapshot_id": snapshot["id"] if snapshot else None,
                  "current_url": snapshot["url"] if snapshot else self.status()["current_url"],
                  "adapter": adapter, "error": error, "application_id": self._active_application_id,
                  "validation_errors": snapshot.get("errors", []) if snapshot else [],
                  "sensitive": any(a.get("kind") in {"disability", "ethnicity", "gender", "criminal_record",
                                                       "demographic", "legal_certification"} for a in answers or [])}
        self._last_result = result
        return result

    async def fill_application(self, application: dict, facts: list[dict], documents: list[dict],
                               mode: str = "REVIEW", settings: dict | None = None) -> dict:
        async with self._run_lock:
            return await self._fill(application, facts, documents, mode, settings or {})

    async def _fill(self, application: dict, facts: list[dict], documents: list[dict], mode: str, settings: dict) -> dict:
        url = application.get("application_url") or application.get("url")
        if not url:
            return self._result("error", None, error="This application has no URL.")
        adapter = get_adapter(url, application.get("ats"))
        same_application = self._active_application_id == application.get("id")
        self._active_application_id = application.get("id")
        self._review.clear()
        if mode.upper() == "MANUAL":
            return self._result("needs_review", None, adapter=adapter.name,
                                questions=[{"question": "Manual mode: open and complete the prepared application yourself.", "kind": "manual"}])
        answers: list[dict] = []
        questions: list[dict] = []
        steps: list[dict] = []
        snapshot = None
        policies = settings.get("sensitive_policies", {})
        self.allowed_uploads = {Path(d["path"]).resolve() for d in documents if d.get("path") and Path(d["path"]).is_file()}
        try:
            self._check_pause()
            if (same_application or settings.get("resume_existing")) and self.page and not self.page.is_closed():
                snapshot = await self.observe()
            else:
                snapshot = await self.open(url)
            if adapter.manual_only:
                return self._result("human_required", snapshot, adapter=adapter.name,
                                    questions=[{"question": "This source uses manual handoff. Complete the application in the visible browser.",
                                                "kind": "platform_restriction"}])
            max_steps = min(100, max(1, int(settings.get("browser_max_steps", 45))))
            handled: set[tuple] = set()
            page_advances = 0
            pending_dropdown = None
            while len(steps) < max_steps:
                self._check_pause()
                if snapshot["blocked"]:
                    reason = "Human verification required" if snapshot["challenge"] else "Complete sign-in or multi-factor authentication in the visible browser."
                    return self._result("human_required", snapshot, answers=answers, steps=steps, adapter=adapter.name,
                                        questions=[{"question": reason, "kind": "captcha" if snapshot["challenge"] else "authentication"}])
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
                    resolution = pending_dropdown["resolution"]
                    choices = [e for e in snapshot["elements"] if e["role"] == "option" and
                               "CLICK" in e["operations"] and normalize(e["label"]) == normalize(resolution["answer"])]
                    if len(choices) != 1:
                        return self._result("human_required", snapshot, answers=answers, steps=steps, adapter=adapter.name,
                                            questions=[{"question": resolution["question"], "kind": "option",
                                                        "reason": "No unique visible dropdown option matches the verified answer."}])
                    await self._record_step(await self.execute(Decision(Operation.CLICK, choices[0]["index"]), snapshot), steps, snapshot)
                    snapshot = await self.observe()
                    selected = next((e for e in snapshot["elements"] if e["node_id"] == pending_dropdown["node_id"] and
                                     e["document_id"] == pending_dropdown["document_id"]), None)
                    if selected is None or normalize(selected.get("selected_text") or selected.get("value", "")) != normalize(resolution["answer"]):
                        return self._result("human_required", snapshot, answers=answers, steps=steps, adapter=adapter.name,
                                            questions=[{"question": resolution["question"], "kind": "option",
                                                        "reason": "The selected dropdown value could not be independently verified."}])
                    self._add_answer(answers, {k: resolution[k] for k in ("question", "answer", "fact_ids", "verified", "confidence", "kind")})
                    pending_dropdown = None
                    continue
                for element in snapshot["elements"]:
                    operations = element["operations"]
                    if element["role"] == "combobox" and operations == ["CLICK"]:
                        resolution = resolve_answer(adapter.question(element), facts, policies=policies)
                        if resolution["answer"] is None:
                            questions.append({"question": adapter.question(element), "kind": resolution["kind"]})
                            continue
                        if normalize(element.get("selected_text", "")) == normalize(resolution["answer"]):
                            self._add_answer(answers, {k: resolution[k] for k in ("question", "answer", "fact_ids", "verified", "confidence", "kind")})
                            continue
                        pending_dropdown = {"resolution": resolution, "node_id": element["node_id"],
                                            "document_id": element["document_id"]}
                        action = Decision(Operation.CLICK, element["index"])
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
                            if element.get("required") or not element.get("value"):
                                questions.append({"question": field or "Select a document to upload", "kind": "document", "target": element["index"]})
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
                    resolution = resolve_answer(field, facts, hints=[element.get("name", ""), element.get("autocomplete", "")], policies=policies)
                    if resolution["leave_blank"]:
                        if element.get("required"):
                            questions.append({"question": field, "kind": resolution["kind"], "reason": "This field is required but your policy is to leave it blank."})
                        if not element.get("required"):
                            handled.add(signature)
                        continue
                    if resolution["answer"] is None:
                        questions.append({"question": field, "kind": resolution["kind"], "target": element["index"],
                                          "current_value": element["value"], "required": element.get("required", False)})
                        # Do not mark unknown fields handled: questions must survive the next observation.
                        continue
                    answer = resolution["answer"]
                    operation = None
                    if "TYPE_TEXT" in operations:
                        if element["value"] != answer:
                            operation, value = Operation.TYPE_TEXT, answer
                    elif "SELECT" in operations:
                        option = option_for(answer, element["options"])
                        if option is None:
                            questions.append({"question": field, "kind": "option", "reason": "The verified answer does not match any observed option."})
                            continue
                        if element["value"] != option["value"]:
                            operation, value = Operation.SELECT, option["value"]
                    elif element["role"] == "radio":
                        if normalize(answer) not in {normalize(element["label"]), normalize(element["value"])}:
                            # Resolve the group as a whole, not one radio independently.
                            siblings = [e for e in snapshot["elements"] if e.get("group") == element.get("group") and e["role"] == "radio"]
                            has_option = any(normalize(answer) in {normalize(e["label"]), normalize(e["value"])} for e in siblings)
                            if not has_option:
                                questions.append({"question": field, "kind": "option", "reason": "No radio option matches the verified answer."})
                            else:
                                handled.add(signature)
                            continue
                        if not element["checked"]:
                            operation = Operation.CHECK
                    else:
                        truth = normalize(answer)
                        if truth not in {"yes", "no", "true", "false", "1", "0"}:
                            questions.append({"question": field, "kind": "checkbox", "reason": "A verified yes/no answer is required."})
                            continue
                        desired = truth in {"yes", "true", "1"}
                        if desired != element["checked"]:
                            operation = Operation.CHECK if desired else Operation.UNCHECK
                    self._add_answer(answers, {k: resolution[k] for k in ("question", "answer", "fact_ids", "verified", "confidence", "kind")})
                    handled.add(signature)
                    if operation:
                        action = Decision(operation, element["index"])
                        break
                if action:
                    result = await self.execute(action, snapshot, value=value, file_path=file_path,
                                                upload_name=upload_name, upload_mime=upload_mime,
                                                minimum_confidence=float(settings.get("browser_confidence_threshold", settings.get("min_confidence", .85))))
                    await self._record_step(result, steps, snapshot)
                    snapshot = await self.observe()
                    continue
                questions.extend({"question": e["label"] or "Required hidden control", "kind": "unsupported_widget",
                                  "reason": "A required form control is hidden and needs manual handling."}
                                 for e in snapshot.get("unresolved_required", []))
                if any(frame.get("unavailable") for frame in snapshot["frames"]):
                    questions.append({"question": "A frame could not be inspected. Check the complete application in the browser.",
                                      "kind": "unavailable_frame"})
                questions = self._dedupe_questions(questions)
                if questions:
                    return self._result("human_required", snapshot, answers=answers, questions=questions,
                                        steps=steps, adapter=adapter.name)
                if snapshot["errors"]:
                    return self._result("human_required", snapshot, answers=answers, steps=steps, adapter=adapter.name,
                                        questions=[{"question": e, "kind": "validation"} for e in snapshot["errors"]])
                submit = adapter.submit_button(snapshot)
                if submit:
                    self._review[snapshot["id"]] = {"application_id": self._active_application_id, "adapter": adapter.name,
                                                    "mode": mode.upper(), "answers": answers, "steps": steps,
                                                    "sensitive": any(a.get("kind") in {"disability", "ethnicity", "gender", "criminal_record", "demographic", "legal_certification"} for a in answers)}
                    return self._result("needs_review", snapshot, answers=answers, steps=steps, adapter=adapter.name)
                next_button = adapter.next_button(snapshot)
                if next_button is None:
                    next_button = await self._decide_safe_progress(snapshot, settings)
                if next_button and page_advances < 8:
                    previous = snapshot["fingerprint"]
                    decision = next_button.pop("_decision", None) or Decision(Operation.CLICK, next_button["index"])
                    await self._record_step(await self.execute(decision, snapshot), steps, snapshot)
                    page_advances += 1
                    snapshot = await self._wait_for_change(previous)
                    if snapshot["fingerprint"] == previous:
                        return self._result("human_required", snapshot, answers=answers, steps=steps, adapter=adapter.name,
                                            questions=[{"question": "The next step did not open. Check form validation in the browser.", "kind": "validation"}])
                    continue
                return self._result("human_required", snapshot, answers=answers, steps=steps, adapter=adapter.name,
                                    questions=[{"question": "No unambiguous next step or final Submit was observed. Continue in the browser.", "kind": "unsupported_widget"}])
            return self._result("blocked", snapshot, answers=answers, steps=steps, adapter=adapter.name,
                                error="The bounded browser step budget was reached.")
        except (HumanRequired, StaleState, Paused) as error:
            return self._result("human_required", snapshot, answers=answers, steps=steps, adapter=adapter.name,
                                questions=[{"question": str(error), "kind": error.code}], error=str(error))
        except Exception as error:
            self._last_error = str(error)[:500]
            return self._result("error", snapshot, answers=answers, steps=steps, adapter=adapter.name, error=self._last_error)

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
                                    "snapshot_id": snapshot["id"], "observation": snapshot})
            if inspect.isawaitable(outcome):
                await outcome

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
        if not any(a["question"] == answer["question"] and a["answer"] == answer["answer"] for a in answers):
            answers.append(answer)

    @staticmethod
    def _dedupe_questions(questions: list[dict]) -> list[dict]:
        seen = set()
        result = []
        for question in questions:
            key = (question["question"], question["kind"])
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
        return valid if len(valid) == 1 else []
