"""Question-specific professional evidence retrieval and independent answer checks."""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
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


SPANISH_WORDS = set("""el la los las del de que por para con una uno unos unas es en tu tus su sus nuestra nuestro nuestros nuestras
como porque trabajar quieres tienes has he mi mis me se lo al pero mas tambien sobre entre este esta estos estas cual cuales
donde cuando empresa puesto palabras maximo caracteres cuentanos incluye explica motivacion experiencia proyecto equipo
candidatura oferta solicitud nos eres estas puedes podrias dos tres ejemplo ejemplos liderazgo informacion adicional cuenta hablanos
escribe resume lista frase carta presentacion logros aqui ti sin desde hasta nuestro rol""".split())
ENGLISH_WORDS = set("""the of to and in is that for with as are you your why what how do this we our at from be on by it or an
have would can could will about tell describe words maximum characters please role company team experience project""".split())
PAGE_LANGUAGES = {"es": "Spanish", "ca": "Catalan", "gl": "Galician", "eu": "Basque", "fr": "French", "de": "German",
                  "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "en": "English"}


def detect_language(text, page_lang=''):
    """The language a question is written in: the words of the question first (Spanish function words, an inverted mark),
    then the language of the page. English when nothing says otherwise. A draft is written in this language."""
    plain = unicodedata.normalize('NFKD', str(text or '')[:800]).encode('ascii', 'ignore').decode().lower()
    words = re.findall(r'[a-z]+', plain)
    spanish = sum(word in SPANISH_WORDS for word in words) + (3 if re.search(r'[¿¡ñÑ]', str(text or '')) else 0)
    english = sum(word in ENGLISH_WORDS for word in words)
    if spanish >= 2 and spanish > english:
        return 'Spanish'
    if english >= 2 and english >= spanish:
        return 'English'
    return PAGE_LANGUAGES.get(str(page_lang or '').lower()[:2], 'English')


def requirements(question, field=None):
    field = field or {}
    text = ' '.join(str(x or '') for x in (question, field.get('description'), field.get('placeholder')))
    result = {'language': detect_language(' '.join(str(x or '') for x in (question, field.get('description'))), field.get('_page_lang')),
              'max_characters': field.get('max_length') or field.get('maxlength')}
    match = re.search(r'(?:approx(?:imately)?\.?|around|about|roughly|aproximadamente|aprox\.?|alrededor de|en torno a|cerca de|unas|unos)\s*(\d+)\s*(?:words?|palabras?)', text, re.I)
    if match:
        count = int(match[1])
        result.update(min_words=round(count * .9), max_words=round(count * 1.1), target_words=count)
    match = re.search(r'(?:under|fewer than|less than|menos de|por debajo de)\s*(\d+)\s*(?:words?|palabras?)', text, re.I)
    if match:
        result['max_words'] = int(match[1]) - 1
    match = re.search(r'(?:up to|maximum|max\.?|no more than|at most|m[aá]ximo(?: de)?|como m[aá]ximo|como mucho|hasta|no m[aá]s de|un m[aá]ximo de)\s*(\d+)\s*(?:words?|palabras?)', text, re.I)
    if match:
        result['max_words'] = int(match[1])
    match = re.search(r'(\d+)\s*[- ]?\s*(?:words?|palabras?)\s*(?:limit|maximum|max\b|m[aá]ximo|como m[aá]ximo|de l[ií]mite)', text, re.I)
    if match:
        result['max_words'] = int(match[1])
    match = re.search(r'\b(?:exactly|exactamente)\s*(\d+)\s*(?:words?|palabras?)\b', text, re.I)
    if match:
        result.update(min_words=int(match[1]), max_words=int(match[1]), target_words=int(match[1]))
    match = re.search(r'(\d+)\s*[-–]\s*(\d+)\s*(?:words?|palabras?)|\bentre\s*(\d+)\s*y\s*(\d+)\s*palabras?', text, re.I)
    if match:
        low, high = (match[1], match[2]) if match[1] else (match[3], match[4])
        result.update(min_words=int(low), max_words=int(high))
    match = re.search(r'(?:maximum|max\.?|under|up to|at most|no more than|m[aá]ximo(?: de)?|hasta|como m[aá]ximo|no m[aá]s de|menos de)\s*(\d+)\s*(?:characters?|caracteres)', text, re.I)
    if match:
        maximum = int(match[1]) - (1 if re.match(r'(?:under|menos de)', match[0], re.I) else 0)
        result['max_characters'] = min(maximum, result['max_characters']) if result['max_characters'] else maximum
    if re.search(r'\b(?:one|single|1) sentence\b|\b(?:una|1)\s+(?:sola\s+)?(?:frase|oraci[oó]n)\b', text, re.I):
        result['sentences'] = 1
    match = re.search(r'\b(one|two|three|four|five|\d+)\s+examples?\b|\b(un|uno|una|dos|tres|cuatro|cinco|\d+)\s+ejemplos?\b', text, re.I)
    if match:
        word = (match[1] or match[2]).lower()
        result['examples'] = {'one': 1, 'two': 2, 'three': 3, 'four': 4, 'five': 5, 'un': 1, 'uno': 1, 'una': 1, 'dos': 2,
                              'tres': 3, 'cuatro': 4, 'cinco': 5}.get(word) or int(word)
    if re.search(r'bullet(?:ed)?(?: points)?|vi[ñn]etas?|puntos clave', text, re.I):
        result['bullets'] = True
    if field.get('options'):
        result['options'] = field['options']
        if not field.get('multiple') and field.get('type') not in {'multiselect', 'checkbox_group'}:
            result.update(min_selections=1, max_selections=1)
        match = re.search(r'(?:pick|select|choose|elige|selecciona)\s*(\d+)\s*[-–]\s*(\d+)', text, re.I)
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


def evidence_text(facts):
    return '\n'.join(str(fact.get('entity') or '') + ' ' + str(fact['value']) for fact in facts)


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
        source = evidence_text(local_facts if grouped else [known[identifier] for identifier in used if identifier in known])
        if prospective_statement(sentence):
            continue
        # Inspect factual methods in the prose even if the model omitted them
        # from its claims index. Subjective future preferences are excluded.
        historical_method = re.search(
            r"\b(?:I|we)\s+(?:(?:had|needed) to\s+)?(?:built|build|develop(?:ed)?|implement(?:ed)?|used|use|train(?:ed)?|"
            r"iterat(?:ed|e)|adjust(?:ed)?|augment(?:ed)?|optimiz(?:ed|e)|test(?:ed)?|validat(?:ed|e)|integrat(?:ed|e)|"
            r"design(?:ed)?|creat(?:ed|e)|deploy(?:ed)?|verif(?:ied|y))\b|"
            r"\b(?:This(?: (?:project|system|pipeline|challenge))?|It|The (?:project|system|pipeline|challenge))\s+(?:required|taught|reinforced)\b|"
            r"\brequired\s+(?:careful|handling|testing|validation|verification|me to|us to)\b",
            sentence, re.I)
        # Check the performed action separately from a relative reflection.
        # Other incident/guarantee checks still inspect the entire sentence.
        action_clause = re.split(r',\s*which\b|\bwhich\s+(?:forced|helped|taught|gave)\b', sentence,
                                 maxsplit=1, flags=re.I)[0]
        method_words = content_words(action_clause)
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
               'complex challenging challenge significant numerous various real-world hardest difficult technical faced implementation'.split())
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
    normalized_stop = {stem(word) for word in TOKENS.findall(' '.join(stop).replace('-', ' '))}
    words = {stem(word) for word in TOKENS.findall(str(text).casefold().replace('-', ' ')) if len(word) >= 3} - normalized_stop
    if 'pathfind' in words:
        words.remove('pathfind')
        words.add('search')
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
            cited = evidence_text([known[identifier] for identifier in identifiers])
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


