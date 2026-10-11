"""Question-specific professional evidence retrieval and independent answer checks."""
from __future__ import annotations

import hashlib
import json
import re
from decimal import Decimal
from difflib import SequenceMatcher

from .store import Knowledge
from .professional_context import PRIVATE_CONCEPTS, contains_private_candidate_data

PRIVATE = re.compile(r"password|cookie|session|passport|national.?insurance|social.?security|bank|government.?id|api.?key", re.I)
TOKENS = re.compile(r"[a-z][a-z0-9+#]*(?:[.-][a-z0-9+#]+)*")
THEMES = {
    "LEADERSHIP": r"lead|team|mentor|teach|manage|collaborat",
    "CONFLICT": r"team|collaborat|conflict|feedback|teach|lead",
    "TECHNICAL_CHALLENGE": r"challenge|problem|trade.?off|algorithm|concurren|performance|vision|stockfish|architect|debug",
    "HYPOTHETICAL": r"interest|preference|goal|software|engineering|algorithm|artificial|machine|data|backend|cloud",
    "CAREER_INTEREST": r"interest|preference|goal|software|engineering|algorithm|artificial|machine|data|backend|cloud",
    "CAREER_GOALS": r"interest|preference|goal|software|engineering|algorithm|artificial|machine|data|backend|cloud",
    "ROLE_PREFERENCE": r"interest|preference|software|engineering|algorithm|architect|backend|product",
    "PRODUCT_PREFERENCE": r"interest|preference|data|cloud|infrastructure|backend|artificial|machine",
    "COMPANY_MOTIVATION": r"interest|preference|software|engineering|data|artificial|machine|product",
    "NOT_ON_RESUME": r"achievement|project|teach|lead|independent|initiative|contribution",
    "AI_USAGE": r"artificial|machine|vision|llm|ai|coding assist|intelligence",
    "LLM_USAGE": r"llm|ai|coding assist|model|intelligence",
}
THEMES['CHALLENGE'] = THEMES['TECHNICAL_CHALLENGE']


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def requirements(question, field=None):
    field = field or {}
    text = ' '.join(str(x or '') for x in (question, field.get('description'), field.get('placeholder')))
    result = {'language': 'English', 'max_characters': field.get('max_length') or field.get('maxlength')}
    match = re.search(r'(?:approx(?:imately)?\.?|around|about|roughly)\s*(\d+)\s*words?', text, re.I)
    if match:
        count = int(match[1])
        result.update(min_words=round(count * .9), max_words=round(count * 1.1), target_words=count)
    match = re.search(r'(?:under|fewer than|less than)\s*(\d+)\s*words?', text, re.I)
    if match:
        result['max_words'] = int(match[1]) - 1
    match = re.search(r'(?:up to|maximum|max\.?|no more than|at most)\s*(\d+)\s*words?', text, re.I)
    if match:
        result['max_words'] = int(match[1])
    match = re.search(r'(\d+)\s*[- ]?\s*words?\s*(?:limit|maximum|max\b)', text, re.I)
    if match:
        result['max_words'] = int(match[1])
    match = re.search(r'\bexactly\s*(\d+)\s*words?\b', text, re.I)
    if match:
        result.update(min_words=int(match[1]), max_words=int(match[1]), target_words=int(match[1]))
    match = re.search(r'(\d+)\s*[-–]\s*(\d+)\s*words?', text, re.I)
    if match:
        result.update(min_words=int(match[1]), max_words=int(match[2]))
    match = re.search(r'(?:maximum|max\.?|under|up to|at most|no more than)\s*(\d+)\s*characters?', text, re.I)
    if match:
        maximum = int(match[1]) - (1 if match[0].lower().startswith('under') else 0)
        result['max_characters'] = min(maximum, result['max_characters']) if result['max_characters'] else maximum
    if re.search(r'\b(?:one|single|1) sentence\b', text, re.I):
        result['sentences'] = 1
    match = re.search(r'\b(one|two|three|four|five|\d+)\s+examples?\b', text, re.I)
    if match:
        result['examples'] = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5}.get(match[1].lower()) or int(match[1])
    if re.search(r'bullet(?:ed)?(?: points)?', text, re.I):
        result['bullets'] = True
    if field.get('options'):
        result['options'] = field['options']
        if not field.get('multiple') and field.get('type') not in {'multiselect', 'checkbox_group'}:
            result.update(min_selections=1, max_selections=1)
        match = re.search(r'(?:pick|select|choose)\s*(\d+)\s*[-–]\s*(\d+)', text, re.I)
        if match:
            result.update(min_selections=int(match[1]), max_selections=int(match[2]))
    return result


