"""What a draft was written FROM, and whether it still holds (SAFE-11, CPL-09).

A draft is kept for the page (an hour) so that refocusing a field shows it again at no cost. The cache key deliberately does
not contain the profile version: editing a fact must not spend money by itself. But a draft that cites a fact the user has
since corrected, un-verified or deleted is a statement the user has just declared wrong, and it was offered again labelled
"drafted from your verified profile" (and stamped with the NEW knowledge version, so the page's own staleness check could not
tell). So every finished draft carries its **basis**:

* ``facts``: a digest (value, verification, lock) of every fact it cites, taken from the profile as the model was given it;
* ``research_bundle`` / ``company``: the research bundle the company pages came from, and the company it was about.

:func:`verdict` compares the basis with now, without any model call:

* a cited fact changed, was un-verified or removed -> ``outdated`` (not offered: "write a new draft", the user's choice);
* the user said "this is NOT <company>" -> ``outdated`` (the draft talks about the company they rejected);
* research learned more since (a new bundle) -> still offered, but ``stale`` (the panel says it is based on older research).
"""

from __future__ import annotations

import hashlib

MAX_CITED = 60


def _digest(row: dict) -> str:
    return hashlib.sha256("|".join([str(row["value"]), str(row["verification_status"]), str(int(row["locked"] or 0))])
                          .encode()).hexdigest()[:16]


def snapshot(db) -> dict[str, str]:
    """``{fact id: digest}`` of every fact now (a few hundred rows): taken BEFORE a draft is written, so the digest a draft
    carries is that of the facts the model was given, not of whatever the profile became meanwhile."""
    return {row["id"]: _digest(row) for row in db.query("SELECT id,value,verification_status,locked FROM facts")}


def cited_fact_ids(result: dict) -> list[str]:
    """Every fact a finished answer cites: its ``fact_ids`` and those of each of its claims."""
    ids = {str(i) for i in (result.get("fact_ids") or []) if i}
    for claim in result.get("claims") or []:
        if isinstance(claim, dict):
            ids |= {str(i) for i in (claim.get("fact_ids") or []) if i}
    return sorted(ids)[:MAX_CITED]


def stamp(result: dict, before: dict[str, str], *, bundle: str, company_key: str, company_name: str = "") -> dict:
    """The basis of a draft: digests of the facts it cites (as they were in ``before``), the research bundle and the company
    it was about (its key, and its name for the sentence that says the draft no longer holds)."""
    return {"facts": {fid: before[fid] for fid in cited_fact_ids(result) if fid in before},
            "research_bundle": bundle or "", "company": company_key or "", "company_name": company_name or ""}


def current_digests(db, ids) -> dict[str, str]:
    ids = list(ids)
    if not ids:
        return {}
    marks = ",".join("?" * len(ids))
    return {row["id"]: _digest(row) for row in db.query(
        f"SELECT id,value,verification_status,locked FROM facts WHERE id IN ({marks})", ids)}


def verdict(db, basis: dict | None, *, bundle: str, bundle_settled: bool, rejected: set | frozenset) -> dict:
    """``{status: fresh | stale | outdated, reason, facts: [changed fact ids]}`` for a draft written from ``basis``.

    A draft with no basis (a cached answer found without a model) cannot be compared: it is fresh."""
    if not basis:
        return {"status": "fresh", "reason": None, "facts": []}
    if basis.get("company") and basis["company"] in rejected:
        return {"status": "outdated", "reason": "company_rejected", "facts": [], "company": basis.get("company_name", "")}
    wanted = basis.get("facts") or {}
    now = current_digests(db, wanted)
    changed = sorted(fid for fid, digest in wanted.items() if now.get(fid) != digest)
    if changed:
        return {"status": "outdated", "reason": "facts_changed", "facts": changed}
    if bundle_settled and (basis.get("research_bundle") or "") != (bundle or ""):
        return {"status": "stale", "reason": "research_changed", "facts": []}
    return {"status": "fresh", "reason": None, "facts": []}