def entailment_issues(answer, claims, known, used, cross_lingual=False):
    """``cross_lingual``: the answer is not in the language of the evidence (a Spanish draft of English facts). Words cannot be
    compared across languages, so the lexical overlap of a claim with its evidence (and with the answer) is not asked of it:
    the claim then quotes the evidence it relies on, and the numbers, names and the independent review carry the check."""
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
        cited = evidence_text([known[x] for x in claim.get('fact_ids', []) if x in known])
        words = content_words(claim.get('text', ''))
        missing = words - content_words(cited)
        # Allow normal paraphrases, while refusing a claim whose substantive
        # detail is mostly absent from its cited source.
        subjective = bool(re.search(r'\b(?:I (?:am|would be) (?:interested|drawn|excited)|I (?:want|hope|prefer)|'
                                   r'I (?:enjoy|value|appreciate)\b|(?:excited|interested|passionate|drawn)\s+(?:about|in|to)|'
                                   r'(?:my|this|these|those) (?:professional )?(?:background|interests?|experience)[^.!?]{0,200}\balign|'
                                   r'\bopportunity to|\bchance to)\b', claim.get('text', ''), re.I))
        historical = bool(re.search(r'\b(?:built|developed|deployed|implemented|managed|led|resolved|increased|reduced|achieved)\b', claim.get('text', ''), re.I))
        if len(words) >= 4 and len(missing) / len(words) > .5 and not (subjective and not historical):
            issues.append('UNSUPPORTED_LLM_CLAIM: claim contains detail absent from cited evidence: ' + ', '.join(sorted(missing)[:6]))
        reflected = words & content_words(answer)
        if len(words) >= 4 and len(reflected) / len(words) < .35 and not cross_lingual:
            issues.append('UNSUPPORTED_LLM_CLAIM: evidence claim is not reflected in the actual answer')
        named_details = re.findall(r'\b[A-Z][A-Za-z0-9+#.-]*\b', claim.get('text', ''))[1:]
        if any(word.casefold() not in answer.casefold() for word in named_details):
            issues.append('UNSUPPORTED_LLM_CLAIM: a named detail in evidence claims is not present in the actual answer')
    return issues


