"""Provider contracts, independent review, privacy and per-question isolation."""
import asyncio
import json

import httpx
import pytest

from jobagent.application_answer_llm import (ANSWER_SCHEMA, SCORE_NAMES, ApplicationAnswerLLM,
                                            AnswerValidationError, ConfigurationError, ProviderError,
                                            professional_previous_answers)
from jobagent.db import Database, now, uid
from jobagent.intelligence.answer_evidence import (AnswerEvidenceValidator, ApplicationConsistencyReviewer,
                                                 CandidateFactRetriever, content_words, reconcile_claim_evidence, requirements)
from jobagent.intelligence.store import entity, ingest_fact
from jobagent.intelligence.professional_context import contains_private_candidate_data, professional_resume_text


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
                                        'usage': {'input_tokens': 15, 'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0}, 'output_tokens': 30}})
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


def model_prices(input_rate=1, output_rate=2, model='discovered-model'):
    return {'answer_model_prices': {model: {'input_cost_per_million': input_rate, 'output_cost_per_million': output_rate,
                                            'cached_input_cost_per_million': input_rate / 20,
                                            'cache_write_cost_per_million': input_rate * 1.25}}}


@pytest.mark.parametrize('provider', ['openai', 'anthropic', 'gemini', 'ollama', 'openai-compatible'])
async def test_native_provider_contracts_exact_question_and_independent_review(db, evidence, provider):
    calls = []
    db.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',
               (uid(), provider, json.dumps(model_prices()), 'configured'))
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


async def test_prior_form_history_is_private_in_generation_regeneration_and_review(db, evidence):
    safe = {'question_id': 'professional', 'intent': 'ROLE_PREFERENCE', 'state': 'ANSWER_VALIDATED',
            'quality': {'passed': True}, 'answer': 'I prefer reusable platform engineering.',
            'selected_roles': ['Software Engineer'], 'private_metadata': 'never-forward-metadata'}
    prior = [safe, {'intent': 'CONTACT', 'answer': 'private-address@example.test'},
             {'intent': 'DEMOGRAPHIC', 'answer': 'private-demographic-value'},
             {'intent': 'PROJECT', 'state': 'ANSWER_VALIDATED', 'quality': {'passed': True},
              'answer': 'Contact me at injected@example.test'},
             {'intent': 'PROJECT', 'state': 'ANSWER_VALIDATED', 'quality': {'passed': True},
              'kind': 'gender', 'answer': 'protected-wrongly-tagged'}, 'legacy-private-value']
    protected = {'intent': 'PROJECT', 'state': 'ANSWER_VALIDATED', 'quality': {'passed': True}}
    prior.extend([{**protected, 'sensitivity': 'personal', 'answer': 'personal-marker'},
                  {**protected, 'canonical_intent': 'DEMOGRAPHIC', 'answer': 'conflicting-intent-marker'},
                  {**protected, 'canonical_intents': ['PROJECT', 'CONSENT'], 'answer': 'composite-consent-marker'},
                  {**protected, 'answer': 'I am a woman and my religion is Catholic.'},
                  {**protected, 'answer': 'I am a Spanish citizen.'},
                  {**protected, 'answer': 'I am on a Student visa.'},
                  {**protected, 'answer': 'I have ADHD.'},
                  {**protected, 'answer': 'My phone is +44 7700 900123.'},
                  {**protected, 'answer': 'api_key=private-credential-marker'}])
    prior.extend([{**protected, 'answer': 'metadata-private-row', 'facts_used': [{'email': 'hidden@example.test'}]},
                  {**protected, 'answer': 'metadata-private-row', 'evidence_entities': [{'credential': 'never-share-marker'}]},
                  {**protected, 'answer': 'metadata-private-row', 'facts_used': ['password:secret-reference-marker']}])
    product = {'question_id': 'product', 'canonical_intent': 'PRODUCT_PREFERENCE', 'state': 'ANSWER_VALIDATED',
               'quality': {'passed': True}, 'answer': 'Foundry', 'selected_options': [
                   {'label': 'Foundry', 'value': 'foundry', 'metadata': 'private-selection-metadata'}]}
    prior.append(product)
    calls = []
    def handler(request):
        payload = request_payload(request)
        calls.append(payload)
        if 'generated_answer' in payload:
            generated = {'question_id': payload['question_id'], 'passed': True,
                         'scores': {name: .95 for name in SCORE_NAMES}, 'unsupported_claims': [], 'issues': [], 'reason': 'Grounded.'}
        elif len(calls) == 1:
            generated = complete(payload, answer='I built a chess system for 9000 users.', fid=evidence)
        else:
            generated = complete(payload, fid=evidence)
        return response_for(request, generated)
    result = await ApplicationAnswerLLM(db, Keys(), transport=httpx.MockTransport(handler)).generate(
        settings(), 'Explain a project you built.', {}, question_id='private-history', previous_answers=prior)
    assert result['state'] == 'ANSWER_VALIDATED'
    assert len(calls) == 3
    for payload in calls:
        assert payload['previous_answers'] == [{k: v for k, v in safe.items() if k in
             {'question_id', 'intent', 'answer', 'selected_roles'}},
             {'question_id': 'product', 'intent': 'PRODUCT_PREFERENCE', 'answer': 'Foundry', 'selected_options': ['Foundry']}]
        serialized = json.dumps(payload)
        assert all(value not in serialized for value in ('private-address', 'private-demographic-value',
                   'injected@example', 'protected-wrongly-tagged', 'legacy-private-value', 'never-forward-metadata',
                   'personal-marker', 'conflicting-intent-marker', 'composite-consent-marker', 'Catholic',
                   'Spanish citizen', 'Student visa', 'ADHD', '7700', 'private-credential-marker', 'private-selection-metadata',
                   'metadata-private-row', 'hidden@example', 'never-share-marker', 'secret-reference-marker'))


