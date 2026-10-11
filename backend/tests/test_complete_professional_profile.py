"""Complete professional context retains evidence without expanding privacy scope."""
import json

import pytest

from jobagent.db import Database, now, uid
from jobagent.intelligence.answer_evidence import CandidateFactRetriever, missing_historical_event, AnswerEvidenceValidator
from jobagent.intelligence.store import entity, ingest_fact


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / 'complete-profile.sqlite3')


def add(db, kind, name, concept, value, **kwargs):
    return ingest_fact(db, entity(db, kind, name), concept, value,
                       source=kwargs.pop('source', 'user'), evidence={}, verified=kwargs.pop('verified', True), **kwargs)


def legacy(db, category, key, value, verified=True, locked=False):
    identifier = uid()
    candidate = db.one('SELECT id FROM candidates LIMIT 1')['id']
    db.execute('INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
               (identifier, candidate, category, key, value, 'user', 'verified' if verified else 'unverified',
                int(locked), '', now(), now()))
    return identifier


def test_complete_profile_keeps_large_pool_and_zero_score_skills_and_education(db):
    expected = {add(db, 'skill', f'Functional Language {index}', 'name',
                    f'Lisp dialect {index}: ' + 'Functional programming and type theory. ' * 10)
                for index in range(48)}
    education = add(db, 'education', 'Algebra College', 'qualification', 'Bachelor of Arts in History.')
    project = add(db, 'project', 'Concurrent Playground', 'public_implementation_details',
                  'I built concurrent Java components for scheduling and display.')
    expected.update({education, project})
    retriever = CandidateFactRetriever(db)
    full = retriever.retrieve('Describe the hardest concurrency challenge.', 'CHALLENGE', limit=1, complete_profile=True)
    assert {fact['id'] for fact in full} == expected
    assert len(full) > 32 and len(json.dumps(full, ensure_ascii=False)) > 10500
    assert full[0]['id'] == project
    focused = retriever.retrieve('Describe the hardest concurrency challenge.', 'CHALLENGE')
    assert project in {fact['id'] for fact in focused}
    assert education not in {fact['id'] for fact in focused}
    assert len(focused) <= 32
    assert sum(len(json.dumps(fact, ensure_ascii=False)) for fact in focused) <= 10500


def test_complete_profile_preserves_current_value_and_private_evidence_filters(db):
    stale = add(db, 'project', 'Current Project', 'contribution', 'An older contribution.', priority=50)
    current = add(db, 'project', 'Current Project', 'contribution', 'Implemented the matching engine.', priority=100)
    rejected = {
        stale,
        add(db, 'skill', 'Unconfirmed Language', 'name', 'Unverified language.', verified=False),
        add(db, 'skill', 'Expired Language', 'name', 'Expired language.', expires_at='2000-01-01'),
        add(db, 'skill', 'Restricted Language', 'name', 'Internal language.', allowed_usage=['internal']),
        add(db, 'project', 'Private Project', 'story', 'Private professional details.', sensitivity='private'),
        add(db, 'person', 'Candidate', 'email', 'private@example.test'),
        add(db, 'person', 'Candidate', 'gender', 'protected-demographic-marker'),
        add(db, 'person', 'Candidate', 'visa', 'protected-legal-marker'),
        add(db, 'project', 'Mixed Contact Project', 'story', 'My phone is +44 7700 900123.'),
        add(db, 'project', 'Mixed Legal Project', 'story', 'I am on a Student visa.'),
        add(db, 'project', 'Mixed Credential Project', 'story', 'api_key=private-credential-marker'),
        legacy(db, 'project', 'Current Project', 'Current Project: obsolete imported contribution.'),
        legacy(db, 'skill', 'locked_unverified', 'Unverified locked skill.', verified=False, locked=True),
        legacy(db, 'contact', 'phone', '+44 7700 900123'),
        legacy(db, 'application_answer', 'old_choice', 'Unsupported prior form choice.'),
    }
    pending_old = add(db, 'skill', 'Pending Language', 'name', 'Old pending language.', priority=50)
    pending_new = add(db, 'skill', 'Pending Language', 'name', 'Unverified replacement.', verified=False, priority=100)
    conflicting_one = add(db, 'education', 'Contradictory College', 'qualification', 'First qualification.')
    conflicting_two = add(db, 'education', 'Contradictory College', 'qualification', 'Second qualification.')
    rejected.update({pending_old, pending_new, conflicting_one, conflicting_two})
    retained_legacy = legacy(db, 'skill', 'functional_language', 'Erlang.')
    facts = CandidateFactRetriever(db).retrieve('What work interests you?', 'HYPOTHETICAL', complete_profile=True)
    identifiers = {fact['id'] for fact in facts}
    assert {current, retained_legacy}.issubset(identifiers)
    assert not identifiers & rejected
    serialized = json.dumps(facts)
    assert all(marker not in serialized for marker in
               ('private@example.test', 'protected-demographic-marker', 'protected-legal-marker',
                'private-credential-marker', 'obsolete imported', '+44 7700'))


