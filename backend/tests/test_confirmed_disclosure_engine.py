import pytest

from jobagent.db import Database
from jobagent.intelligence.store import entity, ingest_fact, policy, update_fact
from jobagent.intelligence.engine import CandidateEngine
from jobagent.intelligence.formatting import choice
from jobagent.automation.disclosures import disclosure_memory_key, disclosure_profile_key


@pytest.fixture
def explicit_profile(tmp_path):
    db = Database(tmp_path / 'candidate.sqlite3')
    person = entity(db, 'person', 'Synthetic candidate')
    fields = dict(pronouns='they/them/theirs', gender='Non-binary', sexual_orientation='Prefer not to say',
                  ethnicity='Prefer not to say', religion='Atheist', disability='No', neurodivergence='Yes',
                  mental_health='No')
    ids = {key: ingest_fact(db, person, key, value, source='Synthetic explicit confirmation',
                           evidence={'kind':'user_confirmation'}, verified=True, sensitivity='personal')
           for key, value in fields.items()}
    policy(db, 'personal_disclosures', 'use_confirmed')
    policy(db, 'optional_demographics', 'decline_or_blank')
    policy(db, 'adjustments_request', 'none_requested')
    return db, ids


@pytest.mark.parametrize('question,key', [
    ('What pronouns do you use?', 'pronouns'), ('Your preferred pronouns', 'pronouns'), ('Pronouns', 'pronouns'),
    ('Describe your gender', 'gender'), ('Gender identity', 'gender'), ('What is your gender?', 'gender'),
    ('Your sexual orientation', 'sexual_orientation'), ('What is your sexuality?', 'sexual_orientation'),
    ('Your ethnic background', 'ethnicity'), ('What is your ethnicity?', 'ethnicity'),
    ('What is your religion or belief?', 'religion'), ('Your faith', 'religion'),
    ('Do you have a disability?', 'disability'), ('Disability', 'disability'),
    ('Are you neurodivergent?', 'neurodivergence'), ('Do you consider yourself neurodivergent?', 'neurodivergence'),
    ('Do you have a mental health condition?', 'mental_health'), ('Your mental health', 'mental_health'),
])
def test_uses_only_confirmed_specific_attribute(explicit_profile, question, key):
    db, ids = explicit_profile
    result = CandidateEngine(db).answer_question(question)
    assert result['disclosure_concept'] == key
    assert result['fact_ids'] == [ids[key]]
    assert result['answer'] is not None and not result['leave_blank']
    assert result['confidence_level'] == 'EXACT'


def test_health_concepts_cannot_substitute_for_each_other(explicit_profile):
    db, _ = explicit_profile
    engine = CandidateEngine(db)
    assert engine.answer_question('Do you have a disability?')['answer'] == 'No'
    assert engine.answer_question('Are you neurodivergent?')['answer'] == 'Yes'
    assert engine.answer_question('Do you have a mental health condition?')['answer'] == 'No'
    for question in ['Do you need reasonable adjustments?', 'What is your sex assigned at birth?', 'Are you transgender?',
                     'Are you cisgender?', 'Are you Hispanic or Latino?', 'Do you have a medical condition or disability?']:
        result = engine.answer_question(question)
        assert result['answer'] is None, (question, result)
        assert result['root_cause'] == 'MISSING_CONFIRMED_DISCLOSURE'


def test_weak_label_uses_specific_description(explicit_profile):
    db, ids = explicit_profile
    result = CandidateEngine(db).answer_question('Please select', {'description':'What are your preferred pronouns?'})
    assert result['answer'] == 'they/them/theirs'
    assert result['fact_ids'] == [ids['pronouns']]


def test_explicit_choice_does_not_invent_a_nearby_option(explicit_profile):
    db, _ = explicit_profile
    result = CandidateEngine(db).answer_question('Gender', {'options':[{'label':'Male','value':'m'},{'label':'Female','value':'f'}]})
    assert result['answer'] is None
    assert result['root_cause'] == 'BAD_ENUM_MAPPING'
    assert result['requires_review']


def test_cleared_fact_cannot_revive_old_saved_answer(explicit_profile):
    db, ids = explicit_profile
    update_fact(db, ids['pronouns'], '', False)
    engine = CandidateEngine(db)
    question = 'What are your preferred pronouns?'
    result = engine.answer_question(question)
    old = [{'id':'stale', 'key':question, 'value':'he/him/his', 'verification_status':'verified', 'profile_explicit':True}]
    assert engine.confirmed_fallback(question, result, old, {})['answer'] is None


def test_missing_value_never_uses_a_previous_decline_policy(explicit_profile):
    db, _ = explicit_profile
    engine = CandidateEngine(db)
    result = engine.answer_question('What is your sex assigned at birth?')
    resolved = engine.confirmed_fallback(result['question'], result, [], {'gender':{'action':'prefer_not_to_say'}})
    assert resolved['answer'] is None


def test_blank_mode_still_overrides_confirmed_values(explicit_profile):
    db, _ = explicit_profile
    policy(db, 'personal_disclosures', 'leave_blank')
    result = CandidateEngine(db).answer_question('Preferred pronouns')
    assert result['leave_blank'] and result['answer'] is None


def test_current_disability_answer_does_not_invent_lifetime_history(explicit_profile):
    db, _ = explicit_profile
    result = CandidateEngine(db).answer_question('Do you have, or have you ever had, a disability?', {
        'tag': 'select', 'options': [{'label': 'Yes', 'value': 'yes'}, {'label': 'No', 'value': 'no'}]})
    assert result['answer'] is None
    assert result['root_cause'] == 'MISSING_CONFIRMED_DISCLOSURE'
    assert result['requires_confirmation']