@pytest.mark.parametrize('text', ['I am a Spanish citizen.', 'I am a citizen of Spain.',
    'I am on a Student visa.', 'I have ADHD.', 'Religion: Catholic', 'My phone is 07123456789.',
    'My gender is female.', 'I consent to sharing my information.', 'password=credential-marker',
    'My phone +44 7700 900123. I process records.', '+44 7700 900123 records', 'Mobile: 07123456789 users'])
def test_mixed_candidate_disclosures_are_not_professional_context(text):
    assert contains_private_candidate_data(text)
    assert not professional_previous_answers([{'intent': 'PROJECT', 'state': 'ANSWER_VALIDATED',
        'quality': {'passed': True}, 'answer': 'I built accessible software. ' + text}])


def test_professional_cv_keeps_companies_inclusive_work_and_dates():
    body = ('Education\nCatholic University, 2020-09-01 - 2024-06-30\n'
            'Experience\nJohn Lewis\nBuilt accessible tools for users with disabilities.\n'
            'Projects\nhttps://github.com/example/accessible-tools\nProcessed 1000000000 events.\n')
    private = ('John Lewis\nAddress: 12 Example Road\nDate of birth: 2000-03-20\n'
               'Gender: male\nReligion: Catholic\nWork authorization: private-authorization\n'
               'private-header@example.test\n+44 7700 900123\nhttps://linkedin.com/in/private-profile\n')
    assert professional_resume_text(private + body, ['John Lewis']) == body.strip()


def test_previous_professional_opaque_references_are_not_phone_numbers():
    prior = {'intent': 'PROJECT', 'state': 'ANSWER_VALIDATED', 'quality': {'passed': True},
             'answer': 'I built inclusive software tools.', 'facts_used': ['12345678-1234-1234-1234-123456789012'],
             'evidence_entities': ['12345678-1234-1234-1234-123456789012', 'Accessible tools']}
    assert professional_previous_answers([prior])[0]['facts_used'] == prior['facts_used']


async def test_mixed_fact_and_cv_disclosures_never_reach_any_model_payload(db, evidence):
    person = entity(db, 'person', 'Alex Example')
    ingest_fact(db, person, 'full_name', 'Alex Example', source='user', evidence={}, verified=True, sensitivity='personal')
    leaked = ingest_fact(db, person, 'professional_interests', 'Backend engineering. My religion is Catholic.',
                        source='user', evidence={}, verified=True)
    credential = ingest_fact(db, person, 'general_context', 'Software engineering. api_key=secret-source-marker',
                            source='user', evidence={}, verified=True)
    candidate = db.one('SELECT id FROM candidates LIMIT 1')['id']
    legacy = uid()
    db.execute('INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
               (legacy, candidate, 'experience', 'story', 'Built accessible tools for users with disabilities at Catholic University.',
                'https://github.com/example/accessible-tools', 'verified', 0, '', '2020-09-01', '2026-10-11T14:30:00Z'))
    unverified = uid()
    db.execute('INSERT INTO facts VALUES(?,?,?,?,?,?,?,?,?,?,?)',
               (unverified, candidate, 'project', 'story', 'Unverified locked private-context-marker',
                'user', 'unverified', 1, '', now(), now()))
    cv = ('Alex Example\nAddress: 12 Example Road\nDate of birth: 2000-03-20\nReligion: Catholic\n'
          'Work authorization: private-authorization\nprivate-cv@example.test\n+44 7700 900123\n'
          'Education\nSoftware degree at Catholic University, 2020-09-01 - 2024-06-30\n'
          'Experience\nBuilt accessible tools for users with disabilities.\n'
          'Projects\nhttps://github.com/example/accessible-tools')
    db.execute('INSERT INTO documents(id,name,kind,created_at,updated_at) VALUES(?,?,?,?,?)', ('cv', 'CV', 'cv', now(), now()))
    db.execute('INSERT INTO document_versions(id,document_id,version,path,filename,mime_type,content_hash,text,approved,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
               ('cv-version', 'cv', 1, '/synthetic/cv.pdf', 'cv.pdf', 'application/pdf', 'synthetic-hash', cv, 1, now()))
    calls = []
    def handler(request):
        payload = request_payload(request)
        calls.append(payload)
        if 'generated_answer' in payload:
            generated = {'question_id': payload['question_id'], 'passed': True,
                         'scores': {name: .95 for name in SCORE_NAMES}, 'unsupported_claims': [], 'issues': [], 'reason': 'Grounded.'}
        else:
            generated = complete(payload, answer='I built a chess system for 9000 users.' if len(calls) == 1 else None, fid=evidence)
        return response_for(request, generated)
    result = await ApplicationAnswerLLM(db, None, transport=httpx.MockTransport(handler)).generate(
        settings(), 'Tell us one thing not on your resume that you are proud of.',
        {'resume_version_id': 'cv-version'}, question_id='professional-only')
    assert result['state'] == 'ANSWER_VALIDATED' and len(calls) == 3
    for payload in calls:
        assert {leaked, credential, unverified}.isdisjoint({fact['id'] for fact in payload['verified_facts']})
        assert legacy in {fact['id'] for fact in payload['verified_facts']}
        assert 'Catholic University' in payload['uploaded_resume']['text']
        assert '2020-09-01 - 2024-06-30' in payload['uploaded_resume']['text']
        assert 'users with disabilities' in payload['uploaded_resume']['text']
        serialized = json.dumps(payload)
        assert all(value not in serialized for value in ('Alex Example', '12 Example Road', '2000-03-20',
                   'Religion:', 'private-authorization', 'private-cv@example', '7700', 'secret-source-marker', 'private-context-marker'))


