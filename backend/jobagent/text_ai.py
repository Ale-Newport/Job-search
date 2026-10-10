"""Public text drafting service with a dedicated application-answer LLM."""
from __future__ import annotations

import asyncio
import re
from uuid import uuid4

from .application_answer_llm import ApplicationAnswerLLM, AnswerValidationError


class TextService:
    def __init__(self, db, secret_store):
        self.db, self.secrets = db, secret_store
        self.answer_llm = ApplicationAnswerLLM(db, secret_store)
        self.lock = asyncio.Lock()

    def uses_grounded_cover_letter(self, question):
        return bool(re.search(r'cover[ -]?letter|carta(?: de)? (?:presentaci[oó]n|motivaci[oó]n)', question, re.I)
                    and self.db.one('SELECT id FROM knowledge_entities LIMIT 1'))

    async def draft(self, settings, question, job, fact_ids):
        async with self.lock:
            return await self._draft(settings, question, job, fact_ids)

    async def infer_field(self, settings, question, job, fact_ids, field):
        # Retrieval sees the verified knowledge base and ranks it for this exact
        # question, instead of inheriting a generic short list of project IDs.
        return await self.answer_llm.generate(settings, question, job, field=field)

    async def test(self, settings):
        async with self.lock:
            return await self.answer_llm.test(settings)

    async def _draft(self, settings, question, job, fact_ids, *, _probe_facts=None, _field=None):
        if _probe_facts is not None:
            # Retained solely for legacy synthetic test callers. No extracted
            # candidate facts or narrative fallback enter the connection test.
            return await self.answer_llm.test(settings)
        facts = []
        for fact_id in fact_ids:
            fact = self.db.one("SELECT * FROM facts WHERE id=? AND (verification_status='verified' OR locked=1)", (fact_id,))
            if not fact:
                raise AnswerValidationError(['Only existing verified or locked facts may be sent to a text model'])
            facts.append(fact)
        if self.uses_grounded_cover_letter(question):
            if not facts:
                raise AnswerValidationError(['Select verified facts first'])
            from .intelligence.narratives import compose_cover_letter
            from .intelligence.store import Knowledge
            composed = compose_cover_letter(Knowledge(self.db), job, flat_facts=facts, allowed_ids=fact_ids or None)
            if composed['answer'] is None:
                raise AnswerValidationError(['Select verified professional experience to support this cover letter'])
            return {'id': str(uuid4()), 'answer': composed['answer'], 'facts_used': [fact['id'] for fact in composed['facts']],
                    'rules_used': composed['rules'], 'role_context': composed['context'], 'confidence': .85,
                    'requires_review': True, 'provider': 'candidate_intelligence', 'cost': 0, 'latency_ms': 0}
        if re.search(r'cover[ -]?letter|carta(?: de)? (?:presentaci[oó]n|motivaci[oó]n)', question, re.I):
            from .intelligence.narratives import compose_cover_letter
            if not facts:
                raise AnswerValidationError(['Select verified facts first'])
            composed = compose_cover_letter(None, job, flat_facts=facts, allowed_ids=fact_ids)
            if not composed['answer']:
                raise AnswerValidationError(['Select verified professional facts to support this cover letter'])
            return {'id': str(uuid4()), 'answer': composed['answer'], 'facts_used': [f['id'] for f in composed['facts']],
                    'confidence': .85, 'requires_review': True, 'provider': 'candidate_intelligence', 'cost': 0, 'latency_ms': 0}
        return await self.answer_llm.generate(settings, question, job, fact_ids or None, field=_field)
