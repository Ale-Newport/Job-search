"""Build exact, reviewable field operations without interacting with the page."""

from pathlib import Path

from .answers import normalize, option_for, resolve_answer, selected_choice_matches


def action_key(element, operation, value=None, file_path=None, upload_name=None):
    return (
        element["document_id"],
        element["node_id"],
        str(operation),
        value,
        str(Path(file_path).resolve()) if file_path else None,
        upload_name,
    )


def section_plan(snapshot, target, adapter, facts, documents, policies, documents_for):
    section = target.get("section_id", "page")
    fields, keys = [], []
    for element in snapshot["elements"]:
        if element["document_id"] != target["document_id"] or element.get("section_id", "page") != section:
            continue
        ops = element["operations"]
        question = adapter.question(element)
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
            resolved = resolve_answer(
                question, facts, hints=[element.get("name", ""), element.get("autocomplete", "")], policies=policies
            )
            answer = resolved["answer"]
            if answer is None or resolved["leave_blank"]:
                continue
            evidence = {"fact_ids": resolved["fact_ids"], "kind": resolved["kind"]}
            if element["role"] == "combobox" and "CLICK" in ops and "SELECT" not in ops:
                if element.get("expanded") or not selected_choice_matches(question, answer, element.get("selected_text") or element.get("value", "")):
                    operation, value = "TYPE_TEXT" if "TYPE_TEXT" in ops else "CLICK", answer
            elif "TYPE_TEXT" in ops and element["value"] != answer:
                operation, value = "TYPE_TEXT", answer
            elif "SELECT" in ops:
                option = option_for(answer, element["options"])
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
            keys.append(action_key(element, operation, value, file_path, filename))
            fields.append({"question": question, "answer": answer, "operation": operation, **evidence})
    return fields, keys