@pytest.mark.parametrize('full_profile', [False, True])
async def test_complete_professional_context_is_forwarded_to_generation_and_review_without_contact_data(db, evidence, full_profile):
    professional_ids = set()
    for index in range(50):
        area = entity(db, 'skill', f'Specialist area {index}')
        professional_ids.add(ingest_fact(db, area, 'name', f'Compiler engineering topic {index}. ' + 'Parser and compiler implementation details. ' * 40,
                                       source='user', evidence={}, verified=True))
    person = entity(db, 'person', 'Alex Example')
    ingest_fact(db, person, 'full_name', 'Alex Example', source='user', evidence={}, verified=True, sensitivity='personal')
    private_id = ingest_fact(db, person, 'email', 'private-cv@example.test', source='user', evidence={}, verified=True)
    cv = 'Alex Example\nprivate-cv@example.test\n+44 7700 900123\nExperience\nBuilt a chess computer vision system using Python and Stockfish.'
    db.execute('INSERT INTO documents(id,name,kind,created_at,updated_at) VALUES(?,?,?,?,?)', ('cv', 'CV', 'cv', now(), now()))
    db.execute('INSERT INTO document_versions(id,document_id,version,path,filename,mime_type,content_hash,text,approved,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)',
               ('cv-version', 'cv', 1, '/synthetic/cv.pdf', 'cv.pdf', 'application/pdf', 'synthetic-hash', cv, 1, now()))
    calls = []
    def handler(request):
        payload = request_payload(request)
        calls.append(payload)
        if 'generated_answer' in payload:
            generated = {'question_id': payload['question_id'], 'passed': True,
                         'scores': {name: .95 for name in SCORE_NAMES}, 'unsupported_claims': [], 'issues': [], 'reason': 'Grounded.'}
        else:
            generated = complete(payload, fid=evidence)
        return response_for(request, generated)
    result = await ApplicationAnswerLLM(db, None, transport=httpx.MockTransport(handler)).generate(
        settings() | {'text_full_professional_context': full_profile}, 'Describe a project you built.',
        {'resume_version_id': 'cv-version'}, question_id='full-professional-context')
    assert result['state'] == 'ANSWER_VALIDATED' and len(calls) == 2
    for payload in calls:
        sent = {fact['id'] for fact in payload['verified_facts']}
        assert private_id not in sent
        assert payload['context_mode'] == ('complete_professional' if full_profile else 'question_retrieval')
        if full_profile:
            assert professional_ids <= sent and len(sent) > 32
            assert len(json.dumps(payload, ensure_ascii=False).encode()) > 65000
            assert payload['uploaded_resume']['id'] == 'cv-version'
            assert 'Stockfish' in payload['uploaded_resume']['text']
        else:
            assert len(sent) <= 32 and payload['uploaded_resume'] is None
        serialized = json.dumps(payload)
        assert all(value not in serialized for value in ('Alex Example', 'private-cv@example', '7700'))


@pytest.mark.parametrize('full_profile, padding_size, permitted', [(False, 70000, False), (True, 70000, True), (True, 1048576, False)])
async def test_full_profile_transport_limit_retains_bounded_preflight_before_credentials(db, evidence, full_profile, padding_size, permitted):
    db.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',
               (uid(), 'openai-compatible', json.dumps(model_prices()), 'configured'))
    calls = []
    class CountingKeys(Keys):
        def get(self, name):
            calls.append('credential')
            return super().get(name)
    def handler(request):
        calls.append('transport')
        return response_for(request, complete(request_payload(request), fid=evidence))
    payload = {'question_id': 'full-context-boundary', 'intent': 'PROJECT', 'padding': 'x' * padding_size,
               'verified_facts': [{'id': evidence, 'value': 'I built a chess computer vision system using Python and Stockfish.'}]}
    llm = ApplicationAnswerLLM(db, CountingKeys(), transport=httpx.MockTransport(handler))
    configured = settings('openai-compatible') | {'text_full_professional_context': full_profile}
    if permitted:
        generated, _usage = await llm._request(configured, 'Return a supported project answer.', payload, ANSWER_SCHEMA)
        assert generated['answer'] and calls == ['credential', 'transport']
    else:
        with pytest.raises(ConfigurationError, match='Question context exceeds'):
            await llm._request(configured, 'Return a supported project answer.', payload, ANSWER_SCHEMA)
        assert calls == []


