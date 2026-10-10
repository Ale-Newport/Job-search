"""Provider contracts, independent review, privacy and per-question isolation."""
import asyncio
import json

import httpx
import pytest

from jobagent.application_answer_llm import (ANSWER_SCHEMA, SCORE_NAMES, ApplicationAnswerLLM,
                                            AnswerValidationError, ConfigurationError, ProviderError)
from jobagent.db import Database, now, uid
from jobagent.intelligence.answer_evidence import (AnswerEvidenceValidator, ApplicationConsistencyReviewer,
                                                 CandidateFactRetriever, content_words, requirements)
from jobagent.intelligence.store import entity, ingest_fact


@pytest.fixture
def db(tmp_path):
    return Database(tmp_path / 'answers.sqlite3')


@pytest.fixture
def evidence(db):
    eid = entity(db, 'project', 'Chess Assistant AI')
    fid = ingest_fact(db, eid, 'story', 'I built a chess computer vision system using Python and Stockfish.',
                      source='user', evidence={'excerpt': 'user verified'}, verified=True)
    person = entity(db, 'person', 'Candidate')
    ingest_fact(db, person, 'professional_interests', 'Software engineering, algorithms and artificial intelligence',
                source='user', evidence={}, verified=True)
    return fid


def complete(payload, answer=None, fid=None):
    fid = fid or payload['verified_facts'][0]['id']
    answer = answer or 'I built a chess computer vision system using Python and Stockfish.'
    return {'question_id': payload['question_id'], 'intent': payload['intent'], 'answer': answer,
            'facts_used': [fid], 'selected_options': [], 'selected_roles': [], 'company_claims': [], 'claims': [{'text': answer, 'fact_ids': [fid]}],
            'evidence_quality': 'HIGH', 'word_count': len(answer.split()), 'follows_instructions': True,
            'answers_question': True, 'unsupported_claims': [], 'requires_human_input': False, 'missing_facts': []}


def request_payload(request):
    body = json.loads(request.content)
    if 'input' in body:
        return json.loads(body['input'])
    if 'contents' in body:
        return json.loads(body['contents'][0]['parts'][0]['text'])
    return json.loads(body['messages'][-1]['content'])


def response_for(request, generated):
    text = json.dumps(generated)
    if request.url.path.endswith('/responses'):
        return httpx.Response(200, json={'status': 'completed', 'output': [{'content': [{'type': 'output_text', 'text': text}]}],
                                        'usage': {'input_tokens': 15, 'output_tokens': 30}})
    if request.url.path.endswith('/messages'):
        return httpx.Response(200, json={'content': [{'type': 'text', 'text': text}], 'usage': {'input_tokens': 15, 'output_tokens': 30}})
    if request.url.path.endswith(':generateContent'):
        return httpx.Response(200, json={'candidates': [{'finishReason': 'STOP', 'content': {'parts': [{'text': text}]}}],
                                        'usageMetadata': {'promptTokenCount': 15, 'candidatesTokenCount': 30}})
    if request.url.path.endswith('/api/chat'):
        return httpx.Response(200, json={'message': {'content': text}, 'prompt_eval_count': 15, 'eval_count': 30})
    return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': text}}],
                                    'usage': {'prompt_tokens': 15, 'completion_tokens': 30}})


class Keys:
    def get(self, _name):
        return 'test-only-key'


def settings(provider='ollama'):
    return {'text_provider': provider, 'text_model': 'discovered-model', 'text_retry_attempts': 0,
            'text_quality_review': True, 'monthly_budget': 10,
            **({'text_base_url': 'https://compatible.example/v1'} if provider == 'openai-compatible' else {})}


