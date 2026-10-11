"""Keep candidate disclosures out of professional model context."""
from __future__ import annotations

import json
import re


PRIVATE_CONCEPTS = {
    'full_name', 'first_name', 'last_name', 'preferred_name', 'name_pronunciation',
    'email', 'phone', 'mobile', 'telephone', 'address', 'postcode', 'postal_code',
    'nationality', 'citizenship', 'date_of_birth', 'dob', 'age', 'gender', 'sex',
    'pronouns', 'sexual_orientation', 'ethnicity', 'race', 'religion', 'disability',
    'neurodivergence', 'mental_health', 'veteran', 'veteran_status', 'adjustments',
    'visa', 'sponsorship', 'work_authorization', 'right_to_work', 'consent', 'gdpr',
    'criminal_history', 'security_clearance', 'salary', 'salary_expectations',
}
EMAIL = re.compile(r'[\w.+-]+@[\w.-]+\.[a-z]{2,}', re.I)
PHONE = re.compile(r'(?:\+?\d[\d ()-]{7,}\d)')
PERSONAL_LABEL = re.compile(
    r'(?:^|[\n;|.!?])\s*(?:name|full name|first name|last name|address|postal address|postcode|'
    r'date of birth|birth date|dob|age|gender|sex|pronouns|sexual orientation|ethnicity|race|religion|faith|'
    r'nationality|citizenship|disability|medical condition|mental health|veteran(?: status)?|'
    r'visa(?: status)?|sponsorship(?: status)?|work authori[sz]ation|right to work|'
    r'criminal (?:history|record)|security clearance|salary(?: expectation)?|consent|gdpr)\s*[:=]', re.I)
PERSONAL_PROSE = re.compile(
    r'\bmy\s+(?:full name|name|email|phone|telephone|address|postcode|date of birth|birth date|age|gender|sex|pronouns|'
    r'sexual orientation|ethnicity|race|religion|faith|nationality|citizenship|disability|'
    r'medical condition|mental health|visa|sponsorship|work authori[sz]ation|salary|criminal record)\b|'
    r'\b(?:i am|i\x27m|i identify as|as)\s+(?:(?:a|an)\s+)?(?:woman|man|male|female|'
    r'nonbinary|non-binary|transgender|gay|lesbian|straight|heterosexual|homosexual|bisexual|'
    r'Catholic|Christian|Muslim|Jewish|Hindu|Buddhist|disabled|a citizen|a national)\b|'
    r'\b(?:i am|i\x27m)\s+(?:[a-z-]+\s+){0,3}(?:citizen|national)\b(?!\s+(?:scientist|developer))|'
    r'\b(?:i am|i\x27m)\s+(?:currently\s+)?(?:on|holding)\s+[^.!?\n]{0,35}\bvisa\b|'
    r'\bi (?:have|am diagnosed with)\s+(?:(?:a|an)\s+)?(?:ADHD|autism|diabetes|depression|'
    r'anxiety disorder|medical condition|mental illness)\b|'
    r'\bi (?:am|was) (?:born\b|\d{1,3} years? old\b)|'
    r'\bi (?:hold|have|need|require)\b[^.!?\n]{0,45}\b(?:visa|sponsorship|work permit|'
    r'criminal conviction|security clearance)\b|'
    r'\bi (?:am )?(?:legally )?(?:authori[sz]ed|eligible) to work\b|'
    r'\bi (?:consent|agree)\b[^.!?\n]{0,50}\b(?:terms|privacy|processing|disclos|sharing)\w*\b|'
    r'\b(?:passport|national insurance|social security|government id)\s*(?:number|no\.?|[:=])', re.I)
CREDENTIAL = re.compile(
    r'\b(?:api[_ -]?key|access[_ -]?token|refresh[_ -]?token|password|session[_ -]?(?:id|token)|'
    r'cookie|authorization)\s*[:=]\s*\S+|\bBearer\s+[A-Za-z0-9._~+/-]{8,}|\bsk-[A-Za-z0-9_-]{12,}', re.I)