def candidate_numeric_prose(answer, generated, facts, company_sources, company_names):
    """Exclude exact sourced employer clauses, never candidate accomplishments."""
    candidate_text = answer
    candidate_entities = {f.get('entity') for f in facts if f.get('entity') and f.get('category') in
                          {'project', 'experience', 'education'}}
    for claim in generated.get('company_claims', []):
        text, urls = claim.get('text', ''), claim.get('source_urls', [])
        if (not text or text not in candidate_text or not urls or any(url not in company_sources for url in urls)
                or re.search(r'\b(?:I|my|we|our)\b', text, re.I)
                or any(name.casefold() in text.casefold() for name in candidate_entities)
                or not any(name and name.casefold() in text.casefold() for name in company_names)):
            continue
        source = '\n'.join(company_sources[url] for url in urls)
        if numeric_claims(text) <= numeric_claims(source):
            candidate_text = candidate_text.replace(text, ' ')
    return candidate_text


# A present-tense job: 'I work at', 'I currently work for', 'I am now employed by', 'actualmente trabajo en'.
CURRENT_EMPLOYMENT = re.compile(
    r"\b(?:i|we) (?:(?:currently|now|presently|also|still) )?(?:work|am working|am employed|serve|am based)\s+(?:at|for|by|with)\b|"
    r"\b(?:currently|now|presently|at present|right now)\b[^.!?]{0,15}\b(?:work(?:ing)?|employed)\s+(?:at|for|by|with)\b|"
    r"\bcurrently\s*,?\s*(?:as\b|employed|working)|\bmy current (?:role|job|position|employer)\b|"
    r"\bactualmente\b[^.!?]{0,15}\btrabaj\w+\s+(?:en|para|con)\b|\btrabajo\s+(?:actualmente\s+)?(?:en|para)\b|"
    r"\bsoy emplead[oa]\b|\bmi (?:puesto|empleo|trabajo|empleador) actual\b", re.I)