async def test_no_provider_never_returns_generic_narrative(db, evidence):
    with pytest.raises(ConfigurationError, match='An LLM is required'):
        await ApplicationAnswerLLM(db, None).generate({'text_provider': 'none'}, 'Tell us about a project', {})


async def test_missing_key_and_insecure_endpoint_fail_explicitly(db, evidence):
    llm = ApplicationAnswerLLM(db, None)
    db.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',
               (uid(), 'openai', json.dumps(model_prices()), 'configured'))
    with pytest.raises(ConfigurationError, match='Keychain'):
        await llm._request(settings('openai'), 'system', {}, ANSWER_SCHEMA)
    with pytest.raises(ConfigurationError, match='HTTPS'):
        await llm.models(settings('openai-compatible') | {'text_base_url': 'http://remote.example/v1'})
    with pytest.raises(ConfigurationError, match='credentials'):
        await llm.models(settings('openai-compatible') | {'text_base_url': 'https://user:key@remote.example/v1'})


@pytest.mark.parametrize('prices', [None, {'input_cost_per_million': 0, 'output_cost_per_million': 0},
                                  {'input_cost_per_million': 1, 'output_cost_per_million': 2}])
async def test_zero_remote_cap_blocks_before_credentials_or_transport(db, prices):
    class ForbiddenKeys:
        def get(self, _name):
            pytest.fail('A zero cloud cap must be rejected before Keychain access')
    if prices is not None:
        db.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',
                   (uid(), 'openai', json.dumps({'answer_model_prices': {'discovered-model': prices}}), 'configured'))
    def handler(_request):
        pytest.fail('No remote request is authorized by a zero spending cap')
    llm = ApplicationAnswerLLM(db, ForbiddenKeys(), transport=httpx.MockTransport(handler))
    with pytest.raises(ConfigurationError, match='positive monthly AI spending cap'):
        await llm._request(settings('openai') | {'monthly_budget': 0}, 'system', {}, ANSWER_SCHEMA)


@pytest.mark.parametrize('prices,message', [(None, 'current input and output token prices'),
    ({'input_cost_per_million': 0, 'output_cost_per_million': 0}, 'cannot both be zero')])
async def test_paid_cloud_requires_configured_nonzero_rates_before_credentials(db, prices, message):
    class ForbiddenKeys:
        def get(self, _name):
            pytest.fail('Pricing setup must be completed before reading credentials')
    if prices is not None:
        db.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',
                   (uid(), 'openai', json.dumps({'answer_model_prices': {'discovered-model': prices}}), 'configured'))
    llm = ApplicationAnswerLLM(db, ForbiddenKeys())
    with pytest.raises(ConfigurationError, match=message):
        await llm._request(settings('openai'), 'system', {}, ANSWER_SCHEMA)


@pytest.mark.parametrize('budget,prices,message,ready', [
    (0, {'input_cost_per_million': 1, 'output_cost_per_million': 2}, 'positive monthly AI spending cap', False),
    (5, None, 'current input and output token prices', False),
    (5, {'input_cost_per_million': 0, 'output_cost_per_million': 0}, 'cannot both be zero', False),
    (5, model_prices()['answer_model_prices']['discovered-model'], 'configured', True),
])
async def test_cloud_status_requires_billing_setup_but_preserves_key_presence(db, budget, prices, message, ready):
    if prices is not None:
        db.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',
                   (uid(), 'openai', json.dumps({'answer_model_prices': {'discovered-model': prices}}), 'configured'))
    llm = ApplicationAnswerLLM(db, Keys(), transport=httpx.MockTransport(lambda _request: httpx.Response(200, json={'models': []})))
    status = await llm.status(settings('openai') | {'monthly_budget': budget})
    assert status['key_present'] is True
    assert status['billing_ready'] is ready
    assert status['configured'] is ready
    assert status['setup_required'] is not ready
    assert message in status['message']


async def test_unbilled_model_discovery_remains_available_with_zero_cap(db):
    llm = ApplicationAnswerLLM(db, Keys(), transport=httpx.MockTransport(
        lambda _request: httpx.Response(200, json={'data': [{'id': 'discovered-model'}]})))
    discovered = await llm.models(settings('openai') | {'monthly_budget': 0})
    assert discovered['items'] == [{'id': 'discovered-model', 'label': 'discovered-model'}]
    assert not db.one("SELECT value FROM settings WHERE key LIKE 'ai_usage:%'")


@pytest.mark.parametrize('config', [
    {'input_cost_per_million': 1, 'output_cost_per_million': 2},
    model_prices(model='different-model'),
])
async def test_answer_cloud_prices_must_match_exact_model_without_legacy_fallback(db, config):
    db.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',
               (uid(), 'openai', json.dumps(config), 'configured'))
    class ForbiddenKeys:
        def get(self, _name):
            pytest.fail('Unbound model rates must block before credentials are read')
    llm = ApplicationAnswerLLM(db, ForbiddenKeys())
    with pytest.raises(ConfigurationError, match='exact selected cloud answer model'):
        await llm._request(settings('openai'), 'system', {}, ANSWER_SCHEMA)