class CandidateFactRetriever:
    def __init__(self, db, knowledge=None):
        self.db = db
        self.knowledge = knowledge or Knowledge(db)

    def retrieve(self, question, intent, allowed_ids=None, limit=32, resume=None, complete_profile=False):
        """Rank eligible current facts, optionally returning the entire safe pool."""
        allowed = set(allowed_ids) if allowed_ids is not None else None
        candidates = []
        authoritative = {f['id'] for kind in {e['kind'] for e in self.knowledge.entities.values()}
                         for record in self.knowledge.records(kind) for f in record['fields'].values()}
        for f in self.knowledge.rows:
            if f['id'] not in authoritative or f.get('sensitivity') not in ('professional', 'public', None):
                continue
            entity = self.knowledge.entities[f['entity_id']]
            if entity['kind'] not in {'person', 'project', 'experience', 'education', 'skill', 'language'}:
                continue
            if entity['kind'] == 'person' and f['concept'] in PRIVATE_CONCEPTS:
                continue
            candidates.append({'id': f['id'], 'key': f['concept'], 'value': f['value'], 'entity_id': entity['id'],
                               'entity': 'Candidate' if entity['kind'] == 'person' else entity['name'], 'category': entity['kind'], 'source': f['source'],
                               'revision': f['revision']})
        for f in self.db.query("SELECT * FROM facts WHERE category!='knowledge' AND (verification_status='verified' OR locked=1)"):
            if f['verification_status'] != 'verified':
                continue
            if f['category'] in {'contact', 'identity', 'personal', 'demographic', 'sensitive', 'legal', 'work_authorization', 'salary'}:
                continue
            if f['category'] == 'language' and self.knowledge.records('language'):
                # A current structured language profile supersedes the old CV
                # language list, including languages removed by user correction.
                continue
            if f['category'] == 'application_answer' or (f['category'] == 'application_profile' and f['key'] not in {
                'preferred_roles', 'professional_interests', 'career_preferences', 'career_goals', 'engineering_preferences',
                'general_context',
            }):
                # Prior ATS choices and consent are not autobiographical evidence.
                continue
            if f['category'] in {'project', 'experience', 'education'} and any(
                entity['kind'] == f['category'] and entity['name'].casefold() in (f['key'] + ' ' + f['value']).casefold()
                for entity in self.knowledge.entities.values()
            ):
                # Structured records already resolve current verified values and
                # preserve their original source; do not re-send stale CV copies.
                continue
            candidates.append({k: f[k] for k in ('id', 'key', 'value', 'category', 'source', 'updated_at')})
        question_tokens = set(TOKENS.findall(question.casefold())) - {'the', 'what', 'you', 'your', 'have', 'with', 'this', 'that', 'words'}
        theme = THEMES.get(intent, r'project|experience|skill|interest|education')
        ranked = []
        for fact in candidates:
            if allowed is not None and fact['id'] not in allowed:
                continue
            if PRIVATE.search(fact['key'] + ' ' + fact['category']):
                continue
            if fact['key'] in PRIVATE_CONCEPTS or contains_private_candidate_data(
                {key: fact.get(key) for key in ('value', 'key', 'entity', 'source')}
            ):
                continue
            # Rank professional content, not opaque IDs or metadata names such
            # as "revision", which accidentally matches the "vision" theme.
            text = json.dumps({key: fact.get(key) for key in ('key', 'value', 'entity', 'category')},
                              ensure_ascii=False).casefold()
            overlap = len(question_tokens & set(TOKENS.findall(text)))
            score = overlap * 3 + len(re.findall(theme, text, re.I))
            if fact['key'] in {'story', 'challenge', 'approach', 'outcome', 'achievement'}:
                score += 6
            if fact['key'] in {'public_architecture_tradeoffs', 'public_implementation_details', 'contribution'}:
                score += 24 if intent in {'CHALLENGE', 'TECHNICAL_CHALLENGE', 'NOT_ON_RESUME', 'PROJECT', 'FROM_SCRATCH'} else 8
            if fact['key'] in {'professional_interests', 'career_preferences', 'career_goals', 'engineering_preferences'}:
                score += 18 if intent in {'HYPOTHETICAL', 'CAREER_INTEREST', 'CAREER_GOALS', 'ROLE_PREFERENCE', 'PRODUCT_PREFERENCE', 'COMPANY_MOTIVATION'} else 2
            if intent in {'LEADERSHIP', 'CONFLICT'} and fact['category'] == 'experience':
                score += 8
            if intent == 'NOT_ON_RESUME' and resume and fact.get('entity'):
                score += -50 if fact['entity'].casefold() in resume['text'].casefold() else 16
            if complete_profile or score > 0 or fact['category'] in {'experience', 'project'}:
                ranked.append((score, fact))
        ranked.sort(key=lambda item: (-item[0], item[1]['id']))
        if complete_profile:
            complete = [fact for _, fact in ranked]
            if len(json.dumps(complete, ensure_ascii=False)) > 250000:
                raise ValueError('Complete professional profile context exceeds 250000 characters; select explicit allowed_ids to narrow the evidence. No facts were silently truncated.')
            return complete
        # Keep an example's implementation and contribution together. Selecting
        # one fact from dozens of skill entities loses the evidence needed for a
        # concrete story, even when those details are available in the profile.
        grouped = {}
        for score, fact in ranked:
            grouped.setdefault(fact.get('entity_id', fact['id']), []).append(fact)
        selected = []
        while grouped and len(selected) < limit:
            for key in list(grouped):
                for _ in range(min(3, len(grouped[key]), limit - len(selected))):
                    selected.append(grouped[key].pop(0))
                if not grouped[key]:
                    del grouped[key]
                if len(selected) >= limit:
                    break
        bounded, size = [], 0
        for fact in selected:
            length = len(json.dumps(fact, ensure_ascii=False))
            if size + length <= 10500:
                bounded.append(fact)
                size += length
        return bounded

    def uploaded_resume(self, job):
        application_id = job.get('application_id') or job.get('id')
        rows = self.db.query(
            "SELECT d.id,d.text,d.fact_ids,d.content_hash FROM document_versions d JOIN application_documents a "
            "ON a.document_version_id=d.id WHERE a.application_id=? AND a.kind IN ('cv','resume','base_cv','swe_cv','ai_cv','data_cv')",
            (application_id,)) if application_id else []
        explicit = job.get('resume_version_id') or job.get('uploaded_resume_version_id')
        if explicit:
            rows = self.db.query('SELECT id,text,fact_ids,content_hash FROM document_versions WHERE id=?', (explicit,))
        if len(rows) != 1 or not rows[0]['text']:
            return None
        row = rows[0]
        return {'id': row['id'], 'text': row['text'], 'fact_ids': json.loads(row['fact_ids']), 'content_hash': row['content_hash']}