@pytest.mark.parametrize('provider', ['openai', 'anthropic', 'gemini', 'ollama', 'openai-compatible'])
async def test_native_provider_contracts_exact_question_and_independent_review(db, evidence, provider):
    calls = []
    db.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',
               (uid(), provider, json.dumps({'input_cost_per_million': 1, 'output_cost_per_million': 2}), 'configured'))
    exact = 'Explain a project you built. (One sentence)  '
    def handler(request):
        payload = request_payload(request)
        calls.append((request, payload))
        if 'generated_answer' in payload:
            generated = {'question_id': payload['question_id'], 'passed': True,
                         'scores': {name: .95 for name in SCORE_NAMES}, 'unsupported_claims': [], 'issues': [], 'reason': 'Grounded and relevant.'}
        else:
            generated = complete(payload, fid=evidence)
        return response_for(request, generated)
    llm = ApplicationAnswerLLM(db, Keys(), transport=httpx.MockTransport(handler))
    answer = await llm.generate(settings(provider), exact, {'title': 'Software Engineer', 'company': 'Example',
                                                          'description': 'Build reusable software.', 'company_context': [{'url': 'https://example.com', 'text': 'Employer makes software.'}]},
                                question_id='immutable-question')
    assert len(calls) == 2
    assert calls[0][1]['exact_question'] == exact
    assert calls[0][1]['question_id'] == 'immutable-question'
    assert calls[0][1]['job']['company'] == 'Example'
    assert calls[0][1]['company_context'][0]['url'] == 'https://example.com'
    assert answer['quality']['independent_review'] == 'passed'
    assert answer['state'] == 'ANSWER_VALIDATED'
    assert answer['answer_hash'] and answer['context_hash']
    assert answer['facts_used'] == [evidence]
    assert db.one("SELECT value FROM settings WHERE key LIKE 'ai_usage:%'")


async def test_no_provider_never_returns_generic_narrative(db, evidence):
    with pytest.raises(ConfigurationError, match='An LLM is required'):
        await ApplicationAnswerLLM(db, None).generate({'text_provider': 'none'}, 'Tell us about a project', {})


async def test_missing_key_and_insecure_endpoint_fail_explicitly(db, evidence):
    llm = ApplicationAnswerLLM(db, None)
    with pytest.raises(ConfigurationError, match='Keychain'):
        await llm._request(settings('openai'), 'system', {}, ANSWER_SCHEMA)
    with pytest.raises(ConfigurationError, match='HTTPS'):
        await llm.models(settings('openai-compatible') | {'text_base_url': 'http://remote.example/v1'})
    with pytest.raises(ConfigurationError, match='credentials'):
        await llm.models(settings('openai-compatible') | {'text_base_url': 'https://user:key@remote.example/v1'})


@pytest.mark.parametrize('failure', ['wrong_id', 'invented_number', 'unsupported_reviewer', 'malformed_schema', 'api_failure'])
async def test_failed_generation_is_rejected_and_never_fallback(db, evidence, failure):
    def handler(request):
        if failure == 'api_failure':
            return httpx.Response(503, json={'error': 'provider down'})
        payload = request_payload(request)
        if 'generated_answer' in payload:
            generated = {'question_id': payload['question_id'], 'passed': failure != 'unsupported_reviewer',
                         'scores': {name: .95 for name in SCORE_NAMES}, 'unsupported_claims': ['Invented tradeoff'] if failure == 'unsupported_reviewer' else [],
                         'issues': [], 'reason': 'Evidence check'}
        else:
            generated = complete(payload, fid=evidence)
            if failure == 'wrong_id':
                generated['question_id'] = 'another-field'
            elif failure == 'invented_number':
                generated['answer'] += ' I increased accuracy by 99%.'
            elif failure == 'malformed_schema':
                del generated['claims']
        return response_for(request, generated)
    llm = ApplicationAnswerLLM(db, None, transport=httpx.MockTransport(handler))
    with pytest.raises((AnswerValidationError, ProviderError)):
        await llm.generate(settings(), 'Tell us about your project.', {}, question_id='original-field')


