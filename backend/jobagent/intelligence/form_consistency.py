"""Review observed factual form values against the current scoped knowledge."""

from ..automation.consent import resolution_element, selected_resolution_matches
from ..automation.reasoning import conditional_state
from .formatting import choice

FACTUAL_REVIEW_INTENTS = {
    'EDUCATION', 'DEGREE', 'GRADUATION', 'GRADE', 'GPA', 'STUDY_YEAR',
    'START_DATE', 'AVAILABILITY', 'NOTICE_PERIOD', 'WORK_EXPERIENCE',
    'PREVIOUS_EMPLOYMENT', 'WORK_AUTHORIZATION', 'SPONSORSHIP', 'VISA',
}


def review_observed_facts(snapshot, adapter, intelligence, job):
    """Dates, education, employment and legal answers share one verified view.

    Missing fields remain ordinary completion questions. Contradictions report
    control identity and intent, without copying private values into the issue.
    """
    if not snapshot or intelligence is None:
        return {'passed': True, 'issues': [], 'checked_fields': 0}
    elements = snapshot.get('elements', [])
    seen, issues, checked = set(), [], 0
    for element in elements:
        if not any(op in element.get('operations', []) for op in ('TYPE_TEXT', 'SELECT', 'CHECK')):
            continue
        key = (element.get('document_id'), element.get('form_id'),
               element.get('group') or element.get('node_id'))
        if key in seen:
            continue
        seen.add(key)
        question = adapter.question(element)
        condition = conditional_state(element, elements, adapter)
        if condition and condition['applies'] is not True:
            continue
        field = {**resolution_element(element, elements), '_condition': condition or {}}
        if element.get('role') == 'combobox' and element.get('selected_text') and not field.get('options'):
            # The observed selected label is an enum contract to verify, not
            # evidence that it is true. The candidate facts must still entail it.
            field['options'] = [{'label': element['selected_text'], 'value': element['selected_text']}]
        expected = intelligence.answer_question(question, field, job)
        if expected.get('root_cause') in {'ASYNC_GENERATION_REQUIRED', 'LLM_SETUP_REQUIRED'}:
            # Professional experience explanations receive their independent
            # narrative review; current-employer facts still use this review.
            continue
        intent = expected.get('canonical_intent')
        if intent not in FACTUAL_REVIEW_INTENTS:
            continue
        role = element.get('role')
        peers = [e for e in elements if (e.get('document_id'), e.get('form_id'), e.get('group') or e.get('node_id')) == key]
        observed = element.get('selected_text') or element.get('value') or ''
        if role == 'radio':
            observed = next((e.get('label') for e in peers if e.get('checked')), '')
        elif role == 'checkbox' and not element.get('multiple'):
            observed = 'Yes' if element.get('checked') else 'No'
        if role == 'combobox' and element.get('tag') != 'select' and not element.get('selected_text'):
            # A typed search is not an observed factual selection.
            continue
        if element.get('multiple'):
            selected = ([o['label'] for o in element.get('options', []) if o['value'] in element.get('selected_values', [])]
                        if element.get('tag') == 'select' else [e.get('label') for e in peers if e.get('checked')])
            if not selected:
                continue
            wanted = expected.get('form_value')
            matches = isinstance(wanted, list) and len(wanted) == len(selected) and all(
                choice(value, [{'label': actual, 'value': actual} for actual in selected], question=question) for value in wanted)
        else:
            if not observed:
                continue
            matches = expected.get('answer') is not None and selected_resolution_matches(expected, str(observed))
        checked += 1
        matches = matches and (element.get('validity') or {}).get('valid', True)
        if not matches:
            issues.append({'code': 'OBSERVED_FACT_CONTRADICTION' if expected.get('answer') is not None or expected.get('leave_blank')
                           else 'OBSERVED_FACT_UNVERIFIED', 'question': question, 'intent': intent,
                           'document_id': element.get('document_id'), 'node_id': element.get('node_id')})
    return {'passed': not issues, 'issues': issues, 'checked_fields': checked}