def similarity(left, right):
    def shingles(text):
        words = re.findall(r'\w+', text.casefold())
        return {' '.join(words[i:i + 4]) for i in range(max(0, len(words) - 3))}
    a, b = shingles(left), shingles(right)
    lexical = len(a & b) / min(len(a), len(b)) if a and b else 0
    return max(lexical, SequenceMatcher(None, left.casefold(), right.casefold()).ratio())


EVENT_PATTERNS = {
    'conflict': r'conflict|disagree|dispute|tension|clash|argument',
    'bug': r'\bbug\b|root cause|misconfigur|regression|crash|deadlock|race condition|faulty|debugging incident',
    'failure': r'\bfailed\b|\bfailure\b|mistake|unsuccessful|missed deadline',
}


def missing_historical_event(question, intent, facts):
    """General project/leadership facts cannot establish an undocumented incident."""
    event = ('conflict' if intent == 'CONFLICT' else 'bug' if re.search(r'\bbug\b|outage|incident|root cause', question, re.I)
             else 'failure' if intent == 'FAILURE' else None)
    if not event:
        return None
    pattern = EVENT_PATTERNS[event]
    supported = [f for f in facts if f.get('category') in {'project', 'experience'}
                 and re.search(pattern, str(f['value']), re.I) and len(str(f['value']).split()) >= 20]
    return None if supported else f'MISSING_CANDIDATE_FACT: a verified specific {event} incident, personal actions and outcome'


# Claim entailment is also checked deterministically. A small model can accept
# plausible invented causal detail in its own review, so novel incident terms
# and unsupported responsibility must independently fail closed.
SUBSTANTIVE = {
    'bug': r'\bbug\b', 'misconfiguration': r'misconfigur', 'indexing': r'index(?:ing|es)?',
    'conflict': r'conflict', 'disagreement': r'disagree', 'meeting': r'meeting',
    'outage': r'outage', 'standstill': r'standstill', 'root_cause': r'root cause|stemmed from',
    'entirely_alone': r'entirely my own|sole developer|single[- ]handed|on my own|alone',
    'architecture_ownership': r'I designed the architecture|full ownership|from concept to implementation',
    'client_satisfaction': r'client was (?:highly )?satisfied|client.s satisfaction|customer.s satisfaction',
}
SUBSTANTIVE_SOURCE_ALIASES = {
    'entirely_alone': r'\bsole author\b|\bsole contributor\b',
    'architecture_ownership': r'\bwhole system\b|\bfull pipeline\b|\bsole author\b',
}

# Claims supplied by the generator are an index, not a complete description of
# its prose. Check consequential historical assertions in the answer itself.
INCIDENT_DETAILS = {
    'race_condition': r'\brace conditions?\b|\bdata races?\b',
    'deadlock': r'\bdeadlocks?\b',
    'interleaving_failure': r'\b(?:specific|certain|particular) interleavings?\b',
    'memory_leak': r'\bmemory leaks?\b',
    'crash': r'\bcrash(?:ed|es|ing)?\b',
    'outage': r'\boutages?\b',
    'bug': r'\bbugs?\b|\bregressions?\b|misconfigur',
}
DIAGNOSTIC_ACTION = r'debug(?:ged|ging)?|diagnos(?:ed|ing)|trac(?:ed|ing)|investigat(?:ed|ing)|reproduc(?:ed|ing)'
REPAIR_ACTION = r'fix(?:ed|ing)|resolv(?:ed|ing)|repair(?:ed|ing)|correct(?:ed|ing)'
COMPLETED_ACTION = re.compile(r'\b(?:debugged|diagnosed|traced|investigated|reproduced|fixed|resolved|repaired|corrected)\b', re.I)
PAST_IMPLEMENTATION = re.compile(r'\b(?:built|developed|implemented|processed|ensured|guaranteed|prevented|achieved|launched|delivered)\b', re.I)
INCIDENT_OCCURRENCE = re.compile(r'\b(?:appeared|occurred|arose|happened|encountered|discovered|found|caused|failed|broke)\b', re.I)
PAST_EVENT_ACTION = re.compile(
    rf'\b(?:I|we)\s+(?:(?:had|needed) to|(?:was|were)|(?:ended up))\s+(?:\w+\s+){{0,3}}(?:debug|diagnose|trace|investigate|reproduce|fix|resolve|repair|correct)(?:\w*)\b|'
    rf'\b(?:part|challenge|difficulty|task)\s+(?:was|involved)\s+(?:{DIAGNOSTIC_ACTION}|{REPAIR_ACTION})\b', re.I)