def test_complete_profile_allowed_ids_still_restrict_eligible_evidence(db):
    first = add(db, 'skill', 'Lisp', 'name', 'Lisp.')
    second = add(db, 'education', 'History College', 'qualification', 'Bachelor of Arts.')
    private = add(db, 'person', 'Candidate', 'email', 'private@example.test')
    retriever = CandidateFactRetriever(db)
    facts = retriever.retrieve('What interests you?', 'HYPOTHETICAL', allowed_ids=[first, private, 'unknown'], complete_profile=True)
    assert [fact['id'] for fact in facts] == [first]
    assert second not in {fact['id'] for fact in facts}
    assert retriever.retrieve('What interests you?', 'HYPOTHETICAL', allowed_ids=[], complete_profile=True) == []


def test_complete_profile_limit_raises_instead_of_truncating_and_boundary_is_inclusive(db):
    identifier = add(db, 'project', 'Long Professional Project', 'story', 'Professional implementation notes.')
    facts = CandidateFactRetriever(db).retrieve('Describe your project.', 'PROJECT', complete_profile=True)
    overhead = len(json.dumps(facts, ensure_ascii=False)) - len(facts[0]['value'])
    exact_length = 250000 - overhead
    exact = ('professional notes ' * (exact_length // 19 + 1))[:exact_length]
    db.execute('UPDATE facts SET value=? WHERE id=?', (json.dumps(exact), identifier))
    at_boundary = CandidateFactRetriever(db).retrieve('Describe your project.', 'PROJECT', complete_profile=True)
    assert len(json.dumps(at_boundary, ensure_ascii=False)) == 250000
    db.execute('UPDATE facts SET value=? WHERE id=?', (json.dumps(exact + 'x'), identifier))
    retriever = CandidateFactRetriever(db)
    with pytest.raises(ValueError, match='exceeds 250000 characters.*No facts were silently truncated'):
        retriever.retrieve('Describe your project.', 'PROJECT', complete_profile=True)
    assert retriever.retrieve('Describe your project.', 'PROJECT', allowed_ids=[], complete_profile=True) == []
    assert retriever.retrieve('Describe your project.', 'PROJECT') == []


def test_complete_profile_does_not_convert_architecture_into_incident_evidence(db):
    project = add(db, 'project', 'Concurrent Playground', 'public_implementation_details',
                  'Java trader threads place orders on independent order books. ScheduledExecutorService drives price ticks.')
    add(db, 'skill', 'Java', 'name', 'Java.')
    add(db, 'education', 'Algebra College', 'qualification', 'Bachelor of Arts.')
    facts = CandidateFactRetriever(db).retrieve('Describe a difficult bug you resolved.', 'CHALLENGE', complete_profile=True)
    assert 'verified specific bug incident' in missing_historical_event('Describe a difficult bug you resolved.', 'CHALLENGE', facts)
    generated = {'question_id': 'q', 'intent': 'CHALLENGE', 'answer':
                 'In Concurrent Playground, I debugged race conditions that occurred under specific interleavings.',
                 'facts_used': [project], 'claims': [], 'unsupported_claims': [],
                 'answers_question': True, 'follows_instructions': True}
    issues = AnswerEvidenceValidator().validate(generated, facts, 'q', 'CHALLENGE', {})
    assert any('undocumented debugging incident' in issue for issue in issues)


def test_current_structured_languages_supersede_stale_imported_language_list(db):
    english = add(db, 'language', 'English', 'proficiency', 'Fluent / professional working proficiency')
    spanish = add(db, 'language', 'Spanish', 'proficiency', 'Native')
    obsolete = {legacy(db, 'language', 'French (Basic)', 'French (Basic)'),
                legacy(db, 'language', 'Spanish (fluent)', 'Spanish (fluent)'),
                legacy(db, 'personal', 'location', 'A private residence marker')}
    facts = CandidateFactRetriever(db).retrieve('Describe your professional profile.', 'PROJECT', complete_profile=True)
    assert {english, spanish} <= {f['id'] for f in facts}
    assert not obsolete & {f['id'] for f in facts}
    assert 'French' not in json.dumps(facts)


@pytest.mark.parametrize('text, permitted', [
    ('I built Cinquillo 2.0 and implemented the engine and agents.', True),
    ('I built Cinquillo 3.0 and implemented the engine and agents.', False),
    ('I built Cinquillo 2.01 and implemented the engine and agents.', False),
    ('I built Cinquillo 2.0 and improved throughput by 2.0%.', False),
])
def test_exact_entity_version_is_identity_while_changed_versions_and_metrics_require_evidence(text, permitted):
    fact = {'id': 'engine', 'entity': 'Cinquillo 2.0', 'category': 'project', 'key': 'contribution',
            'value': 'Implemented the engine and agents.'}
    generated = {'question_id': 'q', 'intent': 'PROJECT', 'answer': text, 'facts_used': ['engine'],
                 'claims': [{'text': text, 'fact_ids': ['engine']}], 'unsupported_claims': [],
                 'answers_question': True, 'follows_instructions': True}
    issues = AnswerEvidenceValidator().validate(generated, [fact], 'q', 'PROJECT', {})
    if permitted:
        assert issues == []
    else:
        assert any('number' in issue or 'metric' in issue for issue in issues)
