"""Regressions from observed Lever fields and omitted narrative claims."""
import pytest

from jobagent.automation.answers import option_for
from jobagent.intelligence.formatting import AnswerFormatResolver, choice
from jobagent.intelligence.answer_evidence import prose_claim_issues, AnswerEvidenceValidator
from jobagent.automation.reasoning import conditional_state
from jobagent.automation.adapters import get_adapter
from jobagent.automation.consent import dropdown_open_target
from jobagent.application_answer_llm import technical_evidence_index


def test_reference_link_does_not_turn_role_explanation_into_url():
    question = ('Which engineering role resonates with your job search and why? '
                'More details: https://employer.example/engineering-roles')
    field = {'type': 'textarea', 'tag': 'textarea', 'label': question}
    assert AnswerFormatResolver.infer(question, field) == 'LONG_TEXT'
    assert AnswerFormatResolver.validate('I prefer Software Engineer because I enjoy platform engineering.', question, field) is None
    assert AnswerFormatResolver.infer('GitHub URL', {'type': 'url'}) == 'URL'
    assert AnswerFormatResolver.validate('invalid link', 'GitHub URL', {'type': 'url'}) == 'ANSWER_FORMAT_ERROR'


@pytest.mark.parametrize('answer,label', [('Yes', 'Yes, I consent'), ('No', 'No, I do not consent')])
def test_exact_consent_polarity_labels(answer, label):
    options = [{'label': 'Yes, I consent', 'value': 'a'}, {'label': 'No, I do not consent', 'value': 'b'}]
    assert option_for(answer, options, question='Do you consent to AI interview transcription?')['label'] == label
    assert option_for(answer, options, question='Are you authorized to work?') is None
    expanded = [{'label': label + ' and future marketing', 'value': answer}]
    assert option_for(answer, expanded, question='Do you consent to AI interview transcription?') is None


@pytest.mark.parametrize('tail', [
    'I iterated on the detection steps, adjusting thresholds and augmenting the dataset.',
    'This required careful handling of lighting, piece overlap, and board orientation.',
    'This project taught me how to validate every stage before moving on.',
])
def test_omitted_historical_method_is_checked_in_actual_prose(tail):
    fact = {'id': 'p', 'entity': 'Chess Assistant AI', 'category': 'project',
            'value': 'OpenCV and NumPy read screen images and compare piece templates. Board reconstruction serializes detected squares into FEN.'}
    issues = prose_claim_issues('I built Chess Assistant AI. ' + tail, {'p': fact}, ['p'])
    assert any('undocumented historical method' in issue for issue in issues)


def test_supported_implementation_and_future_hypothetical_are_allowed():
    fact = {'id': 'p', 'entity': 'Chess Assistant AI', 'category': 'project',
            'value': 'OpenCV and NumPy read screen images and compare piece templates. Board reconstruction serializes detected squares into FEN.'}
    answer = ('I built Chess Assistant AI. I used OpenCV and NumPy to read screen images and compare piece templates. '
              'I would investigate lighting and orientation when designing a future vision system.')
    assert not prose_claim_issues(answer, {'p': fact}, ['p'])


def test_if_so_multiselect_siblings_share_observed_radio_parent():
    adapter = get_adapter('https://jobs.lever.co/example/job/apply')
    common = {'document_id': 'doc', 'form_id': 1}
    yes = dict(common, node_id=1, role='radio', group='deadline', label='Yes', checked=False,
               context='Do you anticipate any offer deadlines?')
    no = dict(common, node_id=2, role='radio', group='deadline', label='No', checked=True,
              context=yes['context'])
    first = dict(common, node_id=3, role='checkbox', group='dates', label='August', multiple=True,
                 context='If so, what are the dates?')
    second = dict(first, node_id=4, label='September')
    nodes = [yes, no, first, second]
    assert conditional_state(first, nodes, adapter)['applies'] is False
    assert conditional_state(second, nodes, adapter)['parent'] == yes['context']
    no['checked'] = False
    assert conditional_state(second, nodes, adapter)['applies'] is None


def test_editable_combobox_opens_before_answer_text_filters_options():
    control = {'role': 'combobox', 'operations': ['CLICK', 'TYPE_TEXT'], 'expanded': False}
    assert dropdown_open_target(control, [control]) is control
    assert dropdown_open_target(dict(control, expanded=True), [control]) is None