async def test_bounded_concurrency_and_correct_per_field_results(db, evidence):
    active, peak = 0, 0
    person = entity(db, 'person', 'Candidate')
    career_id = ingest_fact(db, person, 'career_preferences', 'I enjoy algorithms, software engineering and building useful systems.', source='user', evidence={}, verified=True)
    teacher = entity(db, 'experience', 'Teaching')
    teaching_id = ingest_fact(db, teacher, 'story', 'Teaching and student support provide opportunities to explain difficult concepts.', source='user', evidence={}, verified=True)
    evidence_ids = {'chess': evidence, 'career': career_id, 'leadership': teaching_id}
    async def handler(request):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(.01)
        payload = request_payload(request)
        text = {'chess': 'My chess system uses computer vision and board recognition.',
                'career': 'I enjoy algorithms, software engineering and building useful systems.',
                'leadership': 'Teaching and student support provide opportunities to explain difficult concepts.'}[payload['question_id']]
        generated = complete(payload, answer=text, fid=evidence_ids[payload['question_id']])
        active -= 1
        return response_for(request, generated)
    llm = ApplicationAnswerLLM(db, None, transport=httpx.MockTransport(handler))
    questions = [{'question_id': 'chess', 'question': 'Describe your chess project.'},
                 {'question_id': 'career', 'question': 'Describe your career interests.'},
                 {'question_id': 'leadership', 'question': 'Describe a leadership experience.'}]
    results = await llm.generate_many(settings() | {'text_quality_review': False, 'text_concurrency': 2}, questions, {})
    assert peak == 2
    assert [x['question_id'] for x in results['answers']] == ['chess', 'career', 'leadership']
    assert results['answers'][0]['answer'].startswith('My chess')
    assert results['answers'][1]['answer'].startswith('I enjoy')
    assert results['answers'][2]['answer'].startswith('Teaching')
    with pytest.raises(ConfigurationError, match='unique'):
        await llm.generate_many(settings(), [questions[0], questions[0]], {})


def test_retrieval_excludes_private_expired_and_unverified_facts(db, evidence):
    person = entity(db, 'person', 'Candidate')
    private = ingest_fact(db, person, 'passport', 'secret-government-value', source='user', evidence={}, verified=True, sensitivity='private')
    expired = ingest_fact(db, person, 'professional_interests', 'expired-skill', source='user', evidence={}, verified=True, expires_at='2000-01-01')
    unverified = ingest_fact(db, person, 'professional_interests', 'invented-skill', source='import', evidence={}, verified=False, priority=20)
    candidate = db.one('SELECT id FROM candidates LIMIT 1')['id']
    db.execute('INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?)', (uid(), candidate, 'contact', 'email', 'private@example.test', 'user', 'verified', 0, '', now(), now()))
    facts = CandidateFactRetriever(db).retrieve('What motivates your software career?', 'CAREER_INTEREST')
    assert evidence in {f['id'] for f in facts}
    assert not {private, expired, unverified} & {f['id'] for f in facts}
    assert 'private@example.test' not in json.dumps(facts)


def test_claim_tokens_normalize_sentence_punctuation_and_inflection():
    assert content_words('Projects and systems. Managed security and compliance. Ensure engagement.') == content_words(
        'Project system manages security, compliance and ensures engagement')
    assert 'vue.js' in content_words('Built Vue.js projects.')
    assert 'python' in content_words('Python.')


def test_claim_paraphrase_preserves_support_and_rejects_new_historical_outcomes():
    validator = AnswerEvidenceValidator()
    fact = {'id': 'fact', 'key': 'story', 'value': 'Developed deep learning models and computer vision projects.', 'category': 'experience'}
    generated = complete({'question_id': 'q', 'intent': 'HYPOTHETICAL', 'verified_facts': [fact]},
                         answer='I developed machine learning projects and computer vision systems.', fid='fact')
    assert not validator.validate(generated, [fact], 'q', 'HYPOTHETICAL', {})
    generated['answer'] = 'I optimized content generation to ensure accuracy and engagement.'
    generated['claims'][0]['text'] = generated['answer']
    assert any('absent from cited evidence' in issue for issue in validator.validate(generated, [fact], 'q', 'HYPOTHETICAL', {}))