async def test_unpriced_cloud_fallback_is_blocked_and_uses_its_own_rates_when_bound(db):
    config = model_prices()
    db.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',
               (uid(), 'openai', json.dumps(config), 'configured'))
    configured = settings('openai') | {'text_fallback_model': 'stronger-model'}
    llm = ApplicationAnswerLLM(db, None)
    with pytest.raises(ConfigurationError, match='exact fallback cloud answer model'):
        await llm._request(configured, 'system', {}, ANSWER_SCHEMA)
    config['answer_model_prices']['stronger-model'] = model_prices(10, 20)['answer_model_prices']['discovered-model']
    db.execute("UPDATE integrations SET config=? WHERE provider='openai'", (json.dumps(config),))
    primary_prices, _ = llm._billing(configured, 'openai', False)
    fallback_prices, _ = llm._billing(configured | {'text_model': 'stronger-model'}, 'openai', False)
    assert primary_prices == config['answer_model_prices']['discovered-model']
    assert fallback_prices == config['answer_model_prices']['stronger-model']


@pytest.mark.parametrize('key', ['cached_input_cost_per_million', 'cache_write_cost_per_million'])
@pytest.mark.parametrize('rate', [None, True, -1, '2.5', float('nan'), float('inf')])
async def test_openai_cache_rates_require_exact_valid_model_configuration(db, key, rate):
    config = model_prices()
    config['answer_model_prices']['discovered-model'][key] = rate
    db.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',
               (uid(), 'openai', json.dumps(config), 'configured'))
    class ForbiddenKeys:
        def get(self, _name):
            pytest.fail('Cache pricing must be configured before Keychain access')
    with pytest.raises(ConfigurationError, match='cached input and cache write token prices'):
        await ApplicationAnswerLLM(db, ForbiddenKeys())._request(settings('openai'), 'system', {}, ANSWER_SCHEMA)
    status = await ApplicationAnswerLLM(db, Keys()).status(settings('openai'))
    assert status['key_present'] and status['setup_required'] and not status['billing_ready']
    assert 'cached input and cache write' in status['message']


async def test_openai_fallback_needs_its_own_cache_rates(db):
    config = model_prices()
    config['answer_model_prices']['stronger-model'] = {'input_cost_per_million': 10, 'output_cost_per_million': 20}
    db.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',
               (uid(), 'openai', json.dumps(config), 'configured'))
    with pytest.raises(ConfigurationError, match='exact fallback OpenAI answer model'):
        await ApplicationAnswerLLM(db, None)._request(settings('openai') | {'text_fallback_model': 'stronger-model'},
                                                     'system', {}, ANSWER_SCHEMA)


def usage_ledger(db):
    return json.loads(db.one("SELECT value FROM settings WHERE key LIKE 'ai_usage:%'")['value'])


@pytest.mark.parametrize('details,expected_input_cost', [
    ({'cached_tokens': 0, 'cache_write_tokens': 12000}, .030),
    ({'cached_tokens': 4000, 'cache_write_tokens': 3000}, .0179),
    ({'cached_tokens': 12000, 'cache_write_tokens': 0}, .0012),
])
async def test_openai_cold_and_mixed_cache_costs_settle_once_on_shared_ledger(tmp_path, details, expected_input_cost):
    live, clone = Database(tmp_path / 'live.sqlite3'), Database(tmp_path / 'copy.sqlite3')
    live.execute("INSERT INTO settings(key,value) VALUES('monthly_budget','10')")
    clone.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',
                  (uid(), 'openai', json.dumps(model_prices(2, 10, model='gpt-6.1-sol')), 'configured'))
    calls = []
    def handler(request):
        calls.append(request)
        body = json.loads(response_for(request, complete(request_payload(request), fid='fact')).content)
        body['usage'] = {'input_tokens': 12000, 'input_tokens_details': details, 'output_tokens': 100}
        return httpx.Response(200, json=body)
    llm = ApplicationAnswerLLM(clone, Keys(), transport=httpx.MockTransport(handler), usage_db=live)
    payload = {'question_id': 'q', 'intent': 'PROJECT', 'verified_facts': [{'id': 'fact'}], 'source': 'x' * 14000}
    _, usage = await llm._request(settings('openai') | {'text_model': 'gpt-6.1-sol'}, 'system', payload, ANSWER_SCHEMA)
    ledger = usage_ledger(live)
    assert len(calls) == ledger['calls'] == 1
    assert ledger['cost'] == pytest.approx(expected_input_cost + .001)
    assert usage['cost'] == pytest.approx(expected_input_cost + .001)
    assert ledger['input_tokens'] == 12000 and ledger['output_tokens'] == 100
    assert not clone.one("SELECT value FROM settings WHERE key LIKE 'ai_usage:%'")