SPELLED_NUMBERS = {}
for _value, _words in enumerate(('three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen '
                                 'eighteen nineteen twenty').split(), start=3):
    SPELLED_NUMBERS[_words] = _value
for _value, _words in enumerate(('tres cuatro cinco seis siete ocho nueve diez ONCE doce trece catorce quince dieciseis diecisiete '
                                 'dieciocho diecinueve veinte').split(), start=3):
    if _words != 'ONCE':  # 'once' is a word of English ('once a week'): the Spanish eleven is not read
        SPELLED_NUMBERS[_words] = _value
for _tens, _value in (('thirty', 30), ('forty', 40), ('fifty', 50), ('sixty', 60), ('seventy', 70), ('eighty', 80), ('ninety', 90),
                      ('hundred', 100), ('thousand', 1000), ('million', 1000000), ('treinta', 30), ('cuarenta', 40),
                      ('cincuenta', 50), ('sesenta', 60), ('setenta', 70), ('ochenta', 80), ('noventa', 90), ('cien', 100),
                      ('ciento', 100), ('mil', 1000), ('millon', 1000000), ('millones', 1000000)):
    SPELLED_NUMBERS[_tens] = _value
SPELLED = re.compile(r'(?<![a-z])(' + '|'.join(sorted(SPELLED_NUMBERS, key=len, reverse=True)) + r')(?![a-z])')
# Words of ordinary prose that are capitalised and no proper noun.
NOT_NAMES = set(w.casefold() for w in (
    'I I\'m I\'ve I\'d I\'ll English Spanish French German Italian Portuguese Dutch Arabic Chinese Japanese Korean Russian Hindi '
    'January February March April May June July August September October November December Monday Tuesday Wednesday Thursday '
    'Friday Saturday Sunday The This That These Those My Our Your Its Their A An And Or But So As At In On For With By From To Of '
    'Yo Mi Mis El La Los Las Un Una Y O Pero Con Para Por En De Del Al Este Esta Estos Estas Como Cuando Donde').split())
EMPLOYER_CUE = re.compile(r"\b(?:at|for|with|by|joined|from|via|en|para|con|por|desde|trabaj\w+ en|empresa)\s+$", re.I)
ORGANISATION = re.compile(r"\b(?:Inc|Ltd|LLC|LLP|Corp|Corporation|GmbH|S\.?A\.?|S\.?L\.?|Limited|Bank|Banco|University|Universidad|"
                          r"College|Technologies|Labs|Group|Grupo|Systems|Solutions|Partners|Studios?|Consulting|Capital|Holdings|"
                          r"Industries|Foundation|Fundaci[oó]n|Institute|Instituto|Agency|Agencia)\b")
NAME_PHRASE = re.compile(r"[A-ZÁÉÍÓÚÑÜ][\w&'’.-]*(?:\s+(?:(?:of|de|del|de la|and|y|&)\s+)?[A-ZÁÉÍÓÚÑÜ][\w&'’.-]*)*")
# Things a draft about a job, a project or a motive has no business saying unless the evidence it cites does.
PRIVATE_IN_ANSWER = re.compile(
    r"\b(?:visa|work permit|right to work|eligible to work|authori[sz]ed to work|legally (?:able|allowed|entitled) to work|"
    r"available (?:to start|immediately|from)|start immediately|sponsorship|citizen(?:ship)?|nationality|passport|date of birth|born in|years old|"
    r"my salary|salary expectations?|gender|religion|ethnic\w*|disabilit\w*|marital|pregnan\w*|sexual orientation|criminal record|"
    r"convicted|visado|permiso de trabajo|derecho a trabajar|patrocinio|ciudadan[ií]a|nacionalidad|pasaporte|fecha de nacimiento|"
    r"mi salario|pretensiones salariales|g[eé]nero|religi[oó]n|etnia|discapacidad|estado civil|antecedentes penales)\b", re.I)
COMPLETION = re.compile(r"\b(?:i (?:have |'ve )?(?:hold|earned|obtained|completed|graduated|received|got|achieved)|(?:graduated|"
                        r"completed|finished)|me (?:he )?(?:graduado|titulado)|he (?:completado|obtenido|terminado|finalizado)|"
                        r"tengo (?:un|el) (?:t[ií]tulo|m[aá]ster|grado))\b", re.I)