def test_confirmed_other_white_category_maps_to_explicit_ethnic_background():
    options = [{'label': 'White: British'}, {'label': 'White: Any other White ethnic background'}]
    assert choice('White - Other', options, disclosure_concept='ethnicity') == options[1]


def test_owned_implementation_clause_is_separate_from_reflection():
    fact = {'id': 'p', 'entity': 'Simulator', 'category': 'project',
            'value': 'Whole system: order books, matching engine, price engine, threading model and dashboard.'}
    assert not prose_claim_issues('In Simulator, I built the entire system myself, which forced me to understand each layer and how they connect.',
                                  {'p': fact}, ['p'])


def numeric_result(answer, employer_claim, candidate_claims=()):
    fact = {'id': 'p', 'category': 'project', 'entity': 'Simulator', 'key': 'story',
            'value': 'I built a Python Simulator.'}
    generated = {'question_id': 'q', 'intent': 'MOTIVATION', 'answer': answer, 'facts_used': ['p'],
                 'claims': list(candidate_claims), 'company_claims': [employer_claim], 'unsupported_claims': [],
                 'answers_question': True, 'follows_instructions': True}
    return AnswerEvidenceValidator().validate(generated, [fact], 'q', 'MOTIVATION', {},
        company_sources={'https://employer.example/job': 'Example Bank offers a 12-week internship in 2027.'},
        company_names=['Example Bank'])


def test_employer_programme_numbers_use_exact_employer_evidence():
    clause = 'Example Bank offers a 12-week internship in 2027'
    claim = {'text': clause, 'source_urls': ['https://employer.example/job']}
    assert not numeric_result(clause + '. I would enjoy contributing Python skills.', claim)
    assert any('number 2028' in x for x in numeric_result(clause.replace('2027', '2028') + '.',
        dict(claim, text=clause.replace('2027', '2028'))))
    assert any('number 12' in x for x in numeric_result(clause + '.', dict(claim, source_urls=[])))


@pytest.mark.parametrize('clause', [
    'I built 12 features at Example Bank in 2027',
    'My graduation year is 2027 at Example Bank',
    'Simulator processed 12 trades for Example Bank in 2027',
])
def test_employer_numeric_claim_cannot_license_candidate_history(clause):
    claim = {'text': clause, 'source_urls': ['https://employer.example/job']}
    assert any('number' in x for x in numeric_result(clause + '.', claim))


def test_subjective_enjoyment_does_not_license_unsupported_performed_action():
    claim = {'text': 'I enjoy business analytics and an empowering culture.', 'fact_ids': ['p']}
    assert not numeric_result(claim['text'], {'text': '', 'source_urls': []}, [claim])
    alignment = {'text': 'My interests in software engineering, artificial intelligence, and data engineering align with the kind of work Example Bank does.', 'fact_ids': ['p']}
    assert not numeric_result(alignment['text'], {'text': '', 'source_urls': []}, [alignment])
    history = {'text': 'I enjoy analytics because I built a secure deployment platform with compliance controls.', 'fact_ids': ['p']}
    assert any('UNSUPPORTED' in x for x in numeric_result(history['text'], {'text': '', 'source_urls': []}, [history]))


def test_large_profile_index_prioritizes_documented_tradeoffs_without_inventing_story():
    facts = []
    for name, design in [('Concurrent system', 'Independent components coordinate updates.'),
                         ('Algorithm visualizer', 'Greedy search trades optimality for heuristic search while BFS finds optimal paths.')]:
        for key, value in [('contribution', 'Sole author.'), ('public_architecture_tradeoffs', design),
                           ('public_implementation_details', 'Implemented search animation with pause and step control.')]:
            facts.append({'id': name + key, 'entity': name, 'category': 'project', 'key': key, 'value': value})
    index = technical_evidence_index(facts, 'CHALLENGE')
    assert index[0]['entity'] == 'Algorithm visualizer'
    assert {f['id'] for c in index for f in c['recorded_evidence']} == {f['id'] for f in facts}
    assert technical_evidence_index(facts, 'HYPOTHETICAL') == []