PROSPECTIVE = re.compile(r'\b(?:I|we)\s+(?:would|will|could|might|plan to|hope to|want to)\b|\b(?:would|could|might)\s+(?:be|need|require|help|allow)\b', re.I)
IMPLEMENTATION_GUARANTEES = {
    'atomicity': r'\batomic(?:ally|ity)?\b|\ball[- ]or[- ]nothing\b',
    'linearizability': r'\blinearizab(?:le|ility)\b',
    'serializability': r'\bserializab(?:le|ility)\b',
    'race_free': r'\brace[- ]free\b|\bwithout (?:any )?(?:data )?races?\b',
    'corruption_prevention': r'\bwithout corrupting\b|\bprevent(?:ed|s|ing)? (?:data |shared[- ]state )?corruption\b',
    'thread_safety': r'\b(?:ensur(?:e|ed|es|ing)|guarantee(?:d|s)?|made|kept)\b.{0,45}\bthread[- ]safe(?:ty)?\b|\b(?:is|was|were|are) thread[- ]safe\b',
}
GUARANTEE_SOURCE_ALIASES = {
    'atomicity': r'\b(?:same lock|single lock|critical section)\b.{0,100}\b(?:orders?|matching)\b.{0,100}\bprice (?:updates?|ticks?)\b|'
                 r'\b(?:orders?|matching)\b.{0,100}\bprice (?:updates?|ticks?)\b.{0,100}\b(?:same lock|single lock|critical section)\b',
}


def prospective_statement(sentence):
    # A conditional does not erase an actual past incident: "I debugged a race
    # that occurred if two threads..." remains a historical assertion.
    return bool(PROSPECTIVE.search(sentence)) and not (COMPLETED_ACTION.search(sentence) or
        PAST_EVENT_ACTION.search(sentence) or PAST_IMPLEMENTATION.search(sentence))


