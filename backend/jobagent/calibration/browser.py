"""Fill-only browser boundary and independent DOM evidence for calibration."""

import re

from ..automation.runner import BrowserManager
from ..automation.reasoning import conditional_state
from ..automation.answers import selected_choice_matches, normalize, question_identity
from ..intelligence.formatting import choice


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
    recorded = {a['question_id']:a for a in result.get('answers', []) if a.get('question_id')}
    by_question = {}
    for answer in result.get('answers', []):
        by_question.setdefault(normalize(answer['question']), []).append(answer)
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
        question_id = question_identity(job.get('id'), field, question)
        candidates = by_question.get(normalize(question), [])
        answer = recorded.get(question_id) or (candidates[0] if len(candidates) == 1 else None)
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
        if field.get('type') == 'tel' and expected is not None and value:
            # ATS phone widgets can insert spacing without changing the number.
            # Compare the complete digit sequence, including the country code.
            expected_digits = re.sub(r'\D', '', str(expected))
            observed_digits = re.sub(r'\D', '', str(value))
            match = bool(7 <= len(expected_digits) <= 15 and expected_digits == observed_digits)
        if field.get('multiple'):
            if field.get('tag') == 'select':
                selected = [o['label'] for o in field['options'] if o['value'] in field.get('selected_values', [])]
            else:
                selected = [e['label'] for e in snapshot['elements'] if e.get('group') == field.get('group')
                            and e.get('document_id') == field.get('document_id')
                            and e.get('form_id') == field.get('form_id') and e.get('checked')]
            wanted = (answer or reasoning).get('form_value')
            value = selected
            match = bool(isinstance(wanted, list) and len(wanted) == len(selected) and all(
                choice(v, [{'label': actual, 'value': actual} for actual in selected], question=question)
                for v in wanted))
        if (answer or reasoning).get('leave_blank') and not value:
            match = True
        if field.get('role') == 'combobox' and field.get('tag') != 'select':
            diagnostic = next((d for d in result.get('answer_diagnostics', [])
                               if d.get('question_id') == question_id), {})
            match = match and not field.get('expanded') and bool(
                field.get('selected_text') or diagnostic.get('selection_verified'))
        match = match and (field.get('validity') or {}).get('valid', True)
        fields.append(
            {
                "question": question,
                "question_id": question_id,
                "name": field.get('name'),
                "section_label": field.get('section_label'),
                "document_id": field.get('document_id'),
                "node_id": field.get('node_id'),
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

