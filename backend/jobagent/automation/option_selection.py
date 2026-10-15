"""Choose real form options from supported facts; local inference only."""

from __future__ import annotations

import hashlib
import json

from functools import lru_cache

from ..intelligence.formatting import choice, question_concept
from .answers import normalize
from .engines import SystemOneEngine
from .types import HumanRequired, InvalidDecision

_ABSENT = object()


@lru_cache(maxsize=1 << 16)
def _supports(answer: str, question: str, concept, label, value, role) -> bool:
    """Does this ONE option, on its own, carry the answer? ``choice`` reads nothing else of an option than these
    (an option's own label, whether it has a usable value, whether it is a listbox option), so a menu that repeats (the
    same country list under several questions, one scan after another) is judged once."""
    option = {}
    if label is not _ABSENT:
        option['label'] = label
    if value is not _ABSENT:
        option['value'] = value
    if role is not _ABSENT:
        option['role'] = role
    return bool(choice(answer, [option], question=question, disclosure_concept=concept))


def supports(answer: str, option: dict, *, question: str, concept) -> bool:
    key = (answer, question, concept, option.get('label', _ABSENT), option.get('value', _ABSENT),
           option.get('role', _ABSENT))
    try:
        return _supports(*key)
    except TypeError:  # an option carrying something unhashable: judge it directly
        return bool(choice(answer, [option], question=question, disclosure_concept=concept))


class OptionSelector:
    def __init__(self, settings: dict, engine=None):
        self.settings = settings
        self.engine = engine
        self.cache: dict[str, dict | None] = {}
        self.unavailable = False

    async def select(self, resolution: dict, options: list[dict], answer=None) -> dict | None:
        answer = resolution.get('answer') if answer is None else answer
        if answer is None or resolution.get('leave_blank'):
            return None
        text, question = str(answer), resolution['question']
        # What the question asks is the same for every option of the menu: read it once (``choice`` would read it again
        # for each of the options, and again to normalise all of them).
        concept = resolution.get('disclosure_concept') or question_concept(question)
        kwargs = {'question': question, 'disclosure_concept': concept}
        # Validate meaning against the visible label, not a saved DOM value.
        supported = [option for option in options if not option.get('disabled') and
                     supports(text, option, question=question, concept=concept)]
        if not supported:
            return None
        preferred = choice(text, supported, **kwargs)
        target = normalize(text)
        exact = [o for o in supported if normalize(o['label']) == target]
        if len(exact) == 1:
            return {'option': exact[0], 'engine': 'exact', 'confidence': 1.0}
        key = hashlib.sha256(json.dumps([resolution['question'], str(answer), resolution.get('fact_ids'),
            resolution.get('disclosure_concept'), supported], sort_keys=True, default=str).encode()).hexdigest()
        if key in self.cache:
            return self.cache[key]
        result = None
        local_enabled = self.settings.get('browser_engine', 'deterministic') in {'laya', 'hybrid'}
        if (not self.unavailable and (local_enabled or self.engine is not None)
                and resolution.get('verified') and resolution.get('fact_ids') and len(supported) <= 19):
            engine = self.engine or SystemOneEngine('laya', endpoint=self.settings.get('laya_endpoint'),
                minimum_confidence=float(self.settings.get('browser_confidence_threshold', .85)))
            try:
                result = await engine.choose_option(resolution['question'], str(answer), supported, resolution['fact_ids'])
                if result.get('option') not in supported:
                    raise InvalidDecision('The option decision escaped its observed supported choice set.')
            except (HumanRequired, InvalidDecision) as error:
                self.unavailable = 'unavailable' in str(error).lower()
                result = None
        # An unavailable or uncertain model cannot invalidate an independently
        # established equivalent. Ambiguous mappings stay unanswered.
        if result is None and preferred:
            result = {'option': preferred, 'engine': 'verified_equivalence', 'confidence': 1.0}
        self.cache[key] = result
        return result