@pytest.mark.parametrize('question,value,label', [
    ('Preferred pronouns','He','he/him/his'), ('Preferred pronouns','he/him','he/him/his'),
    ('Preferred pronouns','She','she/her/hers'), ('Preferred pronouns','they/them','they/them/theirs'),
    ('Gender','Man','Male'), ('Gender','Woman','Female'),
    ('Sexual orientation','Heterosexual','Straight / Heterosexual'),
    ('Ethnic background','White - Other','Any other White background'),
    ('Religion','Atheist','No religion or belief'),
    ('Religion','Atheist','No religion or Atheist'),
])
def test_question_scoped_option_normalization(question, value, label):
    assert choice(value,[{'label':label,'value':'chosen'}],question=question)['label'] == label


@pytest.mark.parametrize('question,value,label', [
    ('Gender','He','Male'), ('Preferred pronouns','Man','he/him/his'),
    ('Ethnic background','White - Other','White British'), ('Religion','No religion','Atheist'),
    ('Disability','No','No disability now or in the past'),
])
def test_no_cross_attribute_or_stronger_claim_mapping(question, value, label):
    assert choice(value,[{'label':label,'value':'different'}],question=question) is None


def test_conditional_detail_is_not_the_parent_attribute():
    assert disclosure_profile_key('If yes, please provide details', {'_disclosure_parent_question':'Do you have a disability?'}) is None


@pytest.mark.parametrize('question', [
    'Do you have a physical or mental health condition?',
    'Do you have a mental or physical health condition?',
    'Do you have a long-term physical or mental health condition?',
    'Do you have any health conditions including mental health conditions?',
    'Do you have a disability or any long-term illness?',
    'Do you have a disability or chronic condition?',
    'Do you have a disability or any impairment?',
    'What gender were you assigned at birth?',
    'What was your birth gender?',
    'Have you ever had a mental health condition?',
    'Have you had a disability in the past?',
    'Which religion were you raised in?',
    'What were your childhood religious beliefs?',
    'What are your previous pronouns?',
    'What pronouns did you use growing up?',
])
def test_current_attribute_cannot_answer_broader_or_historical_question(explicit_profile, question):
    from jobagent.application_profile import question_field

    db, _ = explicit_profile
    assert disclosure_profile_key(question) is None
    assert question_field(question, {'company': 'Example'}) is None
    result = CandidateEngine(db).answer_question(question)
    assert result['answer'] is None
    assert result['root_cause'] == 'MISSING_CONFIRMED_DISCLOSURE'


@pytest.mark.parametrize('label,description', [
    ('Mental health', 'Do you have a physical or mental health condition?'),
    ('Disability', 'Have you ever had a disability?'),
    ('Pronouns', 'What are your previous pronouns?'),
    ('Gender', 'What gender were you assigned at birth?'),
    ('Religion', 'Which religion were you raised in?'),
])
def test_question_hint_limits_the_scope_of_an_otherwise_supported_attribute(explicit_profile, label, description):
    from jobagent.application_profile import question_field

    db, _ = explicit_profile
    field = {'description': description}
    assert disclosure_profile_key(label, field) is None
    assert question_field(label, {'company': 'Example'}, field) is None
    result = CandidateEngine(db).answer_question(label, field)
    assert result['answer'] is None
    assert result['root_cause'] == 'MISSING_CONFIRMED_DISCLOSURE'


def test_birth_facts_keep_their_own_explicit_concepts():
    assert disclosure_profile_key('What is your date of birth?') == 'date_of_birth'
    assert disclosure_profile_key('What sex were you assigned at birth?') == 'sex'


def test_generic_personal_memories_are_bound_to_the_observed_question_meaning(tmp_path):
    from jobagent.application_profile import application_knowledge, remember_answer
    from jobagent.core import prepare_application
    from jobagent.discovery import ingest_job

    db = Database(tmp_path / 'context-memory.sqlite3')
    entity(db, 'person', 'Synthetic candidate')
    policy(db, 'personal_disclosures', 'use_confirmed')
    job = ingest_job(db, {'title': 'Engineer', 'company': 'Example', 'location': 'London',
                         'url': 'https://example.test/role'})[0]
    app = {**prepare_application(db, job['id']), 'company': 'Example', 'location': 'London'}
    question = 'Please specify'
    pronouns = {'description': 'Which pronouns do you use?', 'section_label': 'Diversity survey'}
    religion = {'description': 'What is your religion or belief?', 'section_label': 'Diversity survey'}
    remember_answer(db, app, question, 'they/them/theirs', element=pronouns)
    facts, _ = application_knowledge(db, app, db.query("SELECT * FROM facts WHERE verification_status='verified'"))
    engine = CandidateEngine(db)
    answer = engine.answer_question(question, pronouns)
    resumed = engine.confirmed_fallback(question, answer, facts, {})
    assert resumed['answer'] == 'they/them/theirs'
    assert resumed['question'] == question
    unrelated = engine.answer_question(question, religion)
    assert engine.confirmed_fallback(question, unrelated, facts, {})['answer'] is None
    legacy = [{'id': 'old-generic', 'key': question, 'value': 'An unrelated old answer',
               'verification_status': 'verified', 'profile_explicit': True}]
    assert engine.confirmed_fallback(question, answer, legacy, {})['answer'] is None
    assert disclosure_memory_key('Preferred pronouns') == 'preferred pronouns'


def test_saved_conditional_detail_never_becomes_a_general_attribute():
    from jobagent.application_profile import question_field

    context = {'_disclosure_parent_question': 'Do you have a disability?'}
    key = disclosure_memory_key('If yes, please provide details', context)
    assert disclosure_profile_key(key) is None
    assert question_field(key, {'company': 'Example'}) is None