@pytest.mark.parametrize('usage', [
    {}, {'input_tokens': 12000, 'output_tokens': 100},
    {'input_tokens': 12000, 'input_tokens_details': {'cached_tokens': 0}, 'output_tokens': 100},
    {'input_tokens': 12000, 'input_tokens_details': {'cache_write_tokens': 12000}, 'output_tokens': 100},
    {'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0}, 'output_tokens': 100},
])
async def test_openai_missing_usage_keeps_full_reservation(db, usage):
    db.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',
               (uid(), 'openai', json.dumps(model_prices(2, 10)), 'configured'))
    reserved = []
    def handler(request):
        reserved.append(usage_ledger(db)['cost'])
        body = json.loads(response_for(request, complete(request_payload(request), fid='fact')).content)
        body['usage'] = usage
        return httpx.Response(200, json=body)
    payload = {'question_id': 'q', 'intent': 'PROJECT', 'verified_facts': [{'id': 'fact'}], 'source': 'x' * 14000}
    _, accounting = await ApplicationAnswerLLM(db, Keys(), transport=httpx.MockTransport(handler))._request(
        settings('openai'), 'system', payload, ANSWER_SCHEMA)
    assert accounting['cost'] == usage_ledger(db)['cost'] == reserved[0]
    assert accounting['cost'] > .031
    assert usage_ledger(db)['calls'] == 1


@pytest.mark.parametrize('details', [None, [], 'invalid', {'cached_tokens': -1, 'cache_write_tokens': 0},
    {'cached_tokens': False, 'cache_write_tokens': 0}, {'cached_tokens': 0, 'cache_write_tokens': '12000'},
    {'cached_tokens': 1, 'cache_write_tokens': 12000}, {'cached_tokens': 0, 'cache_write_tokens': -1}])
async def test_openai_invalid_cache_counts_reject_and_keep_reservation_without_retry(db, details):
    db.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',
               (uid(), 'openai', json.dumps(model_prices(2, 10)), 'configured'))
    reserved = []
    def handler(request):
        reserved.append(usage_ledger(db)['cost'])
        body = json.loads(response_for(request, complete(request_payload(request), fid='fact')).content)
        body['usage'] = {'input_tokens': 12000, 'input_tokens_details': details, 'output_tokens': 100}
        return httpx.Response(200, json=body)
    payload = {'question_id': 'q', 'intent': 'PROJECT', 'verified_facts': [{'id': 'fact'}], 'source': 'x' * 14000}
    with pytest.raises(ProviderError, match='invalid cache token accounting'):
        await ApplicationAnswerLLM(db, Keys(), transport=httpx.MockTransport(handler))._request(
            settings('openai') | {'text_retry_attempts': 2}, 'system', payload, ANSWER_SCHEMA)
    assert usage_ledger(db)['cost'] == reserved[0]
    assert len(reserved) == usage_ledger(db)['calls'] == 1


async def test_openai_cache_premium_reservation_blocks_budget_boundary_before_dispatch(db):
    db.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',
               (uid(), 'openai', json.dumps(model_prices(2, 10)), 'configured'))
    def handler(_request):
        pytest.fail('The cache write premium must be reserved before dispatch')
    # A 16KB request costs under $0.04 at the ordinary-input rate but over
    # $0.04 at the configured write rate, even before the output allowance.
    payload = {'question_id': 'q', 'intent': 'PROJECT', 'verified_facts': [{'id': 'fact'}], 'source': 'x' * 16000}
    with pytest.raises(ValueError, match='budget would be exceeded'):
        await ApplicationAnswerLLM(db, Keys(), transport=httpx.MockTransport(handler))._request(
            settings('openai') | {'monthly_budget': .04, 'text_max_output_tokens': 1}, 'system', payload, ANSWER_SCHEMA)
    assert not db.one("SELECT value FROM settings WHERE key LIKE 'ai_usage:%'")


@pytest.mark.parametrize('provider,base', [('ollama', 'http://127.0.0.1:11434/v1'),
                                         ('openai-compatible', 'http://127.0.0.1:8123/v1')])
async def test_explicit_loopback_inference_is_free_with_zero_cap(db, evidence, provider, base):
    def handler(request):
        return response_for(request, complete(request_payload(request), fid=evidence))
    llm = ApplicationAnswerLLM(db, None, transport=httpx.MockTransport(handler))
    answer = await llm.generate(settings(provider) | {'text_base_url': base, 'monthly_budget': 0, 'text_quality_review': False},
                                'Describe your project.', {}, question_id='local')
    assert answer['state'] == 'ANSWER_VALIDATED' and answer['cost'] == 0