def _plain(text):
    return unicodedata.normalize('NFKD', str(text or '')).encode('ascii', 'ignore').decode().casefold()


def fabrication_issues(answer, facts, cited_facts, context_text=''):
    """What a draft states that no evidence it was given supports, checked without a model and in any language: an EMPLOYER or
    organisation the candidate's facts and the question never name, a spelled-out number ('fifty engineers', 'diez años') that
    no cited fact states, something private (a visa, a nationality, a salary) the cited facts do not hold, and a degree
    presented as finished whose record is still in progress. The independent review can then judge the rest."""
    issues = []
    everything = _plain(' '.join(str(f.get('entity') or '') + ' ' + str(f.get('value')) for f in facts) + ' ' + str(context_text))
    cited = _plain(' '.join(str(f.get('entity') or '') + ' ' + str(f.get('value')) for f in cited_facts))
    for sentence in re.split(r'(?<=[.!?])\s+|\n+', answer):
        for match in NAME_PHRASE.finditer(sentence):
            tokens = list(re.finditer(r"[^\s]+", match.group(0)))
            # the words of ordinary prose at the edges ('At', 'I') are no part of a name: 'At Globex Corporation I built'
            while tokens and tokens[0].group(0).strip(" .,;:'").casefold() in NOT_NAMES:
                tokens.pop(0)
            while tokens and tokens[-1].group(0).strip(" .,;:'").casefold() in NOT_NAMES:
                tokens.pop()
            if not tokens:
                continue
            begin = match.start() + tokens[0].start()
            phrase = sentence[begin: match.start() + tokens[-1].end()].strip(' .,;:')
            first = not sentence[:begin].strip()
            if not phrase or (len(phrase.split()) == 1 and (first or len(phrase) < 3)):
                continue
            employer = bool(EMPLOYER_CUE.search(sentence[:begin])) or bool(ORGANISATION.search(phrase))
            # 'Python y OpenCV' and 'Python and OpenCV' are the same two names: the connective is a word of a language, not of the
            # name, so each name is looked for on its own (a name of two words, 'Globex Corporation', is looked for whole)
            parts = [part for part in re.split(r"\s+(?:of|de|del|de la|and|y|e|&)\s+", phrase) if part.strip()]
            if employer and not all(_plain(part) in everything for part in parts):
                issues.append('UNSUPPORTED_LLM_CLAIM: an employer or organisation absent from the evidence: ' + phrase)
    evidenced = {value for value in (SPELLED_NUMBERS.get(w) for w in SPELLED.findall(cited))}
    evidenced |= {int(d) for d in re.findall(r'\b\d{1,7}\b', cited)}
    for word in dict.fromkeys(SPELLED.findall(_plain(answer))):
        value = SPELLED_NUMBERS[word]
        if value not in evidenced:
            issues.append('UNSUPPORTED_LLM_CLAIM: number ' + word + ' is absent from cited evidence')
    for found in dict.fromkeys(m.group(0).casefold() for m in PRIVATE_IN_ANSWER.finditer(answer)):
        if _plain(found) not in cited and _plain(found) not in _plain(context_text):
            issues.append('UNSUPPORTED_LLM_CLAIM: private information not in the cited evidence: ' + found)
    for sentence in re.split(r'(?<=[.!?])\s+|\n+', answer):
        if not COMPLETION.search(sentence):
            continue
        for entity in {f['entity'] for f in facts if f.get('category') == 'education' and f.get('entity')}:
            if entity.casefold() in sentence.casefold():
                states = [str(f['value']).casefold() for f in facts if f.get('entity') == entity and f.get('key') == 'status']
                if states and 'completed' not in states:
                    issues.append('UNSUPPORTED_LLM_CLAIM: a qualification at ' + entity + ' is presented as finished, but is in progress')
    return issues