def prose_claim_issues(answer, known, used):
    """Check omitted incident, guarantee and causal claims against local evidence."""
    issues = []
    grouped = {}
    names = {}
    for identifier in used:
        fact = known.get(identifier, {})
        if fact.get('category') not in {'project', 'experience', 'education'}:
            continue
        entity = fact.get('entity_id') or fact.get('entity')
        if entity:
            grouped.setdefault(entity, []).append(fact)
            names[entity] = re.sub(r'\W+', '', str(fact.get('entity', '')).casefold())
    active = set(grouped) if len(grouped) == 1 else set()
    for sentence in re.split(r'(?<!\d)[.!?](?:\s+|$)|\n+', answer):
        if not sentence.strip():
            continue
        normalized = re.sub(r'\W+', '', sentence.casefold())
        mentioned = {entity for entity, name in names.items() if name and name in normalized}
        if mentioned:
            active = mentioned
        local_facts = [fact for entity in active for fact in grouped[entity]]
        source = '\n'.join(str(fact['value']) for fact in (local_facts if grouped else
                           [known[identifier] for identifier in used if identifier in known]))
        if prospective_statement(sentence):
            continue
        # Inspect factual methods in the prose even if the model omitted them
        # from its claims index. Subjective future preferences are excluded.
        historical_method = re.search(
            r"\b(?:I|we)\s+(?:(?:had|needed) to\s+)?(?:built|developed|implemented|used|trained|"
            r"iterated|adjusted|augmented|optimized|tested|validated|integrated|designed|created|deployed)\b|"
            r"\b(?:This(?: (?:project|system|pipeline|challenge))?|It|The (?:project|system|pipeline|challenge))\s+(?:required|taught|reinforced)\b",
            sentence, re.I)
        method_words = content_words(sentence)
        missing_method_words = method_words - content_words(source)
        if historical_method and len(method_words) >= 4 and len(missing_method_words) / len(method_words) > .5:
            issues.append('UNSUPPORTED_LLM_CLAIM: actual answer adds undocumented historical method detail: ' +
                          ', '.join(sorted(missing_method_words)[:6]))
        details = {name: pattern for name, pattern in INCIDENT_DETAILS.items() if re.search(pattern, sentence, re.I)}
        completed = bool(COMPLETED_ACTION.search(sentence) or PAST_EVENT_ACTION.search(sentence))
        occurred = bool(details and INCIDENT_OCCURRENCE.search(sentence))
        if completed or occurred:
            # Components, ownership and generic discussion of concurrency do
            # not establish an actual debugging episode or repair action.
            incident_facts = [str(fact['value']) for fact in local_facts if not prospective_statement(str(fact['value'])) and (
                COMPLETED_ACTION.search(str(fact['value'])) or PAST_EVENT_ACTION.search(str(fact['value'])) or
                (any(re.search(pattern, str(fact['value']), re.I) for pattern in INCIDENT_DETAILS.values()) and
                 (INCIDENT_OCCURRENCE.search(str(fact['value'])) or re.search(rf'\b(?:{DIAGNOSTIC_ACTION}|{REPAIR_ACTION})\b', str(fact['value']), re.I))))]
            incident_source = '\n'.join(incident_facts)
            if not incident_source:
                issues.append('UNSUPPORTED_LLM_CLAIM: actual answer invents an undocumented debugging incident or personal repair action')
            else:
                for name, pattern in details.items():
                    if not re.search(pattern, incident_source, re.I):
                        issues.append('UNSUPPORTED_LLM_CLAIM: actual answer adds undocumented incident detail ' + name)
                for action, pattern in [('diagnostic', r'debug(?:ged|ging)?|diagnos(?:ed|ing)|investigat(?:ed|ing)'),
                                        ('tracing', r'trac(?:ed|ing)'), ('reproduction', r'reproduc(?:ed|ing)'),
                                        ('repair', REPAIR_ACTION)]:
                    if re.search(rf'\b(?:{pattern})\b', sentence, re.I) and not re.search(rf'\b(?:{pattern})\b', incident_source, re.I):
                        issues.append('UNSUPPORTED_LLM_CLAIM: actual answer adds an undocumented personal ' + action + ' action')
        for name, pattern in IMPLEMENTATION_GUARANTEES.items():
            source_pattern = pattern + '|' + GUARANTEE_SOURCE_ALIASES[name] if name in GUARANTEE_SOURCE_ALIASES else pattern
            if re.search(pattern, sentence, re.I) and not re.search(source_pattern, source, re.I):
                issues.append('UNSUPPORTED_LLM_CLAIM: actual answer asserts an undocumented implementation guarantee ' + name)
        distributive = re.search(r'\beach\b.{0,50}\b(?:thread|trader|worker|process|client|user)\b.{0,65}\b(?:its|their) own\b', sentence, re.I)
        if distributive and not re.search(r'\beach\b.{0,50}\b(?:thread|trader|worker|process|client|user)\b.{0,65}\b(?:its|their) own\b|\bper[- ](?:thread|trader|worker|process|client|user)\b|\bone\b.{0,35}\bper (?:thread|trader|worker|process|client|user)\b', source, re.I):
            issues.append('UNSUPPORTED_LLM_CLAIM: actual answer adds undocumented per-actor resource ownership')
        method = re.search(r'\b[Bb]y\s+([^,;.!?]+)[,;]\s*', sentence)
        if method and re.search(r'\b(?:launched|delivered|achieved|increased|reduced|improved|completed|finished|saved|enabled)\b', sentence[method.end():], re.I):
            method_words = {'set' if word == 'sett' else word for word in content_words(method[1])} - {'clear', 'careful', 'carefully'}
            source_words = {'set' if word == 'sett' else word for word in content_words(source)}
            leadership = {'lead', 'coordinat', 'manag'}
            if re.search(r'\b(?:lead|led|leading|coordinat\w*|manag\w*)\b', method[1], re.I) and re.search(r'\b(?:lead|led|leading|coordinat\w*|manag\w*)\b', source, re.I):
                method_words -= leadership
            if method_words - source_words:
                issues.append('UNSUPPORTED_LLM_CLAIM: actual answer attributes an outcome to an undocumented causal method')
    return issues


def content_words(text):
    stop = set('i a an the my me we our to of in on at for as and or with that this it is was were had have has '
               'by from then also which while through during using used use built build developed develop '
               'project work worked working experience system team software engineering engineer engineers '
               'its these those into both more each all their they them not only led lead would can could '
               'be been am are such some very significantly particular particularly result results '
               'complex challenging challenge significant numerous various real-world'.split())
    def stem(word):
        if any(character in word for character in '.+#'):
            return word
        if len(word) > 5 and word.endswith('ies'):
            word = word[:-3] + 'y'
        elif len(word) > 6 and word.endswith('ing'):
            word = word[:-3]
        elif len(word) > 5 and word.endswith('ed'):
            word = word[:-2]
        elif len(word) > 5 and re.search(r'(?:ss|x|z|ch|sh)es$', word):
            word = word[:-2]
        elif len(word) > 3 and word.endswith('s') and not word.endswith('ss'):
            word = word[:-1]
        return word[:-1] if len(word) > 5 and word.endswith('e') else word
    # Normalize both source and claim, including stop words, before comparison.
    # Terminal periods are punctuation; internal periods in Vue.js remain useful.
    normalized_stop = {stem(word) for word in stop}
    words = {stem(word) for word in TOKENS.findall(str(text).casefold()) if len(word) >= 3} - normalized_stop
    if re.search(r'deep[- ]learning|neural|\bcnn\b|machine[- ]learning', str(text), re.I):
        words.update({stem('machine'), stem('learning')})
    return words


