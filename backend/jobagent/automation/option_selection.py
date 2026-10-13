"""Choose real form options from supported facts; local inference only."""

from __future__ import annotations

import hashlib
import json

from ..intelligence.formatting import choice
from .answers import normalize
from .engines import SystemOneEngine
from .types import HumanRequired, InvalidDecision


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
        kwargs = {'question': resolution['question'], 'disclosure_concept': resolution.get('disclosure_concept')}
        # Validate meaning against the visible label, not a saved DOM value.
        supported = [option for option in options if not option.get('disabled') and
                     choice(str(answer), [option], **kwargs)]
        if not supported:
            return None
        preferred = choice(str(answer), supported, **kwargs)
        exact = [o for o in supported if normalize(o['label']) == normalize(str(answer))]
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