STREET_ADDRESS = re.compile(
    r'(?:^|\n)\s*\d{1,6}\s+(?:[\w\x27.-]+\s+){1,5}(?:street|road|avenue|lane|drive|'
    r'close|crescent|boulevard|st\.?|rd\.?|ave\.?)\b', re.I)
IDENTITY_URL = re.compile(r'https?://(?:www\.)?linkedin\.com/in/[^\s|]+|'
                          r'https?://(?:www\.)?github\.com/[^/\s|]+/?(?=\s|\||$)', re.I)


def contains_private_candidate_data(value):
    if isinstance(value, dict):
        return any(contains_private_candidate_data(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_private_candidate_data(item) for item in value)
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    # ISO dates and year ranges contain eight digits, whereas candidate phone
    # numbers contain at least nine. Avoid treating professional metrics as phones.
    phone_text = re.sub(r'\b\d{4}-\d{2}-\d{2}\b', '[date]', text)
    phones = []
    for match in PHONE.finditer(phone_text):
        if len(re.sub(r'\D', '', match[0])) < 9:
            continue
        indicated = (match[0].startswith('+') or bool(re.search(r'[()]|\d[ -]+\d', match[0]))
                     or bool(re.search(r'\b(?:phone|mobile|telephone|tel)\b',
                                       phone_text[max(0, match.start() - 25):match.start()], re.I)))
        metric = re.match(r'\s*(?:users|events|transactions|requests|records|samples|videos|nodes)\b',
                          phone_text[match.end():], re.I)
        if indicated or not metric:
            phones.append(match)
    return bool(EMAIL.search(text) or phones or any(pattern.search(text) for pattern in
                    (PERSONAL_LABEL, PERSONAL_PROSE, CREDENTIAL, STREET_ADDRESS, IDENTITY_URL)))


def professional_reference(value, *, entity=False):
    """Bound opaque evidence IDs and professional entity names, not metadata."""
    if not isinstance(value, str) or not 0 < len(value) <= 160:
        return False
    if re.fullmatch(r'[0-9a-f]{32}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', value, re.I):
        return True  # UUID digits are not phone numbers.
    if contains_private_candidate_data(value):
        return False
    if entity:
        return value.casefold().replace(' ', '_') not in PRIVATE_CONCEPTS
    return bool(re.fullmatch(r'[a-z][a-z0-9:_-]{0,127}', value, re.I))


def professional_resume_text(text, identity_values=()):
    """Remove personal lines/headers while retaining professional sections."""
    identities = [str(value).strip() for value in identity_values if isinstance(value, str) and value.strip()]
    result = []
    in_body = False
    section = re.compile(r'^(?:professional summary|summary|profile|objective|education|'
                         r'(?:professional |work |volunteer )?experience|employment|projects|'
                         r'(?:technical )?skills|publications|research|achievements|certifications)\s*[:\-]?$', re.I)
    for line in str(text).splitlines():
        stripped = line.strip()
        if section.fullmatch(stripped):
            in_body = True
        if contains_private_candidate_data(stripped):
            continue
        # Exact locally known names catch identity headers without guessing from
        # capitalization or mistaking an employer for a candidate's name.
        if not in_body and any(stripped.casefold() == name.casefold() for name in identities):
            continue
        for name in identities:
            if len(name.split()) >= 2 and (not in_body or re.match(re.escape(name) +
                    r'\s+(?:built|developed|implemented|led|worked|designed|created)\b', stripped, re.I)):
                line = re.sub(r'(?<!\w)' + re.escape(name) + r'(?!\w)', '[candidate omitted]', line, flags=re.I)
        if not in_body and re.fullmatch(r'(?:Curriculum Vitae|R[eé]sum[eé]|CV)', stripped, re.I):
            continue
        result.append(line)
    return '\n'.join(result).strip()