def numeric_claims(text):
    """Compare equivalent written metrics while preserving percentage units."""
    text = re.sub(r'(?m)^\s*\d+[.)]\s+', '', str(text))
    values = set()
    for raw in re.findall(r'\b\d[\d,]*(?:\.\d+)?(?:\s*(?:%|[km]\b|million\b|thousand\b))?', text, re.I):
        match = re.fullmatch(r'([\d,.]+)\s*(%|k|m|million|thousand)?', raw, re.I)
        unit = (match[2] or '').casefold()
        number = Decimal(match[1].replace(',', ''))
        multiplier = {'k': 1000, 'thousand': 1000, 'm': 1000000, 'million': 1000000}.get(unit, 1)
        canonical = format((number * multiplier).normalize(), 'f')
        values.add((canonical, '%' if unit == '%' else ''))
    return values


def factual_numeric_claims(text, cited_facts):
    """An exact cited entity label is identity, not an outcome metric.

    Versions outside that exact label, or additional numbers elsewhere in the
    prose, still require value-level evidence from the cited facts.
    """
    text = str(text)
    for fact in cited_facts:
        name = fact.get('entity')
        if isinstance(name, str) and numeric_claims(name):
            pattern = r'(?<![A-Za-z0-9])' + re.escape(name) + r'(?![A-Za-z0-9]|\.[0-9]|[-+][A-Za-z0-9])'
            text = re.sub(pattern, ' ', text, flags=re.I)
    return numeric_claims(text)


def reconcile_claim_evidence(generated, facts):
    """Restore omitted citations only from the same supplied verified entity.

    A model may cite a project's summary but describe its implementation fact.
    This changes provenance metadata only: it never changes prose, introduces
    other entities, or makes a claim valid unless that detail exists in evidence.
    """
    known = {fact['id']: fact for fact in facts}
    restored = []
    for claim in generated.get('claims', []):
        identifiers = claim.get('fact_ids', [])
        if not identifiers or any(identifier not in known for identifier in identifiers):
            continue
        entity_keys = {known[identifier].get('entity_id') or known[identifier].get('entity') for identifier in identifiers}
        if None in entity_keys or len(entity_keys) != 1:
            continue
        claim_terms = content_words(claim.get('text', ''))
        claim_numbers = factual_numeric_claims(claim.get('text', ''), [known[identifier] for identifier in identifiers])
        for _ in range(3):
            cited = '\n'.join(str(known[identifier]['value']) for identifier in identifiers)
            missing_terms = claim_terms - content_words(cited)
            missing_numbers = claim_numbers - factual_numeric_claims(cited, [known[identifier] for identifier in identifiers])
            candidates = []
            for fact in facts:
                if fact['id'] in identifiers or (fact.get('entity_id') or fact.get('entity')) not in entity_keys:
                    continue
                score = len(missing_terms & content_words(fact['value'])) + 3 * len(missing_numbers & numeric_claims(fact['value']))
                if score:
                    candidates.append((score, fact['id']))
            if not candidates:
                break
            identifier = max(candidates)[1]
            identifiers.append(identifier)
            restored.append(identifier)
        claim['fact_ids'] = identifiers
    generated['facts_used'] = list(dict.fromkeys([*generated.get('facts_used', []),
        *(identifier for claim in generated.get('claims', []) for identifier in claim.get('fact_ids', []))]))
    return list(dict.fromkeys(restored))


def entailment_issues(answer, claims, known, used):
    issues = prose_claim_issues(answer, known, used)
    source = '\n'.join(str(known[x]['value']) for x in used if x in known)
    historical_sentences = '\n'.join(sentence for sentence in re.split(r'(?<!\d)[.!?](?:\s+|$)', answer)
                                     if not prospective_statement(sentence))
    for name, pattern in SUBSTANTIVE.items():
        source_pattern = pattern + '|' + SUBSTANTIVE_SOURCE_ALIASES[name] if name in SUBSTANTIVE_SOURCE_ALIASES else pattern
        if re.search(pattern, historical_sentences, re.I) and not re.search(source_pattern, source, re.I):
            issues.append('UNSUPPORTED_LLM_CLAIM: unsupported historical detail ' + name)
    for claim in claims if isinstance(claims, list) else []:
        if not isinstance(claim, dict):
            continue
        cited = '\n'.join(str(known[x]['value']) for x in claim.get('fact_ids', []) if x in known)
        words = content_words(claim.get('text', ''))
        missing = words - content_words(cited)
        # Allow normal paraphrases, while refusing a claim whose substantive
        # detail is mostly absent from its cited source.
        subjective = bool(re.search(r'\b(?:I (?:am|would be) (?:interested|drawn|excited)|I (?:want|hope|prefer)|'
                                   r'(?:excited|interested|passionate|drawn)\s+(?:about|in|to)|'
                                   r'(?:my|this) (?:background|interest|experience).{0,35}\balign|'
                                   r'\bopportunity to|\bchance to)\b', claim.get('text', ''), re.I))
        historical = bool(re.search(r'\b(?:built|developed|deployed|implemented|managed|led|resolved|increased|reduced|achieved)\b', claim.get('text', ''), re.I))
        if len(words) >= 4 and len(missing) / len(words) > .5 and not (subjective and not historical):
            issues.append('UNSUPPORTED_LLM_CLAIM: claim contains detail absent from cited evidence: ' + ', '.join(sorted(missing)[:6]))
        reflected = words & content_words(answer)
        if len(words) >= 4 and len(reflected) / len(words) < .35:
            issues.append('UNSUPPORTED_LLM_CLAIM: evidence claim is not reflected in the actual answer')
        named_details = re.findall(r'\b[A-Z][A-Za-z0-9+#.-]*\b', claim.get('text', ''))[1:]
        if any(word.casefold() not in answer.casefold() for word in named_details):
            issues.append('UNSUPPORTED_LLM_CLAIM: a named detail in evidence claims is not present in the actual answer')
    return issues


