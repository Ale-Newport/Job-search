from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

from ..db import dumps, now, uid


def today():
    return datetime.now(ZoneInfo("Europe/London")).date()


def typed(value, kind):
    if kind == "date":
        return date.fromisoformat(str(value)).isoformat()
    if kind in {"number", "integer"}:
        import math

        if isinstance(value, bool):
            raise ValueError("A boolean is not a number")
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("Value must be finite")
        if kind == "integer" and not number.is_integer():
            raise ValueError("An integer is required")
        return int(number) if kind == "integer" else number
    if kind == "boolean":
        if value not in (True, False, "true", "false", "Yes", "No"):
            raise ValueError("Use Yes or No")
        return value in (True, "true", "Yes")
    if kind == "list":
        if isinstance(value, str):
            value = [x.strip() for x in value.split("\n") if x.strip()]
        if not isinstance(value, list) or any(not isinstance(x, str) for x in value):
            raise ValueError("Use a list of strings")
        return value
    if kind != "text":
        raise ValueError("Unsupported value type")
    return str(value)


def entity(db, kind, name, aliases=()):
    row = db.one("SELECT id FROM knowledge_entities WHERE kind=? AND name=?", (kind, name))
    if row:
        return row["id"]
    identifier = uid()
    db.execute("INSERT INTO knowledge_entities VALUES(?,?,?,?)", (identifier, kind, name, dumps(aliases)))
    return identifier


def ingest_fact(
    db,
    entity_id,
    concept,
    value,
    *,
    value_type="text",
    source,
    evidence,
    verified=False,
    priority=50,
    sensitivity="professional",
    unit=None,
    valid_from=None,
    valid_until=None,
    expires_at=None,
    effective_at=None,
    allowed_usage=None,
):
    """Idempotent ingestion. Conflicting evidence remains visible; never overwrites."""
    value = typed(value, value_type)
    for d in (valid_from, valid_until, expires_at):
        if d:
            date.fromisoformat(d)
    if valid_from and valid_until and valid_from > valid_until:
        raise ValueError("Invalid validity interval")
    encoded = dumps(value)
    old = db.one(
        "SELECT f.id FROM facts f JOIN knowledge_facts k ON k.fact_id=f.id "
        "WHERE k.entity_id=? AND k.concept=? AND f.value=? AND f.source=?",
        (entity_id, concept, encoded, source),
    )
    if old:
        return old["id"]
    identifier, stamp = uid(), now()
    candidate = db.one("SELECT id FROM candidates LIMIT 1")["id"]
    with db.transaction() as c:
        c.execute(
            "INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                candidate,
                "knowledge",
                concept,
                encoded,
                source,
                "verified" if verified else "unverified",
                0,
                "",
                stamp,
                stamp,
            ),
        )
        c.execute(
            "INSERT INTO knowledge_facts VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                identifier,
                entity_id,
                concept,
                value_type,
                unit,
                valid_from,
                valid_until,
                effective_at,
                expires_at,
                sensitivity,
                dumps(allowed_usage or ["application"]),
                1 if verified else 0.5,
                priority,
                dumps(evidence),
                1,
            ),
        )
    return identifier


def update_fact(db, fact_id, value, confirmed):
    from ..core import invalidate_fact_evidence

    old = db.one(
        "SELECT f.*,k.value_type FROM facts f JOIN knowledge_facts k ON k.fact_id=f.id WHERE f.id=?", (fact_id,)
    )
    if not old:
        raise ValueError("Knowledge fact not found")
    encoded = dumps(typed(value, old["value_type"]))
    with db.transaction() as c:
        invalidate_fact_evidence(db, c, old)
        c.execute(
            "UPDATE facts SET value=?,verification_status=?,locked=0,updated_at=? WHERE id=?",
            (encoded, "verified" if confirmed else "unverified", now(), fact_id),
        )
        c.execute("UPDATE knowledge_facts SET revision=revision+1,source_priority=100 WHERE fact_id=?", (fact_id,))


def link(db, subject, predicate, target, facts):
    db.execute(
        "INSERT OR IGNORE INTO knowledge_relationships VALUES(?,?,?,?,?)",
        (uid(), subject, predicate, target, dumps(facts)),
    )


def policy(db, subject, value, scope=None, *, source="User policy", confirmed=True):
    scope = json.dumps(scope or {}, sort_keys=True)
    old = db.one("SELECT * FROM candidate_policies WHERE subject=? AND scope=?", (subject, scope))
    db.execute(
        "INSERT OR REPLACE INTO candidate_policies VALUES(?,?,?,?,?,?,?,?)",
        (
            old["id"] if old else uid(),
            subject,
            dumps(value),
            scope,
            int(confirmed),
            source,
            old["revision"] + 1 if old else 1,
            now(),
        ),
    )


