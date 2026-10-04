"""Build exact, reviewable field operations without interacting with the page."""

from pathlib import Path

from ..intelligence.formatting import choice as option_for
from .reasoning import conditional_state, profile_resolution
from .disclosures import disclosure_resolution, disclosure_clear_operation, inherit_disclosure_context

from .answers import normalize, dropdown_search_value


def action_key(element, operation, value=None, file_path=None, upload_name=None):
    return (
        element["document_id"],
        element["node_id"],
        str(operation),
        value,
        str(Path(file_path).resolve()) if file_path else None,
        upload_name,
    )


def radio_group(element, elements):
    """Only observed peers in this radio control's own document and form."""
    if element.get("role") != "radio":
        return []
    group = element.get("group")
    if not group:
        return [element]
    return [
        peer for peer in elements
        if peer.get("role") == "radio"
        and peer.get("group") == group
        and peer.get("document_id") == element.get("document_id")
        and peer.get("form_id") == element.get("form_id")
    ]


def resolution_element(element, elements):
    """Present native radio choices to the same enum resolver used by selects."""
    if element.get("role") != "radio":
        return element
    return {
        **element,
        "options": [
            {"label": peer["label"], "value": peer.get("value") or peer["label"],
             "disabled": not peer.get("enabled", True) or "CHECK" not in peer.get("operations", [])}
            for peer in radio_group(element, elements)
        ],
    }


def dropdown_open_target(element, elements):
    """Use only a menu toggle explicitly linked by the current DOM observation."""
    trigger_id = element.get("dropdown_trigger_id")
    if trigger_id is None or element.get("expanded"):
        return None
    return next((peer for peer in elements
                 if peer.get("document_id") == element.get("document_id")
                 and peer.get("node_id") == trigger_id and peer.get("role") == "button"
                 and "CLICK" in peer.get("operations", [])), None)


def selected_resolution_matches(resolution, value):
    return option_for(resolution["answer"], [{"label": value, "value": value}],
                      question=resolution["question"],
                      disclosure_concept=resolution.get("disclosure_concept")) is not None


def section_plan(snapshot, target, adapter, facts, documents, policies, documents_for):
    section = target.get("section_id", "page")
    fields, keys = [], []
    for element in snapshot["elements"]:
        if element["document_id"] != target["document_id"] or element.get("section_id", "page") != section:
            continue
        inherit_disclosure_context(element, snapshot["elements"], adapter)
        disclosure = disclosure_resolution(adapter.question(element), element, policies)
        if disclosure:
            clear = disclosure_clear_operation(element)
            if clear:
                operation, value = clear
                keys.append(action_key(element, operation, value))
                fields.append({"question": adapter.question(element), "answer": "", "operation": operation,
                               "reason": disclosure["reason"], "policy": disclosure["policy"]})
            continue
        condition = conditional_state(element, snapshot['elements'], adapter)
        element['_condition'] = condition or {}
        if condition and condition['applies'] is not True:
            if condition['applies'] is False and element.get('value') and 'TYPE_TEXT' in element['operations']:
                keys.append(action_key(element, 'TYPE_TEXT', ''))
                fields.append({'question': adapter.question(element), 'answer': '', 'operation': 'TYPE_TEXT', 'reason': condition['reason']})
            continue
        ops = element["operations"]
        question = adapter.question(element)
        action_target = element
        operation, value, file_path, filename = None, None, None, None
        if "UPLOAD" in ops:
            candidates = documents_for(question, documents)
            if len(candidates) != 1:
                continue
            document = candidates[0]
            filename = Path((document.get("filename") or document["path"]).replace("\\", "/")).name
            if filename in element.get("value", ""):
                continue
            operation, file_path = "UPLOAD", document["path"]
            answer = filename
            evidence = {"document_version_id": document.get("id"), "fact_ids": []}
        else:
            field = resolution_element(element, snapshot["elements"])
            resolved = profile_resolution(
                question, facts, element=field, hints=[element.get("name", ""), element.get("autocomplete", "")], policies=policies
            )
            intelligence = policies.get('_candidate_engine')
            if intelligence:
                candidate = intelligence.answer_question(question, field, policies.get('_job_context', {}))
                candidate = intelligence.confirmed_fallback(question, candidate, facts, policies)
                if candidate['canonical_intent'] != 'OTHER':
                    resolved = candidate
            answer = resolved["answer"]
            if answer is None or resolved["leave_blank"]:
                continue
            evidence = {"fact_ids": resolved["fact_ids"], "kind": resolved["kind"]}
            if element["role"] == "combobox" and "CLICK" in ops and "SELECT" not in ops:
                if element.get("expanded") or not selected_resolution_matches(resolved, element.get("selected_text") or element.get("value", "")):
                    trigger = dropdown_open_target(element, snapshot["elements"])
                    if trigger:
                        action_target, operation, value = trigger, "CLICK", None
                    else:
                        operation, value = "TYPE_TEXT" if "TYPE_TEXT" in ops else "CLICK", dropdown_search_value(resolved)
            elif "TYPE_TEXT" in ops and element["value"] != answer:
                operation, value = "TYPE_TEXT", answer
            elif "SELECT" in ops:
                option = option_for(answer, element["options"], question=question, disclosure_concept=resolved.get("disclosure_concept"))
                if option and element["value"] != option["value"]:
                    operation, value = "SELECT", option["value"]
            elif element["role"] == "radio" and "CHECK" in ops:
                if (
                    normalize(answer) in {normalize(element["label"]), normalize(element["value"])}
                    and not element["checked"]
                ):
                    operation = "CHECK"
            elif "CHECK" in ops:
                truth = normalize(answer)
                if truth in {"yes", "no", "true", "false", "1", "0"}:
                    desired = truth in {"yes", "true", "1"}
                    if desired != element["checked"]:
                        operation = "CHECK" if desired else "UNCHECK"
        if operation:
            keys.append(action_key(action_target, operation, value, file_path, filename))
            fields.append({"question": question, "answer": answer, "operation": operation, **evidence})
    return fields, keys