def test_subjective_alignment_does_not_require_literal_evidence_wording():
    validator = AnswerEvidenceValidator()
    fact = {'id': 'fact', 'key': 'professional_interests', 'value': 'Backend engineering and algorithms.', 'category': 'person'}
    answer = 'I am drawn to the opportunity to build useful software. My background aligns with this direction.'
    generated = complete({'question_id': 'q', 'intent': 'ROLE_PREFERENCE', 'verified_facts': [fact]}, answer=answer, fid='fact')
    assert not validator.validate(generated, [fact], 'q', 'ROLE_PREFERENCE', {})
    generated['answer'] += ' I resolved an indexing misconfiguration during a team conflict.'
    assert any('unsupported historical detail' in issue for issue in validator.validate(generated, [fact], 'q', 'ROLE_PREFERENCE', {}))


def test_retrieval_keeps_concrete_project_details_and_excludes_ats_consent(db, evidence):
    project = entity(db, 'project', 'Market Simulator')
    expected = {
        ingest_fact(db, project, 'public_implementation_details', 'Java trader threads place orders on independent order books.', source='public', evidence={}, verified=True),
        ingest_fact(db, project, 'public_architecture_tradeoffs', 'Concurrent strategies share price-time order matching.', source='public', evidence={}, verified=True),
        ingest_fact(db, project, 'contribution', 'Whole system: order books, matching engine and threading model.', source='user', evidence={}, verified=True),
    }
    for index in range(40):
        skill = entity(db, 'skill', f'Engineering skill {index}')
        ingest_fact(db, skill, 'name', f'Engineering algorithm {index}', source='user', evidence={}, verified=True)
    candidate = db.one('SELECT id FROM candidates LIMIT 1')['id']
    consent = uid()
    db.execute('INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?)', (consent, candidate, 'application_answer', 'AI notetaker consent', 'Yes', 'manual', 'verified', 1, '', now(), now()))
    selected = CandidateFactRetriever(db).retrieve('Describe your hardest technical challenge.', 'CHALLENGE')
    assert expected.issubset({f['id'] for f in selected})
    assert consent not in {f['id'] for f in selected}


def test_not_on_resume_rejects_same_project_under_other_evidence_ids():
    fact = {'id': 'new-id', 'key': 'story', 'entity': 'Chess Assistant AI', 'value': 'I built a chess computer vision system using Python and Stockfish.', 'category': 'project'}
    generated = complete({'question_id': 'q', 'intent': 'NOT_ON_RESUME', 'verified_facts': [fact]}, fid='new-id')
    issues = AnswerEvidenceValidator().validate(generated, [fact], 'q', 'NOT_ON_RESUME', {},
        resume={'fact_ids': ['old-id'], 'text': 'Projects: Chess Assistant AI. Developed computer vision and Stockfish integration.'})
    assert any('already named' in issue for issue in issues)


def test_numeric_claims_accept_equivalent_units_and_reject_cross_entity_metrics():
    facts = [
        {'id': 'wyllex', 'key': 'story', 'entity': 'Wyllex', 'value': 'Wyllex generated 10k videos for 200 users.', 'category': 'project'},
        {'id': 'pansofia', 'key': 'story', 'entity': 'Pansofia', 'value': 'At Pansofia I led a team of six engineers and increased client inquiries by 40%.', 'category': 'experience'},
    ]
    generated = complete({'question_id': 'q', 'intent': 'LEADERSHIP', 'verified_facts': facts}, answer='Wyllex generated 10,000 videos for 200 users.', fid='wyllex')
    validator = AnswerEvidenceValidator()
    assert not validator.validate(generated, facts, 'q', 'LEADERSHIP', {})
    generated['answer'] = 'At Wyllex I increased client inquiries by 40%.'
    generated['facts_used'] = ['wyllex', 'pansofia']
    generated['claims'] = [{'text': 'At Pansofia I increased client inquiries by 40%.', 'fact_ids': ['pansofia']}]
    assert any('attributed to an entity' in issue for issue in validator.validate(generated, facts, 'q', 'LEADERSHIP', {}))
    generated['answer'] = 'At Pansofia I increased client inquiries by 40%.'
    generated['claims'][0]['fact_ids'] = ['wyllex']
    assert any('own cited evidence' in issue for issue in validator.validate(generated, facts, 'q', 'LEADERSHIP', {}))


