"""Runs cases through the REAL pipeline (bridge routes -> SuggestService -> FieldResolver -> CandidateEngine) with the harness
of the policy matrix: a real app per candidate (temp data dir, no network, no writing model), one session on the candidate's
page, and one ``/suggest`` request per case (``batch=1``: no case can influence another) or per page of cases (``batch=n``:
the way a real form is scanned)."""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[2] / "extension"
if str(HARNESS) not in sys.path:
    sys.path.insert(0, str(HARNESS))

from . import bases as B  # noqa: E402
from . import people  # noqa: E402

INSERTABLE = {"VERIFIED", "INFERRED", "GENERATED_DRAFT"}


@dataclass(frozen=True)
class Case:
    cid: str
    person: str
    base: str
    lang: str
    rendering: object | None = None  # a wrappers.Rendering, None for the plain base

    @property
    def wid(self) -> str:
        return "base" if self.rendering is None else self.rendering.wid


@dataclass(frozen=True)
class Obs:
    state: str
    insert: str
    value: str | None
    protected: bool
    review: bool
    why: str | None
    root_cause: str | None
    question: str
    leave_blank: bool = False

    @property
    def triple(self) -> tuple:
        state = "GENERATING" if self.state in {"GENERATING", "GENERATED_DRAFT"} else self.state
        return (state, self.insert, self.value)

    @property
    def insertable(self) -> bool:
        return self.state in INSERTABLE

    def show(self) -> str:
        shown = f" = {self.value!r}" if self.value is not None else ""
        return f"{self.state}/{self.insert}{shown}" + (" [review]" if self.review else "") + (" [protected]" if self.protected else "")


def value_of(suggestion) -> str | None:
    if not suggestion:
        return None
    if suggestion.get("option"):
        return suggestion["option"].get("label")
    if suggestion.get("labels"):
        return ", ".join(suggestion["labels"])
    if suggestion.get("text") is not None:
        return suggestion["text"]
    if suggestion.get("checked") is not None:
        return "checked" if suggestion["checked"] else "unchecked"
    return suggestion.get("display")


def observe(result: dict | None) -> Obs:
    if result is None:
        return Obs("SKIP", "never", None, False, False, None, None, "")
    flags = result.get("flags") or {}
    return Obs(result["state"], result["insert"], value_of(result.get("suggestion")), bool(flags.get("protected")),
               bool(flags.get("requires_review")), (result.get("why") or {}).get("code"),
               (result.get("needs") or {}).get("root_cause"), result.get("question") or "", bool(flags.get("leave_blank")))


class Builder:
    """The controls of a case, as the content script reports them (a radio group is one assist field: its first member)."""

    def __init__(self):
        self.node = 1
        self.group = 0

    def take(self) -> int:
        node, self.node = self.node, self.node + 100
        return node

    def control(self, kind: str, text: str, options: tuple, extra: dict, default_options: tuple):
        from harness import el, radios, select  # noqa: PLC0415
        node, self.group = self.take(), self.group + 1
        extra = dict(extra)
        if kind == "radio":
            members = radios(node, text, list(options or default_options), group=f"g{self.group}", **extra)
            return members, node
        if kind == "select":
            return [select(node, text, list(options), **extra)], node
        if kind == "checkbox":
            return [el(node, "checkbox", text, group=f"g{self.group}", **extra)], node
        if kind == "textarea":
            return [el(node, "textbox", text, tag="textarea", type="textarea", **extra)], node
        return [el(node, "textbox", text, **{"type": kind, **extra})], node

    def case(self, case: Case, unique_section: bool = False):
        base, r = B.BY_ID[case.base], case.rendering
        text = base.text(case.lang) if r is None else r.text
        kind = base.kind if (r is None or r.kind is None) else r.kind
        options = base.opts(case.lang) if (r is None or r.options is None) else r.options
        extra = {} if r is None else dict(r.extra)
        if unique_section:  # unrelated questions of one scan are not one section (a signature must not turn a name into one)
            extra.setdefault("section_id", f"section-{self.group + 1}")
        elements, main = self.control(kind, text, options, extra, B.YES_NO[case.lang])
        companions = []
        for ckind, ctext, cextra, wanted in (() if r is None else r.companions):
            more, node = self.control(ckind, ctext, options if ckind == kind else base.opts(case.lang),
                                      {**({"section_id": extra["section_id"]} if unique_section else {}), **cextra},
                                      B.YES_NO[case.lang])
            elements += more
            companions.append((node, wanted))
        return elements, main, companions


async def scan(person: people.Person, cases: list, *, disclosures="use_confirmed", tab_protected=False, batch=1,
               on_progress=None) -> dict:
    """-> {cid: (Obs, [(Obs, wanted)])}"""
    from harness import add_cv, bridge, http, make_app, posting, put_setting, suggest_body  # noqa: F401, PLC0415

    out = {}
    with tempfile.TemporaryDirectory(prefix="meridian-metamorphic-") as tmp:
        app = make_app(Path(tmp), seed=False, text_model=False)
        people.seed(app.state.db, person, disclosures=disclosures)
        if tab_protected:
            put_setting(app.state.db, "extension_tab_inserts_protected", True)
        try:
            async with http(bridge(app)) as client:
                created = (await client.post("/v1/sessions", json=people.page_for(person, posting))).json()
                sid = created["session_id"]
                await app.state.extension_research.wait(created["research"]["run_id"], 120)
                builder = Builder()
                for start in range(0, len(cases), batch):
                    chunk, elements, tracked = cases[start:start + batch], [], []
                    for case in chunk:
                        built, main, companions = builder.case(case, unique_section=batch > 1)
                        elements += built
                        tracked.append((case, main, companions))
                    assert len(elements) <= 400
                    response = await client.post(f"/v1/sessions/{sid}/suggest",
                                                 json=suggest_body(elements, document_id=f"doc-{start}"))
                    assert response.status_code == 200, response.text
                    results = {s["node_id"]: s for s in response.json()["results"]}
                    for case, main, companions in tracked:
                        out[case.cid] = (observe(results.get(main)), [(observe(results.get(n)), wanted) for n, wanted in companions])
                    if on_progress:
                        on_progress(len(out))
        finally:
            await app.state.extension_sessions.close()
            await app.state.extension_generation.close()
            await app.state.extension_research.close()
    return out


def run_person(person, cases, **kw) -> dict:
    os.environ.setdefault("MERIDIAN_TEST", "1")
    os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    return asyncio.run(scan(person, cases, **kw))


def run_all(plan: dict, **kw) -> dict:
    """plan: {person key: [Case]} -> {cid: (Obs, companions)}; one worker thread with its own event loop (as the matrix)."""
    results = {}
    with ThreadPoolExecutor(max_workers=1) as pool:
        for key, cases in plan.items():
            if cases:
                results.update(pool.submit(run_person, people.BY_KEY[key], cases, **kw).result())
    return results
