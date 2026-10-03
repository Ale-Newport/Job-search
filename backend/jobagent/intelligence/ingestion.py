"""Structured import boundary. Extracted proposals never replace verified data."""

from .store import entity, ingest_fact, link, policy


def import_bundle(db, bundle, *, trusted_user_input=False):
    identifiers = {}
    fact_ids = {}
    for row in bundle.get("entities", []):
        identifier = entity(db, row["kind"], row["name"], row.get("aliases", []))
        identifiers[row["key"]] = identifier
        fact_ids[row["key"]] = []
        for concept, spec in row.get("facts", {}).items():
            if not isinstance(spec, dict) or "value" not in spec:
                spec = {"value": spec}
            value = spec["value"]
            kind = spec.get("type") or (
                "boolean"
                if isinstance(value, bool)
                else "number"
                if isinstance(value, (int, float))
                else "list"
                if isinstance(value, list)
                else "text"
            )
            fid = ingest_fact(
                db,
                identifier,
                concept,
                value,
                value_type=kind,
                source=bundle["source"],
                evidence={
                    "source": bundle["source"],
                    "entity": row["name"],
                    "excerpt": spec.get("evidence", str(value)),
                },
                verified=bool(trusted_user_input and spec.get("verified", True)),
                priority=90 if trusted_user_input else 40,
                sensitivity=spec.get(
                    "sensitivity", "personal" if row["kind"] in {"person", "immigration"} else "professional"
                ),
                unit=spec.get("unit"),
                valid_from=spec.get("valid_from"),
                valid_until=spec.get("valid_until"),
                expires_at=spec.get("expires_at"),
            )
            fact_ids[row["key"]].append(fid)
        for skill in row.get("skills", []):
            sid = entity(db, "skill", skill)
            evidence = [
                f
                for f in fact_ids[row["key"]]
                if db.one("SELECT key FROM facts WHERE id=?", (f,))["key"] == "technologies"
            ]
            if evidence:
                link(db, sid, "used_in", identifier, evidence)
    for p in bundle.get("policies", []):
        policy(db, p["subject"], p["value"], p.get("scope"), source=bundle["source"], confirmed=trusted_user_input)
    return {"entities": len(identifiers), "facts": sum(map(len, fact_ids.values()))}


def document_proposals(db, document_id):
    """Reuse the CV parser; proposals stay unverified, including immigration documents."""
    from ..cv_parser import propose_cv_facts

    doc = db.one("SELECT * FROM document_versions WHERE id=?", (document_id,))
    if not doc:
        raise ValueError("Document not found")
    # Existing document text is a source, never instructions or a permission grant.
    return {
        "document_id": document_id,
        "text": doc["text"],
        "proposals": propose_cv_facts(doc["text"], "document:" + document_id)[0],
        "immigration_proposals": immigration_proposals(doc["text"], "document:" + document_id),
    }


def immigration_proposals(text, source):
    """Extract document-labelled values as proposals, never immigration determinations."""
    import re

    proposals = []
    route = re.search(
        r"(?:immigration (?:route|status)|visa type|type of permission)\s*[:\n]\s*(Student|Graduate|Skilled Worker|Settled status|Pre-settled status)\b",
        text,
        re.I,
    )
    if route:
        proposals.append(
            {
                "concept": "route",
                "value": route[1],
                "type": "text",
                "verified": False,
                "evidence": route[0],
                "source": source,
            }
        )
    for label, concept in [
        ("valid from", "valid_from"),
        ("valid until", "valid_until"),
        ("expiry date", "valid_until"),
    ]:
        match = re.search(label + r"\s*[:\n]\s*(\d{4}-\d{2}-\d{2})", text, re.I)
        if match:
            from datetime import date

            try:
                value = date.fromisoformat(match[1]).isoformat()
            except ValueError:
                continue
            proposals.append(
                {
                    "concept": concept,
                    "value": value,
                    "type": "date",
                    "verified": False,
                    "evidence": match[0],
                    "source": source,
                }
            )
    return proposals