def test_documented_sole_author_supports_independent_responsibility():
    fact = {'id': 'labyrinth', 'key': 'contribution', 'entity': 'Labyrinth', 'value': 'Sole author. Implemented JavaScript maze generation and search algorithms.', 'category': 'project'}
    answer = 'I built Labyrinth entirely on my own, implementing JavaScript maze generation and search algorithms.'
    generated = complete({'question_id': 'q', 'intent': 'PROJECT', 'verified_facts': [fact]}, answer=answer, fid='labyrinth')
    assert not AnswerEvidenceValidator().validate(generated, [fact], 'q', 'PROJECT', {})


async def test_hypothetical_company_preferences_do_not_require_employer_claims(db, evidence):
    def handler(request):
        payload = request_payload(request)
        answer = "If Palantir didn't exist, I would be excited about a company that focuses on artificial intelligence and algorithms."
        generated = complete(payload, answer=answer, fid=next(f['id'] for f in payload['verified_facts'] if f['key'] == 'professional_interests'))
        generated['claims'] = []
        return response_for(request, generated)
    llm = ApplicationAnswerLLM(db, None, transport=httpx.MockTransport(handler))
    answer = await llm.generate(settings() | {'text_quality_review': False}, "If Palantir didn't exist, what kind of work would excite you?",
                                {'company': 'Palantir'}, question_id='hypothetical')
    assert answer['state'] == 'ANSWER_VALIDATED'


@pytest.mark.parametrize('question,expected', [
    ('Explain this in approximately 200 words', {'min_words': 180, 'max_words': 220}),
    ('Under 150 words', {'max_words': 149}),
    ('Why do you want this role? (250 word limit)', {'max_words': 250}),
    ('Please explain (250-word maximum)', {'max_words': 250}),
    ('Respond in exactly 40 words.', {'min_words': 40, 'max_words': 40, 'target_words': 40}),
    ('Maximum 500 characters', {'max_characters': 500}),
    ('One sentence', {'sentences': 1}),
    ('Please provide three examples as bullet points', {'examples': 3, 'bullets': True}),
    ('Select 1-2 products', {'min_selections': 1, 'max_selections': 2}),
])
def test_general_instruction_requirements(question, expected):
    actual = requirements(question, {'multiple': True, 'options': ['Foundry', 'Apollo']} if 'products' in question else {})
    assert actual.items() >= expected.items()


def test_not_on_resume_requires_exact_application_cv(db, evidence):
    validator = AnswerEvidenceValidator()
    fact = {'id': evidence, 'key': 'story', 'value': 'A chess project', 'category': 'project'}
    generated = complete({'question_id': 'q', 'intent': 'NOT_ON_RESUME', 'verified_facts': [fact]}, fid=evidence)
    assert any('exact uploaded resume' in x for x in validator.validate(generated, [fact], 'q', 'NOT_ON_RESUME', {}, resume=None))
    assert any('already in' in x for x in validator.validate(generated, [fact], 'q', 'NOT_ON_RESUME', {}, resume={'fact_ids': [evidence], 'text': 'Chess project'}))


def test_cross_answer_review_detects_reused_prose_and_openings():
    answers = [{'question_id': 'a', 'answer': 'I built a chess computer vision system using Python. It understands boards.'},
               {'question_id': 'b', 'answer': 'I built a chess computer vision system using Python. My career interests are software.'}]
    review = ApplicationConsistencyReviewer().review(answers)
    assert not review['passed']
    assert review['issues'][0]['code'] == 'REPEATED_LLM_ANSWER'


async def test_real_configured_provider_opt_in(tmp_path):
    """Run only with an explicitly supplied local calibration DB, never manufacture success."""
    import os
    path = os.environ.get('MERIDIAN_LLM_INTEGRATION_DB')
    if not path:
        pytest.skip('Set MERIDIAN_LLM_INTEGRATION_DB to an authorised calibration database for genuine provider calls')
    from jobagent.core import get_settings
    from jobagent.security import SecretStore
    import sqlite3
    connection = sqlite3.connect(path)
    copy = sqlite3.connect(tmp_path / 'real.sqlite3')
    connection.backup(copy)
    connection.close()
    copy.close()
    real_db = Database(tmp_path / 'real.sqlite3')
    llm = ApplicationAnswerLLM(real_db, SecretStore())
    probe = await llm.test(get_settings(real_db))
    assert probe['state'] == 'ANSWER_VALIDATED'
    assert probe['provider'] != 'extractive'
    assert probe['input_tokens'] > 0 and probe['output_tokens'] > 0


