"""The report of a run: totals, pass rates by family and by wrapper, the failure CLASSES (family x wrapper x property) ranked by
frequency with one example each, and every violation grouped so that a fixer reads classes, not cases."""
from __future__ import annotations

from collections import Counter, defaultdict

from . import bases as B
from .check import LABELS, Violation
from .driver import Outcome


def clip(text: str, width: int = 118) -> str:
    text = text.replace("\n", "\\n")
    return text if len(text) <= width else text[:width - 1] + "…"


def describe(v: Violation) -> str:
    who, base, lang = v.case.person, v.case.base, v.case.lang
    wrapper = "plain base" if v.case.rendering is None else v.case.rendering.wid
    lines = [f"    [{v.prop}] {who} | {base} | {lang} | {wrapper}",
             f"        question : {clip(v.text)}",
             f"        answer   : {v.got}",
             f"        oracle   : {v.expected}"]
    if v.detail:
        lines.append(f"        why      : {clip(v.detail)}")
    return "\n".join(lines)


def render_group(violations: list, limit: int = 6) -> str:
    """The text of a failing (family, wrapper) test: the classes, then examples."""
    classes = Counter(v.prop for v in violations)
    head = ", ".join(f"{prop} x{n}: {LABELS[prop]}" for prop, n in classes.most_common())
    shown, seen = [], Counter()
    for v in violations:
        if seen[v.prop] < max(1, limit // len(classes)):
            seen[v.prop] += 1
            shown.append(describe(v))
    more = len(violations) - len(shown)
    return f"{len(violations)} violation(s): {head}\n" + "\n".join(shown) + (f"\n    ... and {more} more" if more > 0 else "")


def table(rows: list, headers: tuple) -> str:
    widths = [max(len(str(r[i])) for r in [headers, *rows]) for i in range(len(headers))]
    line = lambda r: "  ".join(str(c).ljust(w) if i == 0 else str(c).rjust(w) for i, (c, w) in enumerate(zip(r, widths)))  # noqa: E731
    return "\n".join([line(headers), line(tuple("-" * w for w in widths)), *[line(r) for r in rows]])


def render(outcome: Outcome, *, command: str = "", top: int = 10, examples: int = 3, full: bool = True) -> str:
    cases, violations = outcome.cases, outcome.violations
    failing = outcome.violated_ids
    out = ["ENGINE METAMORPHIC SUITE (held-out answer-safety properties of the real suggest pipeline)", ""]
    if command:
        out.append(f"command      : {command}")
    out.append(f"cases        : {len(cases)}  ({sum(1 for c in cases if c.rendering is None)} plain bases + "
               f"{sum(1 for c in cases if c.rendering is not None)} wrapped)")
    out.append(f"candidates   : {len({c.person for c in cases})}   families: {len({B.BY_ID[c.base].family for c in cases})}   "
               f"wrapper groups: {len({c.rendering.group for c in cases if c.rendering})}   languages: en/es")
    out.append("time         : " + ", ".join(f"{k} {v:.1f}s" for k, v in outcome.seconds.items() if k != "cpu")
               + f"   (cpu {outcome.seconds.get('cpu', 0):.0f}s)")
    out.append("control runs : " + ", ".join(f"{k} {n} cases" for k, n in outcome.controls.items()))
    for note in outcome.notes:
        out.append("note         : " + note)
    signed = sum(1 for c in cases if B.BY_ID[c.base].cls == "G" and c.rendering is None and outcome.results[c.cid][0].insertable)
    if signed:
        out.append(f"note         : {signed} plain typed-signature fields were answered with the candidate's own name on Enter (explicit "
                   "tier), the attestation exception pinned by CPL-02 / SAFE-16; consent, certification and conviction never are")
    out.append(f"RESULT       : {len(cases) - len(failing & {c.cid for c in cases})} cases clean, "
               f"{len(failing)} cases with a violation ({len(violations)} violations)")
    out.append("")

    out.append("VIOLATIONS BY PROPERTY")
    by_prop = Counter(v.prop for v in violations)
    out.append(table([(p, n, LABELS[p]) for p, n in sorted(by_prop.items())] or [("-", 0, "none")], ("prop", "count", "meaning")))
    out.append("")

    def breakdown(key):
        totals, bad = Counter(), defaultdict(set)
        for c in cases:
            totals[key(c)] += 1
        for v in violations:
            bad[key(v.case)].add(v.case.cid)
        rows = [(k, totals[k], len(bad[k]), f"{100 * (totals[k] - len(bad[k])) / totals[k]:.0f}%") for k in sorted(totals)]
        return table(rows, ("", "cases", "failing", "clean"))

    out += ["PASS / FAIL BY FAMILY", breakdown(lambda c: B.BY_ID[c.base].family), ""]
    out += ["PASS / FAIL BY WRAPPER GROUP (base = the plain questions)", breakdown(lambda c: "base" if c.rendering is None else c.rendering.group), ""]
    out += ["PASS / FAIL BY CANDIDATE", breakdown(lambda c: c.person), ""]

    classes = defaultdict(list)
    for v in violations:
        classes[v.cls].append(v)
    ranked = sorted(classes.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    out.append(f"TOP {min(top, len(ranked))} FAILURE CLASSES (family / wrapper / property), most frequent first")
    for n, ((family, group, prop), items) in enumerate(ranked[:top], start=1):
        out += [f"{n:2d}. {family} / {group} / {prop}  x{len(items)}  ({LABELS[prop]})", describe(items[0])]
    out.append("")

    out.append("COVERAGE (the pipeline must not pass by never answering)")
    answered, total = Counter(), Counter()
    for c in cases:
        if c.rendering is None and B.BY_ID[c.base].cls in {"S", "V", "I", "D"}:
            from .check import expectation
            if expectation(c).known:
                total[B.BY_ID[c.base].family] += 1
                answered[B.BY_ID[c.base].family] += outcome.results[c.cid][0].insertable
    out.append(table([(f, total[f], answered[f], f"{100 * answered[f] / total[f]:.0f}%") for f in sorted(total)],
                     ("family (plain bases the facts settle)", "cases", "answered", "rate")))
    out.append("")

    if full and ranked:
        out.append("ALL FAILURE CLASSES")
        for (family, group, prop), items in ranked:
            out.append(f"- {family} / {group} / {prop}  x{len(items)}")
            out += [describe(v) for v in items[:examples]]
    return "\n".join(out)