class Knowledge:
    def __init__(self, db, as_of=None):
        self.db = db
        self.today = date.fromisoformat(as_of) if isinstance(as_of, str) else as_of or today()
        self.entities = {
            e["id"]: {**e, "aliases": json.loads(e["aliases"])} for e in db.query("SELECT * FROM knowledge_entities")
        }
        self.rows = db.query(
            "SELECT f.*,k.* FROM knowledge_facts k JOIN facts f ON f.id=k.fact_id ORDER BY f.updated_at"
        )
        for f in self.rows:
            f["value"] = json.loads(f["value"])
            f["evidence"] = json.loads(f["evidence"])
            f["allowed_usage"] = json.loads(f["allowed_usage"])
        self.links = [
            {**r, "fact_ids": json.loads(r["fact_ids"])} for r in db.query("SELECT * FROM knowledge_relationships")
        ]
        self.policies = [
            {**r, "value": json.loads(r["value"]), "scope": json.loads(r["scope"])}
            for r in db.query("SELECT * FROM candidate_policies")
        ]
        self.version = hashlib.sha256(
            dumps([self.rows, self.links, self.policies, self.today.isoformat()]).encode()
        ).hexdigest()

    def active(self, f):
        stamp = self.today.isoformat()
        return (
            f["verification_status"] == "verified"
            and "application" in f["allowed_usage"]
            and (not f["effective_at"] or f["effective_at"] <= stamp)
            and (not f["valid_from"] or f["valid_from"] <= stamp)
            and (not f["valid_until"] or f["valid_until"] >= stamp)
            and (not f["expires_at"] or f["expires_at"] >= stamp)
        )

    def fact(self, entity_id, concept):
        pending = [
            f
            for f in self.rows
            if f["entity_id"] == entity_id
            and f["concept"] == concept
            and f["source_priority"] >= 100
            and f["verification_status"] == "unverified"
        ]
        if pending:
            newest = max(pending, key=lambda f: f["updated_at"])
            if not any(
                f["entity_id"] == entity_id
                and f["concept"] == concept
                and f["source_priority"] >= 100
                and f["verification_status"] == "verified"
                and f["updated_at"] > newest["updated_at"]
                for f in self.rows
            ):
                return None
        rows = [f for f in self.rows if f["entity_id"] == entity_id and f["concept"] == concept and self.active(f)]
        if not rows:
            return None
        priority = max(f["source_priority"] for f in rows)
        best = [f for f in rows if f["source_priority"] == priority]
        if len({dumps(f["value"]) for f in best}) != 1:
            return None  # equal-priority contradiction is not resolved by guessing
        return best[-1]

    def records(self, kind):
        result = []
        for e in self.entities.values():
            if e["kind"] != kind:
                continue
            fields = {f["concept"]: self.fact(e["id"], f["concept"]) for f in self.rows if f["entity_id"] == e["id"]}
            result.append({**e, "fields": {k: v for k, v in fields.items() if v}})
        return result

    def get_policy(self, subject, job):
        rows = [
            p
            for p in self.policies
            if p["confirmed"]
            and p["subject"] == subject
            and all(str(job.get(k, "")).casefold() == str(v).casefold() for k, v in p["scope"].items())
        ]
        if not rows:
            return None
        depth = max(len(p["scope"]) for p in rows)
        rows = [p for p in rows if len(p["scope"]) == depth]
        return rows[0] if len({dumps(p["value"]) for p in rows}) == 1 else None

    def conflicts(self):
        result = []
        for e in self.entities.values():
            for concept in {f["concept"] for f in self.rows if f["entity_id"] == e["id"]}:
                rows = [
                    f for f in self.rows if f["entity_id"] == e["id"] and f["concept"] == concept and self.active(f)
                ]
                if len({dumps(f["value"]) for f in rows}) > 1:
                    chosen = self.fact(e["id"], concept)
                    result.append(
                        {
                            "entity": e["name"],
                            "concept": concept,
                            "fact_ids": [f["id"] for f in rows],
                            "selected": chosen["id"] if chosen else None,
                            "reason": "Source priority" if chosen else "Conflicting verified evidence",
                        }
                    )
        return result


def sync_profile_fact(db, key, value, confirmed):
    """Keep edits in the established Profile editor authoritative over imported data."""
    people = db.query("SELECT id FROM knowledge_entities WHERE kind='person'")
    if len(people) != 1:
        return
    keys = {
        "full_name",
        "first_name",
        "last_name",
        "email",
        "phone",
        "location",
        "address",
        "postcode",
        "linkedin",
        "github",
        "portfolio",
        "nationality",
        "date_of_birth",
        "current_company",
        "current_title",
    }
    if key not in keys:
        return
    rows = db.query("SELECT fact_id FROM knowledge_facts WHERE entity_id=? AND concept=?", (people[0]["id"], key))
    # An explicit profile edit is recorded as a new source; competing historical
    # values remain available in the fact explorer and historical evidence.
    identifier = ingest_fact(
        db,
        people[0]["id"],
        key,
        value,
        value_type="text",
        source="application_profile:" + key,
        evidence={"source": "Explicit profile edit"},
        verified=confirmed,
        priority=100,
        sensitivity="personal",
    )
    db.execute("UPDATE knowledge_facts SET source_priority=100,revision=revision+1 WHERE fact_id=?", (identifier,))
    db.execute(
        "UPDATE facts SET verification_status=?,updated_at=? WHERE id=?",
        ("verified" if confirmed else "unverified", now(), identifier),
    )
    for row in rows:
        if row["fact_id"] != identifier:
            db.execute(
                "UPDATE knowledge_facts SET source_priority=MIN(source_priority,90) WHERE fact_id=?", (row["fact_id"],)
            )
