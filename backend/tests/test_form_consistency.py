import pytest

from jobagent.automation.adapters.base import ATSAdapter
from jobagent.db import Database
from jobagent.intelligence.engine import CandidateEngine
from jobagent.intelligence.form_consistency import review_observed_facts
from jobagent.intelligence.store import entity, ingest_fact


@pytest.fixture
def engine(tmp_path):
    db = Database(tmp_path / 'consistent.sqlite3')
    records = [('person', 'Test Candidate', {'earliest_full_time_start_date': '2027-09', 'current_company': ''}),
               ('education', 'School', {'institution': 'School', 'level': 'secondary', 'status': 'completed', 'end_date': '2023-06'}),
               ('education', 'University', {'institution': 'University', 'level': 'master', 'status': 'studying',
                                           'start_date': '2026-09', 'end_date': '2027-09', 'rank': 2}),
               ('immigration', 'UK', {'country': 'United Kingdom', 'current_right_to_work': True, 'sponsorship_now_or_future': True})]
    for kind, name, facts in records:
        eid = entity(db, kind, name)
        for key, value in facts.items():
            ingest_fact(db, eid, key, value, value_type='boolean' if isinstance(value, bool) else 'text',
                        verified=True, source='Independent evidence', evidence={'excerpt': str(value)})
    return CandidateEngine(db)


@pytest.mark.parametrize('question,value,passed', [
    ('High school name', 'School', True), ('High school name', 'University', False),
    ('Expected graduation year', '2027', True), ('Expected graduation year', '2026', False),
    ('Anticipated start date (Month/Year)', '09/2027', True), ('Anticipated start date (Month/Year)', '27 September 2027', False),
    ('Current company', 'Former Employer', False),
    ('Are you currently authorized to work in the UK?', 'Yes', True),
    ('Are you currently authorized to work in the USA?', 'Yes', False),
])
def test_observed_factual_consistency_uses_scoped_verified_knowledge(engine, question, value, passed):
    snapshot = {'elements': [{'document_id': 'd', 'form_id': 'f', 'node_id': 1, 'label': question,
                             'role': 'textbox', 'type': 'text', 'operations': ['TYPE_TEXT'], 'value': value,
                             'required': False, 'validity': {'valid': True}}]}
    result = review_observed_facts(snapshot, ATSAdapter(), engine, {'country': 'United Kingdom'})
    assert result['passed'] is passed
    assert result['checked_fields'] == 1
    assert all(value not in str(issue) for issue in result['issues'])


def test_experience_narrative_is_not_mistaken_for_a_current_employer_fact(engine):
    field = {'document_id': 'd', 'node_id': 1, 'form_id': 'f', 'role': 'textbox', 'type': 'textarea', 'tag': 'textarea',
             'label': 'Do you have prior coding experience? If so, please provide details about languages, projects or experiences that demonstrate your coding skills.',
             'operations': ['TYPE_TEXT'], 'value': 'A separately validated professional narrative.'}
    result = review_observed_facts({'elements': [field]}, ATSAdapter(), engine, {})
    assert result == {'passed': True, 'issues': [], 'checked_fields': 0}