# --- the language a draft is written in -------------------------------------------------------------------------------------------
STOP_WORDS = {
    'English': set('the of and to in is that for with as are was were be been have has had it its this these those on by from at or an '
                   'my our your we i you they their not but which who what when where how also can will would about into over than '
                   'more most such through during while between both each other'.split()),
    'Spanish': set('el la los las de del que y en un una unos unas es son fue fueron ser ha han he hemos para con por como mi mis su '
                   'sus se lo al pero mas tambien sobre entre este esta estos estas donde cuando muy sin desde hasta nuestro nuestra '
                   'nuestros nuestras tengo tiene tienen porque durante cada otros otras'.split()),
    'French': set('le la les de des du et en un une est sont dans que qui pour avec sur par au aux ce cette ces mon ma mes notre nos '
                  'votre vos je nous vous ils elle il pas plus mais ou ainsi aussi tres comme entre depuis pendant chaque'.split()),
    'German': set('der die das den dem des und in ist sind war waren ein eine einen einem einer zu mit von fur auf bei als auch nicht '
                  'ich wir sie er es sich dass wie oder aber nach uber mein meine unser unsere durch zwischen wahrend jede'.split()),
    'Portuguese': set('o a os as de do da dos das que e em um uma uns umas e sao foi para com por como meu minha meus minhas seu sua '
                      'se ao mas tambem sobre entre este esta estes estas onde quando muito sem desde ate nosso nossa tenho tem'.split()),
    'Italian': set('il lo la i gli le di del della dei delle che e in un una uno e sono per con come mio mia miei mie suo sua si al ma '
                   'anche su tra fra questo questa questi queste dove quando molto senza da fino nostro nostra ho ha'.split()),
}


def language_of(text: str) -> tuple[str | None, dict]:
    """The language a text is written in, by the share of its words that are that language's most frequent function words (None when
    it is too short to say, or no language of the table is clearly ahead). No model, no network."""
    words = re.findall(r"[a-z]+", unicodedata.normalize('NFKD', str(text or '')[:4000]).encode('ascii', 'ignore').decode().lower())
    if len(words) < 12:
        return None, {}
    share = {language: sum(word in stop for word in words) / len(words) for language, stop in STOP_WORDS.items()}
    best = max(share, key=share.get)
    others = max(value for language, value in share.items() if language != best)
    return (best if share[best] >= .12 and share[best] >= 1.5 * others else None), share


def wrong_language_issue(answer, wanted):
    """The question (or the page) asks for ``wanted``: a draft written in another language of the table is not that answer."""
    if wanted not in STOP_WORDS:
        return None
    found, share = language_of(answer)
    if found is not None and found != wanted:
        return f'INSTRUCTION_COMPLIANCE: the answer is written in {found}, the form asks for {wanted}'
    return None


