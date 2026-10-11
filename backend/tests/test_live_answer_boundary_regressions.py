"""Regressions from observed Lever fields and omitted narrative claims."""
import pytest

from jobagent.automation.answers import option_for
from jobagent.intelligence.formatting import AnswerFormatResolver, choice
from jobagent.intelligence.answer_evidence import prose_claim_issues
from jobagent.automation.reasoning import conditional_state
from jobagent.automation.adapters import get_adapter
from jobagent.automation.consent import dropdown_open_target


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