def test_llm_setup_api_secure_credentials_models_probe_and_settings(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from jobagent.main import create_app
    class MemorySecrets:
        def __init__(self):
            self.values = {}
        def get(self, name):
            return self.values.get(name)
        def set(self, name, value):
            self.values[name] = value
        def delete(self, name):
            self.values.pop(name, None)
    monkeypatch.setenv('MERIDIAN_TEST', '1')
    secrets = MemorySecrets()
    app = create_app(tmp_path, worker_isolation=False, token='test-only', secret_store=secrets, start_scheduler=False)
    def handler(request):
        if request.url.path == '/api/tags':
            return httpx.Response(200, json={'models': [{'name': 'installed-local-model'}]})
        if request.url.path == '/v1/models':
            return httpx.Response(200, json={'data': [{'id': 'discovered-model'}]})
        payload = request_payload(request)
        assert payload['verified_facts'] == [{'id': 'connection-probe', 'key': 'full_name', 'value': 'Sample Candidate'}]
        return response_for(request, complete(payload, answer='Sample Candidate', fid='connection-probe'))
    app.state.text_ai.answer_llm.transport = httpx.MockTransport(handler)
    with TestClient(app, headers={'Authorization': 'Bearer test-only'}) as client:
        status = client.get('/api/ai/answers/status').json()
        assert status['setup_required'] and status['local']['models'] == ['installed-local-model']
        assert client.patch('/api/settings', json={'text_temperature': 3}).status_code == 422
        assert client.patch('/api/settings', json={'text_retry_attempts': 6}).status_code == 422
        assert client.patch('/api/settings', json={'text_timeout_seconds': 0}).status_code == 422
        assert client.patch('/api/settings', json={'text_max_output_tokens': True}).status_code == 422
        assert client.put('/api/integrations/openai-compatible', json={'secret': 'private-key', 'config': {'input_cost_per_million': 1, 'output_cost_per_million': 2}}).status_code == 200
        assert client.patch('/api/settings', json={'text_provider': 'openai-compatible', 'text_model': 'discovered-model', 'text_base_url': 'https://compatible.example/v1', 'monthly_budget': 10}).status_code == 200
        status = client.get('/api/ai/answers/status').json()
        assert status['configured'] and status['key_present']
        assert 'private-key' not in json.dumps(status)
        assert client.get('/api/ai/answers/models').json()['items'] == [{'id': 'discovered-model', 'label': 'discovered-model'}]
        assert client.post('/api/ai/answers/test').json()['state'] == 'ANSWER_VALIDATED'
        assert secrets.values['openai-compatible:secret'] == 'private-key'
        assert 'private-key' not in str(app.state.db.query('SELECT * FROM settings'))
        assert 'private-key' not in str(app.state.db.query('SELECT * FROM integrations'))


def test_invented_incidents_rejected_even_with_plausible_cited_metrics():
    from jobagent.intelligence.answer_evidence import missing_historical_event
    story = {'id': 'nformar', 'key': 'story', 'category': 'experience', 'value':
             'At Nformar, I led a team of six web engineers and delivered the first professional website ahead of schedule. I built more than 80% of the frontend with Vue.js and Bootstrap, focusing on responsive web development.'}
    question = 'Describe a team conflict and how you resolved it.'
    assert 'verified specific conflict incident' in missing_historical_event(question, 'CONFLICT', [story])
    generated = complete({'question_id': 'q', 'intent': 'CONFLICT', 'verified_facts': [story]},
                         answer='At Nformar there was a disagreement over Vue.js and Bootstrap. I organised a meeting and resolved the conflict.', fid='nformar')
    issues = AnswerEvidenceValidator().validate(generated, [story], 'q', 'CONFLICT', {})
    assert any('unsupported historical detail disagreement' in x for x in issues)
    assert any('unsupported historical detail meeting' in x for x in issues)
    bug_source = {'id': 'rag', 'key': 'story', 'category': 'experience', 'value':
                  'I developed a chatbot prototype with RAG achieving 90% query accuracy and reducing support response time by 60%. I fine-tuned LLMs on a dataset of documents.'}
    assert 'specific bug incident' in missing_historical_event('Describe a difficult bug you resolved.', 'CHALLENGE', [bug_source])
    generated = complete({'question_id': 'q', 'intent': 'CHALLENGE', 'verified_facts': [bug_source]},
                         answer='I discovered a misconfigured indexing process and fixed the bug, achieving 90% query accuracy.', fid='rag')
    issues = AnswerEvidenceValidator().validate(generated, [bug_source], 'q', 'CHALLENGE', {})
    assert any('unsupported historical detail misconfiguration' in x for x in issues)
    assert any('unsupported historical detail indexing' in x for x in issues)


def test_unstated_sole_ownership_and_unused_claim_metadata_rejected():
    source = {'id': 'chess', 'key': 'story', 'category': 'project', 'value': 'I built a chess vision system using a CNN, Stockfish and AWS EC2.'}
    generated = complete({'question_id': 'q', 'intent': 'OWNERSHIP', 'verified_facts': [source]},
                         answer='The project was entirely my own initiative, and I took full ownership from concept to implementation.', fid='chess')
    issues = AnswerEvidenceValidator().validate(generated, [source], 'q', 'OWNERSHIP', {})
    assert any('entirely_alone' in x for x in issues)
    assert any('architecture_ownership' in x for x in issues)
    generated['answer'] = 'I want to work on cloud infrastructure.'
    generated['claims'] = [{'text': 'I built a chess vision system using a CNN, Stockfish and AWS EC2.', 'fact_ids': ['chess']}]
    issues = AnswerEvidenceValidator().validate(generated, [source], 'q', 'OWNERSHIP', {})
    assert any('not reflected in the actual answer' in x for x in issues)


@pytest.mark.parametrize('bad_claim', ['unknown_source', 'unsupported_content'])
async def test_company_claim_requires_actual_source_and_supported_content(db, evidence, bad_claim):
    def handler(request):
        payload = request_payload(request)
        generated = complete(payload, fid=evidence)
        generated['company_claims'] = [{'text': 'The company makes healthcare economic forecasting products.',
                                       'source_urls': ['https://unknown.example' if bad_claim == 'unknown_source' else 'https://company.example/job']}]
        return response_for(request, generated)
    llm = ApplicationAnswerLLM(db, None, transport=httpx.MockTransport(handler))
    expected = ProviderError if bad_claim == 'unknown_source' else AnswerValidationError
    message = 'company_claims' if bad_claim == 'unknown_source' else 'BAD_COMPANY_CONTEXT'
    with pytest.raises(expected, match=message):
        await llm.generate(settings(), 'Why work at the company?', {'company': 'Example', 'url': 'https://company.example/job',
                                                                  'description': 'Build cloud infrastructure and backend services.'})


async def test_product_specific_company_source_ranks_before_unrelated_sources(db, evidence):
    captured = []
    def handler(request):
        payload = request_payload(request)
        captured.append(payload)
        return response_for(request, complete(payload, fid=evidence))
    llm = ApplicationAnswerLLM(db, None, transport=httpx.MockTransport(handler))
    sources = [{'url': f'https://company.example/{name}', 'title': name, 'text': 'Shared navigation: Alpha Beta Gamma Delta Apollo.'}
               for name in ['Alpha', 'Beta', 'Gamma', 'Delta', 'Apollo']]
    await llm.generate(settings() | {'text_quality_review': False}, 'Why does Apollo interest you?', {'company_context': sources})
    assert captured[0]['company_context'][0]['title'] == 'Apollo'
    assert len(captured[0]['company_context']) == 4