class AnswerEvidenceValidator:
    def validate(self, generated, facts, question_id, intent, output_requirements, previous_answers=(), resume=None):
        issues = []
        answer = generated.get('answer')
        if generated.get('question_id') != question_id:
            issues.append('CONCURRENT_ANSWER_MIXUP: response question ID does not match request')
        if generated.get('intent') != intent:
            issues.append('WRONG_QUESTION_INTENT: response intent does not match request')
        used = generated.get('facts_used')
        known = {f['id']: f for f in facts}
        if not isinstance(used, list) or not used or any(not isinstance(x, str) or x not in known for x in used):
            issues.append('UNSUPPORTED_LLM_CLAIM: invalid evidence references')
            used = []
        if not isinstance(answer, str) or not answer.strip():
            return issues + ['IRRELEVANT_LLM_ANSWER: empty answer was rejected']
        if len(answer) > 20000:
            issues.append('BAD_FORMAT_INFERENCE: answer exceeds safety limit')
        words = len(answer.split())
        for key, predicate in [('min_words', lambda v: words < v), ('max_words', lambda v: words > v),
                               ('max_characters', lambda v: len(answer) > v)]:
            if output_requirements.get(key) and predicate(output_requirements[key]):
                issues.append(f'INSTRUCTION_COMPLIANCE: {key} failed ({words} words; {len(answer)} characters)')
        if output_requirements.get('sentences') == 1 and len(re.findall(r'[.!?](?:\s|$)', answer)) != 1:
            issues.append('INSTRUCTION_COMPLIANCE: one sentence required')
        if output_requirements.get('bullets') and not re.search(r'(?m)^\s*[-*•]\s', answer):
            issues.append('INSTRUCTION_COMPLIANCE: bullet points required')
        if output_requirements.get('bullets') and output_requirements.get('examples') and len(re.findall(r'(?m)^\s*[-*•]\s', answer)) != output_requirements['examples']:
            issues.append('INSTRUCTION_COMPLIANCE: incorrect number of examples')
        if output_requirements.get('options'):
            labels = {x.get('label') if isinstance(x, dict) else x for x in output_requirements['options']}
            selections = generated.get('selected_options')
            if not isinstance(selections, list) or not selections or any(x not in labels for x in selections):
                issues.append('BAD_DROPDOWN_MAPPING: select exact offered labels')
            elif len(selections) != len(set(selections)) or len(selections) < output_requirements.get('min_selections', 1) or len(selections) > output_requirements.get('max_selections', len(labels)):
                issues.append('INSTRUCTION_COMPLIANCE: wrong number of selected options')
        source = '\n'.join(str(known[x]['value']) for x in used).casefold()
        cited_facts = [known[x] for x in used]
        for number, unit in factual_numeric_claims(answer, cited_facts) - factual_numeric_claims(source, cited_facts):
            issues.append(f'UNSUPPORTED_LLM_CLAIM: number {number}{unit} is absent from cited evidence')
        used_entities = {known[x].get('entity') for x in used if x in known and known[x].get('entity')
                         and known[x].get('category') in {'project', 'experience'}}
        named_entities = {name for name in used_entities if name.casefold() in answer.casefold()}
        for sentence in re.split(r'(?<!\d)[.!?](?:\s+|$)', answer):
            metric_entities = {name for name in named_entities if name.casefold() in sentence.casefold()}
            if not metric_entities and len(named_entities) == 1:
                metric_entities = named_entities
            if not metric_entities:
                continue
            local_source = '\n'.join(str(known[x]['value']) for x in used if x in known and known[x].get('entity') in metric_entities)
            local_facts = [known[x] for x in used if x in known and known[x].get('entity') in metric_entities]
            if factual_numeric_claims(sentence, local_facts) - factual_numeric_claims(local_source, local_facts):
                issues.append('UNSUPPORTED_LLM_CLAIM: a metric is attributed to an entity absent from its cited evidence')
        current = any((f['key'] == 'current_company' or (f['category'] == 'experience' and f['key'] == 'ongoing'))
                      and f['value'] not in (False, 'false') for f in facts)
        if not current and re.search(r'\bi (?:work|am working|am employed) (?:at|for)\b|\bcurrently\s*,?\s*(?:as\b|employed|working)|\bmy current (?:role|job)', answer, re.I):
            issues.append('UNSUPPORTED_LLM_CLAIM: current employment is not confirmed')
        for prior in previous_answers:
            text = prior.get('answer', '') if isinstance(prior, dict) else str(prior)
            prior_id = prior.get('question_id') if isinstance(prior, dict) else None
            if not text or prior_id == question_id:
                continue
            if similarity(answer, text) >= .72:
                issues.append('REPEATED_LLM_ANSWER: near-identical content belongs to another question')
            opening = re.split(r'[.!?]', answer)[0].casefold().strip()
            if len(opening.split()) >= 7 and opening == re.split(r'[.!?]', text)[0].casefold().strip():
                issues.append('REPEATED_LLM_ANSWER: repeated opening sentence')
        if intent == 'NOT_ON_RESUME':
            if not resume:
                issues.append('MISSING_CANDIDATE_FACT: exact uploaded resume text is unavailable')
            else:
                resume_ids = set(resume['fact_ids'])
                if used and set(used).issubset(resume_ids):
                    issues.append('IRRELEVANT_LLM_ANSWER: all cited evidence is already in the uploaded resume')
                if similarity(answer, resume['text']) >= .65:
                    issues.append('IRRELEVANT_LLM_ANSWER: answer repeats uploaded resume content')
                selected_entities = {known[x].get('entity') for x in used if x in known and known[x].get('entity')
                                     and known[x].get('category') in {'project', 'experience'}}
                resume_words = content_words(resume['text'])
                novel_claim = any(len((claim_words := content_words(claim.get('text', ''))) - resume_words) >= 4
                                  and len(claim_words - resume_words) / max(1, len(claim_words)) >= .5
                                  for claim in generated.get('claims', []) if isinstance(claim, dict))
                if selected_entities and all(name.casefold() in resume['text'].casefold() for name in selected_entities) and not novel_claim:
                    issues.append('IRRELEVANT_LLM_ANSWER: the selected accomplishment is already named in the uploaded resume; choose an absent achievement')
        if generated.get('unsupported_claims'):
            issues.append('UNSUPPORTED_LLM_CLAIM: model identified unsupported claims')
        if generated.get('answers_question') is not True or generated.get('follows_instructions') is not True:
            issues.append('IRRELEVANT_LLM_ANSWER: response reports failing relevance or instructions')
        claims = generated.get('claims', [])
        if not isinstance(claims, list):
            issues.append('UNSUPPORTED_LLM_CLAIM: claim evidence must be a list')
        else:
            for claim in claims:
                if not isinstance(claim, dict) or not claim.get('text') or not claim.get('fact_ids') or not set(claim['fact_ids']).issubset(used):
                    issues.append('UNSUPPORTED_LLM_CLAIM: factual claim has invalid evidence')
                else:
                    claim_source = '\n'.join(str(known[x]['value']) for x in claim['fact_ids'] if x in known)
                    claim_facts = [known[x] for x in claim['fact_ids'] if x in known]
                    if factual_numeric_claims(claim['text'], claim_facts) - factual_numeric_claims(claim_source, claim_facts):
                        issues.append('UNSUPPORTED_LLM_CLAIM: a factual claim contains a metric absent from its own cited evidence')
        issues.extend(entailment_issues(answer, claims, known, used))
        return list(dict.fromkeys(issues))