async def test_qa_profile_copies_share_live_usage_and_cannot_reset_cap(tmp_path):
    live = Database(tmp_path / 'live.sqlite3')
    live.execute('INSERT INTO settings(key,value) VALUES(?,?)', ('monthly_budget', '0.3'))
    clones = [Database(tmp_path / f'qa-{index}.sqlite3') for index in range(2)]
    for clone in clones:
        clone.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',
                      (uid(), 'openai', json.dumps(model_prices(0, 1000)), 'configured'))
    calls = []
    def handler(request):
        payload = request_payload(request)
        calls.append(payload)
        reply = response_for(request, complete(payload, fid='fact'))
        body = json.loads(reply.content)
        body['usage'] = {'input_tokens': 15, 'input_tokens_details': {'cached_tokens': 0, 'cache_write_tokens': 0}, 'output_tokens': 200}
        return httpx.Response(200, json=body)
    configured = settings('openai') | {'monthly_budget': .3, 'text_max_output_tokens': 256}
    payload = {'question_id': 'q', 'intent': 'PROJECT', 'verified_facts': [{'id': 'fact'}]}
    services = [ApplicationAnswerLLM(clone, Keys(), transport=httpx.MockTransport(handler), usage_db=live) for clone in clones]
    await services[0]._request(configured, 'system', payload, ANSWER_SCHEMA)
    with pytest.raises(ValueError, match='budget would be exceeded'):
        await services[1]._request(configured, 'system', payload, ANSWER_SCHEMA)
    assert len(calls) == 1
    ledger = json.loads(live.one("SELECT value FROM settings WHERE key LIKE 'ai_usage:%'")['value'])
    assert ledger['cost'] == pytest.approx(.2)
    assert ledger['calls'] == 1
    assert all(not clone.one("SELECT value FROM settings WHERE key LIKE 'ai_usage:%'") for clone in clones)
    live.execute("UPDATE settings SET value='0' WHERE key='monthly_budget'")
    with pytest.raises(ConfigurationError, match='shared live monthly AI spending cap is zero'):
        await services[1]._request(configured, 'system', payload, ANSWER_SCHEMA)


def test_calibration_copies_current_nonsecret_configuration_but_shares_ledger(tmp_path):
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path
    spec = spec_from_file_location('calibrate_answer_llm', Path(__file__).parents[2] / 'scripts/calibrate_answer_llm.py')
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    live = Database(tmp_path / 'live.sqlite3')
    clone = Database(tmp_path / 'candidate-copy.sqlite3')
    for key, value in {'text_provider': 'openai', 'text_model': 'selected-cloud-model', 'monthly_budget': 5}.items():
        live.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (key, json.dumps(value)))
    live.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',
                 (uid(), 'openai', json.dumps({'input_cost_per_million': 1, 'output_cost_per_million': 2,
                                               **model_prices(model='selected-cloud-model'),
                                               'account_label': 'irrelevant-config'}), 'configured'))
    live.execute("INSERT INTO settings(key,value) VALUES('ai_usage:2099-01','{\"cost\":4}')")
    configured = module.configure_from_live(clone, live)
    assert configured['text_provider'] == 'openai' and configured['text_model'] == 'selected-cloud-model'
    assert configured['monthly_budget'] == 5
    assert json.loads(clone.one("SELECT config FROM integrations WHERE provider='openai'")['config']) == {
        'input_cost_per_million': 1, 'output_cost_per_million': 2,
        **model_prices(model='selected-cloud-model')}
    assert not clone.one("SELECT value FROM settings WHERE key LIKE 'ai_usage:%'")


def test_calibration_preserves_other_providers_two_rate_configuration(tmp_path):
    from importlib.util import module_from_spec, spec_from_file_location
    from pathlib import Path
    spec = spec_from_file_location('calibrate_answer_llm', Path(__file__).parents[2] / 'scripts/calibrate_answer_llm.py')
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    live, clone = Database(tmp_path / 'live.sqlite3'), Database(tmp_path / 'copy.sqlite3')
    for key, value in {'text_provider': 'anthropic', 'text_model': 'selected-model', 'monthly_budget': 5}.items():
        live.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (key, json.dumps(value)))
    bound = {'answer_model_prices': {'selected-model': {'input_cost_per_million': 3, 'output_cost_per_million': 15}}}
    live.execute('INSERT INTO integrations(id,provider,config,status) VALUES(?,?,?,?)',
                 (uid(), 'anthropic', json.dumps(bound), 'configured'))
    configured = module.configure_from_live(clone, live)
    assert json.loads(clone.one("SELECT config FROM integrations WHERE provider='anthropic'")['config']) == bound
    assert ApplicationAnswerLLM(clone, None)._billing(configured, 'anthropic', False) == (
        {'input_cost_per_million': 3, 'output_cost_per_million': 15}, 5)


@pytest.mark.parametrize('script', ['calibrate_answer_llm', 'regress_application_answers'])
async def test_remote_qa_scripts_require_shared_ledger_before_any_external_work(tmp_path, monkeypatch, script):
    from importlib import import_module
    from pathlib import Path
    from types import SimpleNamespace
    scripts = Path(__file__).parents[2] / 'scripts'
    monkeypatch.syspath_prepend(str(scripts))
    module = import_module(script)
    source = Database(tmp_path / 'profile-copy.sqlite3')
    source.execute('INSERT INTO settings(key,value) VALUES(?,?)', ('text_provider', '"openai"'))
    args = SimpleNamespace(database=source.path, output=tmp_path / 'report', allow_remote=True, usage_database=None,
                           job=None, url='https://example.test/application', company='Example', title='Engineer',
                           location='London', fill=True, headless=True)
    with pytest.raises(ValueError, match='shared live database'):
        await module.run(args)


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


