"""Explicit candidate corrections, separate from document import and history.

The supplied payload must contain an explicitly verified user source. Running the same import twice
is a no-op; a later profile edit cannot be reverted by replaying the correction.
No guessed schooling, immigration conditions, consent or offer facts are added.
"""

from ..db import dumps, now
from ..automation.answers import normalize
from .store import entity, ingest_fact, Knowledge, policy

def apply_authoritative_candidate_updates(db, bundle):
    """Persist explicit user facts without replacing historical application text."""
    from ..core import invalidate_fact_evidence

    source = bundle["source"]
    if not isinstance(source, str) or not source.strip():
        raise ValueError("Explicit user correction source is required")
    changed = []

    def existing_entity(kind, name, aliases=()):
        candidates = db.query("SELECT * FROM knowledge_entities WHERE kind=?", (kind,))
        if kind == "person" and len(candidates) == 1:
            return candidates[0]["id"]
        names = {normalize(x) for x in (name, *aliases)}
        found = next((row for row in candidates if normalize(row["name"]) in names), None)
        return found["id"] if found else entity(db, kind, name, aliases)

    def put(eid, concept, value, kind):
        if db.one("SELECT f.id FROM facts f JOIN knowledge_facts k ON k.fact_id=f.id WHERE k.entity_id=? AND k.concept=? AND f.source=?", (eid, concept, source)):
            return
        before = db.query("SELECT f.* FROM facts f JOIN knowledge_facts k ON k.fact_id=f.id WHERE k.entity_id=? AND k.concept=?", (eid, concept))
        vtype = "boolean" if isinstance(value, bool) else "number" if isinstance(value, (int, float)) else "list" if isinstance(value, list) else "text"
        fid = ingest_fact(
            db, eid, concept, value, value_type=vtype, source=source,
            evidence={"source": "User's corrected Candidate Knowledge Base", "date": bundle.get("verified_at"), "excerpt": str(value)},
            verified=True, priority=100, sensitivity="professional" if concept.endswith(("interests", "preferences", "goals")) else "personal" if kind in {"person", "immigration"} else "professional",
        )
        with db.transaction() as conn:
            seen = set()
            for old in before:
                if old["value"] != dumps(value):
                    invalidate_fact_evidence(db, conn, old, seen)
                conn.execute("UPDATE knowledge_facts SET source_priority=MIN(source_priority,90),revision=revision+1 WHERE fact_id=?", (old["id"],))
            conn.execute("UPDATE facts SET updated_at=? WHERE id=?", (now(), fid))
        changed.append({"entity_id": eid, "concept": concept, "fact_id": fid})

    for row in bundle.get("entities", []):
        kind = row["kind"]
        matched = None
        if row.get("match_country"):
            matched = next((record for record in Knowledge(db).records(kind)
                            if record["fields"].get("country", {}).get("value") == row["match_country"]), None)
        eid = matched["id"] if matched else existing_entity(kind, row["name"], row.get("aliases", []))
        for concept, value in row.get("facts", {}).items():
            put(eid, concept, value, kind)
    for record in bundle.get("policies", []):
        if not db.one("SELECT id FROM candidate_policies WHERE subject=? AND source=?", (record["subject"], source)):
            policy(db, record["subject"], record["value"], record.get("scope"), source=source, confirmed=True)
    return {"source": source, "facts_added": len(changed), "changes": changed,
            "preserved": ["email", "phone", "historical_application_answers", "documents", "jobs"],
            "missing_facts": bundle.get("missing_facts", [])}
