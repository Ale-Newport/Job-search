"""The cross product, generated programmatically with a fixed seed and sampled so that every (family, wrapper group) is hit
by several candidates, languages and variants, within a budget of cases that runs in about a minute.

  plain bases     every base x every candidate x its languages: the controls (and the benign wrappers' reference)
  wrapped         per (family, wrapper group) bucket: ``k`` renderings, interleaved over (variant, language), each given to
                  the next candidate for whom it applies
"""
from __future__ import annotations

import random
from collections import defaultdict

from . import bases as B
from . import wrappers as W
from .people import BY_KEY, PEOPLE
from .runner import Case

SEED = 20260714
BUDGET = 3900


def renderings():
    """(family, group) -> [(base, lang, rendering)] without the renderings that do not depend on the base repeated."""
    buckets, seen = defaultdict(list), set()
    for base in B.BASES:
        for lang in base.langs:
            for r in W.all_renderings(base, lang):
                signature = (base.family, r.wid, lang, r.text, r.kind, r.options, tuple(sorted(r.extra.items())),
                             tuple((c[0], c[1], tuple(sorted(c[2].items())), c[3]) for c in r.companions))
                if signature in seen:
                    continue
                seen.add(signature)
                buckets[(base.family, r.group)].append((base, lang, r))
    return buckets


def interleave(items):
    """Round-robin over (variant, language) so that a small quota still covers many variants and both languages."""
    groups = defaultdict(list)
    for item in items:
        groups[(item[2].variant, item[1])].append(item)
    queues = [groups[key] for key in sorted(groups)]
    out = []
    while any(queues):
        for queue in queues:
            if queue:
                out.append(queue.pop(0))
    return out


def plain_cases() -> list[Case]:
    return [Case(f"{p.key}|{b.id}|{lang}|base", p.key, b.id, lang) for p in PEOPLE for b in B.BASES for lang in b.langs]


def sample(buckets, quota: int, rng: random.Random) -> list[Case]:
    """``quota`` cases per bucket: the renderings interleaved over (variant, language), each given to the next candidate for whom
    it applies; a bucket with fewer renderings than its quota gives each of them to more candidates."""
    cases = []
    people = [p.key for p in PEOPLE]
    for index, key in enumerate(sorted(buckets)):
        items = sorted(buckets[key], key=lambda t: (t[0].id, t[1], t[2].wid))
        rng.shuffle(items)
        ordered, chosen, used, turn = interleave(items), [], set(), index  # the first candidate rotates from bucket to bucket
        for _ in range(len(people)):
            for base, lang, r in ordered:
                if len(chosen) >= quota:
                    break
                for step in range(len(people)):
                    who = people[(turn + step) % len(people)]
                    if (base.id, lang, r.wid, who) in used or (r.only is not None and not r.only(BY_KEY[who])):
                        continue
                    used.add((base.id, lang, r.wid, who))
                    chosen.append(Case(f"{who}|{base.id}|{lang}|{r.wid}", who, base.id, lang, r))
                    turn += step + 1
                    break
            if len(chosen) >= quota:
                break
        cases += chosen
    return cases


def build_plan(budget: int = BUDGET, seed: int = SEED) -> list[Case]:
    plain = plain_cases()
    buckets = renderings()
    low, high = 1, 60
    while low < high:  # the largest quota whose total fits the budget
        mid = (low + high + 1) // 2
        total = len(plain) + len(sample(buckets, mid, random.Random(seed)))
        low, high = (mid, high) if total <= budget else (low, mid - 1)
    return plain + sample(buckets, low, random.Random(seed))


def by_person(cases: list[Case]) -> dict:
    out = defaultdict(list)
    for case in cases:
        out[case.person].append(case)
    return dict(out)