def test_same_entity_implementation_citations_are_reconciled_without_new_prose():
    facts = [
        {'id': 'architecture', 'key': 'public_architecture_tradeoffs', 'entity': 'Three Body Problem', 'category': 'project',
         'value': 'Numerical stability is observable through energy and momentum diagnostics.'},
        {'id': 'implementation', 'key': 'public_implementation_details', 'entity': 'Three Body Problem', 'category': 'project',
         'value': 'Interactive gravitational simulation in JavaScript. Uses a velocity-Verlet integrator and gravitational softening.'},
        {'id': 'another-project', 'key': 'story', 'entity': 'Another Project', 'category': 'project',
         'value': 'I built Cassandra and Chromium integrations with 99.6% accuracy.'},
    ]
    answer = 'I implemented a velocity-Verlet integrator and gravitational softening in an interactive JavaScript gravitational simulation.'
    generated = complete({'question_id': 'q', 'intent': 'CHALLENGE', 'verified_facts': facts}, answer=answer, fid='architecture')
    assert reconcile_claim_evidence(generated, facts) == ['implementation']
    assert generated['answer'] == answer
    assert generated['facts_used'] == ['architecture', 'implementation']
    assert generated['claims'][0]['fact_ids'] == ['architecture', 'implementation']
    assert not AnswerEvidenceValidator().validate(generated, facts, 'q', 'CHALLENGE', {})
    generated['answer'] += ' I integrated Cassandra and achieved 99.6% accuracy.'
    generated['claims'][0]['text'] = generated['answer']
    assert reconcile_claim_evidence(generated, facts) == []
    assert 'another-project' not in generated['facts_used']
    assert any('number 99.6%' in issue for issue in AnswerEvidenceValidator().validate(generated, facts, 'q', 'CHALLENGE', {}))


def test_telegraphic_interest_claims_are_subjective_but_named_metadata_is_checked():
    fact = {'id': 'interests', 'key': 'professional_interests', 'category': 'person', 'value': 'AI, machine learning and backend engineering.'}
    answer = 'I would enjoy developing useful AI applications and backend systems that support real-world work.'
    generated = complete({'question_id': 'q', 'intent': 'HYPOTHETICAL', 'verified_facts': [fact]}, answer=answer, fid='interests')
    generated['claims'] = [{'text': 'Excited about developing useful AI applications and backend systems.', 'fact_ids': ['interests']}]
    assert not AnswerEvidenceValidator().validate(generated, [fact], 'q', 'HYPOTHETICAL', {})
    generated['claims'][0]['text'] = 'Passionate about software tools such as Cassandra or Chromium.'
    assert any('named detail' in issue for issue in AnswerEvidenceValidator().validate(generated, [fact], 'q', 'HYPOTHETICAL', {}))


async def test_invented_named_employer_is_rejected_even_in_hypothetical_answer(db, evidence):
    def handler(request):
        payload = request_payload(request)
        answer = 'I would be excited about DataSage. DataSage focuses on healthcare analytics and develops predictive models for hospitals.'
        generated = complete(payload, answer=answer, fid=evidence)
        generated['claims'] = []
        return response_for(request, generated)
    llm = ApplicationAnswerLLM(db, None, transport=httpx.MockTransport(handler))
    with pytest.raises(AnswerValidationError, match='factual employer assertions'):
        await llm.generate(settings() | {'text_quality_review': False}, "If Palantir didn't exist, what kind of work would excite you?",
                           {'company': 'Palantir'}, question_id='hypothetical')


async def test_length_regeneration_uses_actual_count_and_keeps_schema_metadata_scoped(db, evidence):
    calls = []
    def handler(request):
        payload = request_payload(request)
        calls.append(payload)
        return response_for(request, complete(payload, fid=evidence))
    llm = ApplicationAnswerLLM(db, None, transport=httpx.MockTransport(handler))
    with pytest.raises(AnswerValidationError, match='min_words'):
        await llm.generate(settings() | {'text_quality_review': False}, 'Describe your project. (Approx. 200 words)', {}, question_id='long')
    assert len(calls) == 3
    assert 'prior draft contains 11 words' in calls[1]['length_revision']
    assert calls[1]['previous_draft']['answer'] == calls[0]['verified_facts'][0]['value']


async def test_length_regeneration_shortens_overlong_prose_using_observed_count(db, evidence):
    calls = []
    def handler(request):
        payload = request_payload(request)
        calls.append(payload)
        sentence = next(f['value'] for f in payload['verified_facts'] if f['id'] == evidence)
        generated = complete(payload, answer=' '.join([sentence] * (22 if len(calls) == 1 else 18)), fid=evidence)
        generated['word_count'] = 200  # Self-reported length must not override prose.
        return response_for(request, generated)
    llm = ApplicationAnswerLLM(db, None, transport=httpx.MockTransport(handler))
    answer = await llm.generate(settings() | {'text_quality_review': False}, 'Describe your project. (Approx. 200 words)', {}, question_id='long')
    assert len(calls) == 2 and answer['word_count'] == 198
    assert '242 whitespace-separated words' in calls[1]['length_revision']
    assert 'Shorten' in calls[1]['length_revision'] and 'do not expand or truncate' in calls[1]['length_revision']
    assert calls[1]['question_id'] == 'long' and calls[1]['output_requirements']['max_words'] == 220


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
        assert client.put('/api/integrations/openai-compatible', json={'secret': 'private-key', 'config': model_prices()}).status_code == 200
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