class ApplicationConsistencyReviewer:
    def review(self, answers, job=None, field=None):
        issues = []
        for index, answer in enumerate(answers):
            for other in answers[:index]:
                if answer.get('question_id') != other.get('question_id') and similarity(answer.get('answer', ''), other.get('answer', '')) >= .72:
                    issues.append({'code': 'REPEATED_LLM_ANSWER', 'question_ids': [other.get('question_id'), answer.get('question_id')]})
                opening = re.split(r'[.!?]', answer.get('answer', ''))[0].casefold().strip()
                if len(opening.split()) >= 7 and opening == re.split(r'[.!?]', other.get('answer', ''))[0].casefold().strip():
                    issues.append({'code': 'REPEATED_LLM_ANSWER', 'question_ids': [other.get('question_id'), answer.get('question_id')]})
        entity_counts = {}
        for answer in answers:
            for entity in set(answer.get('evidence_entities', [])):
                entity_counts[entity] = entity_counts.get(entity, 0) + 1
        if len(answers) >= 3 and any(n == len(answers) for n in entity_counts.values()):
            issues.append({'code': 'EXCESSIVE_EVIDENCE_REUSE', 'message': 'Every narrative uses the same project; review whether each question is answered distinctly.'})
        return {'passed': not any(x['code'] == 'REPEATED_LLM_ANSWER' for x in issues), 'issues': issues}