# --- credentials, titles, awards, languages, technologies and legal status a draft can only state if a cited fact does ----------
CLAIM_TERMS = {
    'a doctorate': r"\bph\.?d\b|\bdoctorate\b|\bdoctoral\b|\bdphil\b|\bdoctorado\b",
    "a master's degree": r"\bmaster'?s\b|\bmsc\b|\bm\.sc\b|\bmba\b|\bmeng\b|\bmaster\b|\bmaestria\b|\bpostgraduate\b|\bposgrado\b|\bpostgrado\b",
    "a bachelor's degree": r"\bbachelor'?s\b|\bbsc\b|\bb\.sc\b|\bbeng\b|\bundergraduate\b|\blicenciatura\b|\bgrado\b",
    'a thesis': r"\btheses\b|\bthesis\b|\bdissertation\b|\btesis\b|\btfm\b|\btfg\b",
    'a certification': r"\bcertified\b|\bcertifications?\b|\bcertificates?\b|\bcertificad[oa]s?\b|\bchartered\b|\bceng\b|\bieng\b|"
                       r"\blicen[cs]ed\b|\baccredited\b|\baccreditation\b|\bdiploma\b|\bcolegiad[oa]\b",
    'a security clearance': r"\bclearance\b|\bhabilitacion de seguridad\b",
    'an award': r"\bwon\b|\bwinners?\b|\bawards?\b|\bawarded\b|\bprizes?\b|\bfirst place\b|\bhackathons?\b|\bscholarships?\b|\bpremios?\b|"
                r"\bbecas?\b|\bganador\w*\b|\bgalardon\w*\b",
    'a promotion': r"\bpromoted\b|\bpromotions?\b|\bpromovid[oa]s?\b|\bascendid[oa]s?\b",
    'a founded company': r"\bfounded\b|\bco-?founded\b|\bfounders?\b|\bstarted (?:my own|a|an|our) (?:own )?(?:company|startup|business)\b|"
                         r"\bfund[eo]\b|\bfundador\w*\b|\bfundamos\b",
    'a leadership role': r"\bled (?:a |an |the |my |our )?(?:\w+ ){0,3}teams?\b|\bmanaged (?:a |an |the |my |our )?(?:\w+ ){0,3}teams?\b|"
                         r"\bteam lead(?:er)?\b|\bengineering manager\b|\blider[eo]\b|\bdirig[io]\b|\bhead of\b",
    'years of experience': r"\bdecades?\b|\bdecada\b",
    'a title': r"\bI (?:was|am|became|worked as|served as|joined as) (?:a |an |the )?(?:senior|principal|staff|lead|head|director|chief|manager)\b|"
               r"\b(?:soy|fui|trabaje como|trabajo como) (?:un |una |el |la )?(?:senior|principal|lider|director|jefe|gerente)\b",
}
TECHNOLOGIES = set('python java javascript typescript rust golang kotlin swift scala ruby php haskell sql postgresql postgres mysql '
                   'mongodb redis kafka docker kubernetes terraform aws azure gcp linux react angular vue django flask fastapi spring '
                   'pytorch tensorflow keras pandas numpy scikit-learn sklearn spark hadoop c++ c# git graphql opencv jupyter matlab '
                   'tableau excel unity unreal swiftui flutter nginx ansible jenkins elasticsearch rabbitmq cassandra snowflake '
                   'databricks airflow dbt hugging transformers langchain llama openai'.split())
TECH_TOKEN = re.compile(r"(?<![A-Za-z0-9+#.-])([A-Za-z][A-Za-z0-9+#.-]*[A-Za-z0-9+#])(?![A-Za-z0-9+#-])|(?<![A-Za-z0-9+#])(C\+\+|C#)")
# 'I worked at google': an employer is named even without capitals; a determiner or a common noun after the verb is no employer
EMPLOYED_AT = re.compile(r"\b(?:worked|work|working|interned|interning|employed|joined|volunteered|trabaje|trabajo|trabajaba)\s+(?:at|for|by|en|para)\s+"
                         r"([a-z][a-z0-9&.-]{2,}(?:\s+[a-z][a-z0-9&.-]{2,}){0,1})")
COMMON_AFTER_WORKED = set('a an the my our their his her its this that these those one two several many some various multiple other another '
                          'each every both all any no more most few team teams company companies startup startups firm firms home night '
                          'nights weekends weekend university universities school schools lab labs agency agencies bank banks hospital '
                          'project projects client clients customer customers organisation organisation organization organizations '
                          'research least most about over under around nearly almost roughly part full time scale speed pace '
                          'un una el la los las mi mis nuestro nuestra varios varias algunos algunas otro otra cada todo toda '
                          'equipo equipos empresa empresas proyecto proyectos cliente clientes universidad escuela casa'.split())
SPOKEN = re.compile(r"\b(?:fluent(?:ly)?|native(?:ly)?|bilingual|proficient|speak(?:s)?|spoke|hablo|fluido|nativo|biling[uü]e|domino)\b", re.I)
LANGUAGE_NAMES = ('english', 'spanish', 'french', 'german', 'portuguese', 'italian', 'dutch', 'arabic', 'mandarin', 'chinese', 'japanese',
                  'korean', 'russian', 'hindi', 'polish', 'turkish', 'catalan', 'ingles', 'espanol', 'frances', 'aleman', 'portugues',
                  'italiano', 'chino', 'japones', 'ruso')


