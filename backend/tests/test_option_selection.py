import pytest

from jobagent.automation.option_selection import OptionSelector
from jobagent.intelligence.formatting import choice


class LocalFixture:
    def __init__(self):
        self.calls = []

    async def choose_option(self, question, answer, options, fact_ids):
        self.calls.append((question, answer, options, fact_ids))
        return {'option': options[0], 'engine': 'laya', 'confidence': .99}


@pytest.mark.parametrize('question,answer,expected,wrong', [
    ('Current location', 'London, UK', 'Greater London, England, UK', 'London, Ontario, Canada'),
    ('Your religion or belief', 'Atheist', 'No religion or belief', 'Christian'),
    ('Your ethnicity', 'White - Other', 'Any other white ethnic background', 'White - British'),
    ('Do you have a disability?', 'No', 'No', 'Yes'),
    ('Do you have a disability?', 'No', 'No, I do not have a disability', 'Yes, I have a disability'),
    ('Preferred pronouns', 'he/him', 'he/him/his', 'she/her/hers'),
])
async def test_only_supported_observed_options_reach_local_laya(question, answer, expected, wrong):
    engine = LocalFixture()
    selector = OptionSelector({}, engine)
    resolution = {'question': question, 'answer': answer, 'fact_ids': ['confirmed'], 'verified': True}
    options = [{'label': wrong, 'value': 'wrong'}, {'label': expected, 'value': 'right'}]
    decision = await selector.select(resolution, options)
    assert decision['option'] == options[1]
    assert not engine.calls or engine.calls[0][2] == [options[1]]
    assert await selector.select(resolution, options) == decision
    assert len(engine.calls) <= 1


@pytest.mark.parametrize('question,answer,label', [
    ('Your religion', 'No religion', 'Atheist'),
    ('Your ethnicity', 'White', 'White - British'),
    ('Do you have a disability?', 'No', 'No disability and no need for adjustments'),
    ('Do you have a disability?', 'No', 'No, I do not have a disability and have not had one in the past'),
    ('Current location', 'London, UK', 'Greater London, Ontario, Canada'),
])
async def test_unknown_identity_or_extra_assertions_are_never_chosen(question, answer, label):
    engine = LocalFixture()
    selector = OptionSelector({}, engine)
    result = await selector.select({'question': question, 'answer': answer, 'verified': True,
        'fact_ids': ['confirmed']}, [{'label': label, 'value': answer}])
    assert result is None
    assert engine.calls == []


def test_hidden_personal_option_code_cannot_override_its_label():
    assert choice('Atheist', [{'label': 'Christian', 'value': 'Atheist'}], question='Your religion') is None


def test_religion_equivalence_does_not_infer_a_specific_belief():
    assert choice('Atheist', [{'label': 'No religious affiliation', 'value': 'none'}], question='Religion')
    assert choice('No religion', [{'label': 'No religious affiliation', 'value': 'none'}], question='Religion')
    assert choice('No religion', [{'label': 'Atheist', 'value': 'atheist'}], question='Religion') is None


async def test_local_laya_never_receives_missing_personal_facts():
    engine = LocalFixture()
    result = await OptionSelector({}, engine).select({'question': 'Your religion', 'answer': None,
        'verified': False, 'fact_ids': []}, [{'label': 'Atheist', 'value': 'atheist'}])
    assert result is None and engine.calls == []


@pytest.mark.parametrize('selected,installed,running,starts', [
    ('laya', True, False, 1), ('hybrid', True, False, 1),
    ('laya', True, True, 0), ('laya', False, False, 0),
    ('jev', True, False, 0), ('deterministic', True, False, 0),
])
async def test_explicit_preparation_starts_only_an_installed_selected_local_engine(selected, installed, running, starts):
    from jobagent.automation.local_runtime import prepare_local_choices
    class Runtime:
        calls = 0
        async def status(self):
            return {'installed': installed, 'running': running}
        async def start(self):
            self.calls += 1
    runtime = Runtime()
    await prepare_local_choices(runtime, {'browser_engine': selected, 'laya_autostart': False})
    assert runtime.calls == starts


def test_choice_question_identity_groups_options_but_keeps_distinct_forms_separate():
    from jobagent.automation.answers import question_identity
    field = {'role': 'checkbox', 'multiple': True, 'group': 'pronouns', 'form_id': 'form-a',
             'document_id': 'document', 'section_label': 'Application', 'name': 'he-option'}
    qid = question_identity('application', field, 'Your pronouns')
    assert qid == question_identity('application', {**field, 'name': 'she-option'}, 'Your pronouns')
    assert qid != question_identity('application', {**field, 'form_id': 'form-b'}, 'Your pronouns')
    assert qid != question_identity('application', {**field, 'group': 'separate-question'}, 'Your pronouns')
