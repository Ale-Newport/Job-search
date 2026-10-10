"""Public evidence must be traceable, reviewed, bounded and additive."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from jobagent.db import Database
from jobagent.intelligence.answer_evidence import CandidateFactRetriever
from jobagent.intelligence.public_evidence import import_public_evidence, portfolio_projects, snapshot
from jobagent.intelligence.store import Knowledge, entity, ingest_fact


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / 'public.sqlite3')


@pytest.fixture
def bundle():
    text = 'Example Maze. Sole author. Uses Python. Breadth-first search finds paths on an unweighted grid.'
    return {'snapshots': {'doc': snapshot('https://example.test/projects/maze', text, '2026-10-10T12:00:00+00:00')},
            'projects': [{'name': 'Example Maze', 'aliases': ['Maze'], 'facts': [
                {'concept': 'public_implementation_details', 'value': ['Breadth-first search on an unweighted grid.'],
                 'source_id': 'doc', 'excerpt': 'Breadth-first search finds paths on an unweighted grid.', 'reviewed': True},
                {'concept': 'contribution', 'value': 'Sole author.', 'source_id': 'doc', 'excerpt': 'Sole author.', 'reviewed': True},
                {'concept': 'technologies', 'value': ['Python'], 'type': 'list', 'source_id': 'doc', 'excerpt': 'Uses Python.', 'reviewed': True},
            ]}]}


def test_provenance_idempotency_and_cache_version(db, bundle):
    original_version = Knowledge(db).version
    first = import_public_evidence(db, bundle)
    changed_version = Knowledge(db).version
    second = import_public_evidence(db, bundle)
    assert first['facts_created'] == 3 and second['facts_created'] == 0
    assert first['fact_ids'] == second['fact_ids']
    assert original_version != changed_version == Knowledge(db).version
    facts = Knowledge(db).rows
    assert len(facts) == 3
    for fact in facts:
        evidence = fact['evidence']
        assert evidence['url'] == bundle['snapshots']['doc']['url']
        assert evidence['snapshot_sha256'] == bundle['snapshots']['doc']['sha256']
        assert evidence['excerpt'] in bundle['snapshots']['doc']['content']
        assert evidence['reviewed'] is True
        assert fact['source_priority'] == 60
    assert db.one('SELECT COUNT(*) AS n FROM knowledge_relationships')['n'] == 1


def test_unreviewed_website_labels_never_become_llm_evidence(db, bundle):
    for spec in bundle['projects'][0]['facts']:
        spec.pop('reviewed')
        spec['source_status'] = 'verified'
    before = import_public_evidence(db, bundle)
    assert before['verified_facts'] == 0 and before['unverified_facts'] == 3
    assert CandidateFactRetriever(db).retrieve('Explain the maze project', 'TECHNICAL_CHALLENGE') == []
    assert db.one('SELECT COUNT(*) AS n FROM knowledge_relationships')['n'] == 0
    # An explicit later review appends verified evidence without destroying the observation.
    for spec in bundle['projects'][0]['facts']:
        spec['reviewed'] = True
    after = import_public_evidence(db, bundle)
    assert after['verified_facts'] == 3 and after['facts_created'] == 3
    assert len(Knowledge(db).rows) == 6
    assert {fact['id'] for fact in CandidateFactRetriever(db).retrieve('Explain the maze project', 'TECHNICAL_CHALLENGE')} == set(after['fact_ids'])


@pytest.mark.parametrize('corruption', ['hash', 'excerpt', 'identity', 'resume_claim', 'missing_value'])
def test_invalid_or_unscoped_claims_are_rejected_before_mutation(db, bundle, corruption):
    if corruption == 'hash':
        bundle['snapshots']['doc']['content'] += ' Tampered after capture.'
    elif corruption == 'excerpt':
        bundle['projects'][0]['facts'][0]['excerpt'] = 'Made up performance result.'
    elif corruption == 'identity':
        bundle['projects'][0]['facts'][0]['concept'] = 'email'
    elif corruption == 'resume_claim':
        bundle['projects'][0]['facts'][0]['concept'] = 'not_on_resume'
    else:
        bundle['projects'][0]['facts'][0].pop('value')
    with pytest.raises(ValueError):
        import_public_evidence(db, bundle)
    assert db.one('SELECT COUNT(*) AS n FROM knowledge_entities')['n'] == 0
    assert db.one('SELECT COUNT(*) AS n FROM facts')['n'] == 0


def test_cv_priority_alias_resolution_and_history_are_preserved(db, bundle):
    person = entity(db, 'person', 'Example Candidate')
    email = ingest_fact(db, person, 'email', 'candidate@example.test', source='CV', evidence={}, verified=True, priority=100)
    project = entity(db, 'project', 'Maze', ['Example Maze'])
    cv_fact = ingest_fact(db, project, 'technologies', ['JavaScript'], value_type='list', source='CV', evidence={}, verified=True, priority=95)
    db.execute("INSERT INTO saved_answers VALUES('historical','Historical question','Exactly what was sent','[]',1,'2025-01-01','2025-01-01')")
    history = db.query('SELECT * FROM saved_answers')
    identity = db.one('SELECT * FROM facts WHERE id=?', (email,))
    result = import_public_evidence(db, bundle)
    assert result['entity_ids']['Example Maze'] == project
    assert Knowledge(db).fact(project, 'technologies')['id'] == cv_fact
    assert Knowledge(db).fact(project, 'technologies')['value'] == ['JavaScript']
    assert db.query('SELECT * FROM saved_answers') == history
    assert db.one('SELECT * FROM facts WHERE id=?', (email,)) == identity
    assert db.one("SELECT COUNT(*) AS n FROM knowledge_entities WHERE kind='project'")['n'] == 1


def test_mid_import_ambiguity_rolls_back_whole_bundle(db, bundle):
    entity(db, 'project', 'First Existing')
    entity(db, 'project', 'Second Existing')
    ambiguous = deepcopy(bundle['projects'][0])
    ambiguous.update(name='Ambiguous', aliases=['First Existing', 'Second Existing'])
    bundle['projects'].append(ambiguous)
    with pytest.raises(ValueError, match='more than one'):
        import_public_evidence(db, bundle)
    assert db.one('SELECT COUNT(*) AS n FROM facts')['n'] == 0
    assert db.one("SELECT COUNT(*) AS n FROM knowledge_entities WHERE name='Example Maze'")['n'] == 0


@pytest.mark.parametrize('priority', [0, 61, True])
def test_public_sources_cannot_override_authoritative_priority(db, bundle, priority):
    with pytest.raises(ValueError, match='priority'):
        import_public_evidence(db, bundle, source_priority=priority)


def test_saved_portfolio_parser_uses_data_without_executing_page_scripts(db):
    projects = [{'title': 'Published maze', 'id': 'maze', 'status': 'published', 'hidden': False},
                {'title': 'Draft', 'status': 'draft'}, {'title': 'Hidden', 'status': 'published', 'hidden': True}]
    payload = '1:' + json.dumps({'projects': projects}, separators=(',', ':'))
    script = 'self.__next_f.push(' + json.dumps([1, payload]) + ')'
    page = '<script>throw new Error("untrusted website instructions");</script><script>' + script + '</script>'
    assert [row['title'] for row in portfolio_projects(page)] == ['Published maze']
    source = snapshot('https://example.test', page, '2026-10-10T12:00:00+00:00')
    record = {'name': 'Published maze', 'facts': [{'concept': 'description', 'value': 'Published maze',
              'source_id': 'page', 'excerpt': 'Published maze', 'reviewed': True}]}
    assert import_public_evidence(db, {'snapshots': {'page': source}, 'projects': [record]})['verified_facts'] == 1


@pytest.mark.parametrize('corruption', ['schema', 'http', 'credentials', 'timezone', 'source_object', 'project_object', 'fact_object'])
def test_malformed_source_metadata_does_not_create_partial_knowledge(db, bundle, corruption):
    if corruption == 'schema':
        bundle['schema_version'] = 2
    elif corruption == 'http':
        bundle['snapshots']['doc']['url'] = 'http://example.test/projects/maze'
    elif corruption == 'credentials':
        bundle['snapshots']['doc']['url'] = 'https://user:secret@example.test/projects/maze'
    elif corruption == 'timezone':
        bundle['snapshots']['doc']['retrieved_at'] = '2026-10-10T12:00:00'
    elif corruption == 'source_object':
        bundle['snapshots']['doc'] = 'not a snapshot'
    elif corruption == 'project_object':
        bundle['projects'][0] = 'not a project'
    else:
        bundle['projects'][0]['facts'][0] = 'not a fact'
    with pytest.raises(ValueError):
        import_public_evidence(db, bundle)
    assert db.one('SELECT COUNT(*) AS n FROM facts')['n'] == 0
    assert db.one('SELECT COUNT(*) AS n FROM knowledge_entities')['n'] == 0


def test_cli_requires_existing_explicit_database_and_repeats_safely(tmp_path, db, bundle):
    root = Path(__file__).resolve().parents[2]
    command = [sys.executable, str(root / 'scripts/import_public_evidence.py')]
    payload = tmp_path / 'reviewed.json'
    payload.write_text(json.dumps(bundle), encoding='utf-8')
    environment = dict(os.environ, PYTHONPATH=str(root / 'backend'))
    missing = tmp_path / 'must-not-create.sqlite3'
    rejected = subprocess.run([*command, str(missing), str(payload)], env=environment, capture_output=True, text=True)
    assert rejected.returncode != 0 and 'must already exist' in rejected.stderr
    assert not missing.exists()
    first = subprocess.run([*command, str(db.path), str(payload)], env=environment, capture_output=True, text=True, check=True)
    second = subprocess.run([*command, str(db.path), str(payload)], env=environment, capture_output=True, text=True, check=True)
    assert json.loads(first.stdout)['facts_created'] == 3
    assert json.loads(second.stdout)['facts_created'] == 0
    assert len(Knowledge(db).rows) == 3