def credential_issues(answer, cited_facts, context_text=''):
    """What a draft CLAIMS that no cited fact (nor the question or job) states, found deterministically: a doctorate, a master's, a
    certification, a clearance, an award, a promotion, a founded company, a team led, a decade of experience, a title, a spoken
    language, a technology. The independent reviewer then judges only what remains; with it disabled these are still never offered
    as written. (The status of the right to work, a nationality, a salary and a visa are ``PRIVATE_IN_ANSWER``.)"""
    issues = []
    evidence = _plain(' '.join(str(f.get('entity') or '') + ' ' + str(f.get('key') or '') + ' ' + str(f.get('value')) for f in cited_facts) + ' ' + str(context_text))
    text = _plain(answer)
    for what, pattern in CLAIM_TERMS.items():
        regex = re.compile(pattern, re.I)
        hit = regex.search(answer)
        if hit and not regex.search(evidence):
            issues.append(f'UNSUPPORTED_LLM_CLAIM: states {what} that no cited fact states: {hit.group(0).strip()}')
    for sentence in re.split(r'(?<=[.!?])\s+|\n+', answer):
        if SPOKEN.search(sentence):
            plain_sentence = _plain(sentence)
            for name in LANGUAGE_NAMES:
                if re.search(r'(?<![a-z])' + name + r'(?![a-z])', plain_sentence) and not re.search(r'(?<![a-z])' + name + r'(?![a-z])', evidence):
                    issues.append('UNSUPPORTED_LLM_CLAIM: a spoken language that no cited fact states: ' + name)
    for match in EMPLOYED_AT.finditer(text):
        name = match.group(1).casefold()
        if name.split()[0] not in COMMON_AFTER_WORKED and name not in evidence:
            issues.append('UNSUPPORTED_LLM_CLAIM: an employer or organisation absent from the evidence: ' + match.group(1))
    seen = set()
    for match in TECH_TOKEN.finditer(answer):
        token = (match.group(1) or match.group(2) or '').strip('.-')
        key = token.casefold()
        if key in seen or not (key in TECHNOLOGIES or re.search(r'[A-Za-z][+#]', token)):
            continue
        seen.add(key)
        if _plain(token) not in evidence and key not in evidence:
            issues.append('UNSUPPORTED_LLM_CLAIM: a technology that no cited fact states: ' + token)
    return issues


class AnswerEvidenceValidator:
    def validate(self, generated, facts, question_id, intent, output_requirements, previous_answers=(), resume=None,
                 *, company_sources=None, company_names=(), context_text=''):
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
        numeric_prose = candidate_numeric_prose(answer, generated, facts, company_sources or {}, company_names)
        for number, unit in factual_numeric_claims(numeric_prose, cited_facts) - factual_numeric_claims(source, cited_facts):
            issues.append(f'UNSUPPORTED_LLM_CLAIM: number {number}{unit} is absent from cited evidence')
        used_entities = {known[x].get('entity') for x in used if x in known and known[x].get('entity')
                         and known[x].get('category') in {'project', 'experience'}}
        named_entities = {name for name in used_entities if name.casefold() in answer.casefold()}
        for sentence in re.split(r'(?<!\d)[.!?](?:\s+|$)', numeric_prose):
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
        if not current and CURRENT_EMPLOYMENT.search(answer):
            issues.append('UNSUPPORTED_LLM_CLAIM: current employment is not confirmed')
        issues.extend(fabrication_issues(answer, facts, cited_facts, ' '.join([context_text, *(company_sources or {}).values(),
                                                                                *company_names])))
        issues.extend(credential_issues(answer, cited_facts, ' '.join([context_text, *company_names])))
        wrong_language = wrong_language_issue(answer, output_requirements.get('language', 'English'))
        if wrong_language:
            issues.append(wrong_language)
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
        issues.extend(entailment_issues(answer, claims, known, used,
                                        cross_lingual=output_requirements.get('language', 'English') != 'English'))
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
