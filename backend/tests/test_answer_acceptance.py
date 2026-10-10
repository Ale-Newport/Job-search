from jobagent.automation.runner import BrowserManager
from jobagent.automation.consent import action_key, section_plan
from jobagent.automation.adapters.base import ATSAdapter
from jobagent.automation.browser import application_input_elements, application_entry_target


def test_wrong_checked_radio_is_not_accepted(tmp_path):
    manager = BrowserManager(tmp_path)
    answer = {'question_id': 'q1', 'question': 'Are you eligible?', 'answer': 'Yes',
              'status': 'ANSWER_VALIDATED'}
    manager._answer_diagnostics = {'q1': dict(answer)}
    manager._accepted_answer(answer, {'role': 'radio', 'label': 'No', 'value': 'No', 'checked': True})
    assert manager._answer_diagnostics['q1']['status'] == 'ANSWER_VALIDATED'
    manager._accepted_answer(answer, {'role': 'radio', 'label': 'Yes', 'value': 'Yes', 'checked': True})
    assert manager._answer_diagnostics['q1']['status'] == 'ANSWER_ACCEPTED_BY_FORM'


def test_explicit_blank_requires_observed_empty_value(tmp_path):
    manager = BrowserManager(tmp_path)
    answer = {'question_id': 'q2', 'question': 'Current company', 'answer': None,
              'leave_blank': True, 'status': 'ANSWER_VALIDATED'}
    manager._answer_diagnostics = {'q2': dict(answer)}
    manager._accepted_answer(answer, {'role': 'textbox', 'value': 'Former Employer'})
    assert manager._answer_diagnostics['q2']['status'] == 'ANSWER_VALIDATED'
    manager._accepted_answer(answer, {'role': 'textbox', 'value': ''})
    assert manager._answer_diagnostics['q2']['status'] == 'ANSWER_ACCEPTED_BY_FORM'


def test_website_reset_revokes_previously_observed_acceptance(tmp_path):
    manager = BrowserManager(tmp_path)
    answer = {'question_id': 'q3', 'question': 'Preferred name', 'answer': 'Alejandro',
              'status': 'ANSWER_VALIDATED'}
    manager._answer_diagnostics = {'q3': dict(answer)}
    manager._accepted_answer(answer, {'role': 'textbox', 'value': 'Alejandro'})
    assert manager._answer_diagnostics['q3']['status'] == 'ANSWER_ACCEPTED_BY_FORM'
    manager._accepted_answer(answer, {'role': 'textbox', 'value': ''})
    assert manager._answer_diagnostics['q3']['status'] == 'ANSWER_INSERTED'


def test_reselection_revokes_group_acceptance(tmp_path):
    manager = BrowserManager(tmp_path)
    answer = {'question_id': 'q4', 'question': 'Languages', 'answer': 'English, Spanish',
              'form_value': ['English', 'Spanish'], 'status': 'ANSWER_ACCEPTED_BY_FORM'}
    manager._answer_diagnostics = {'q4': dict(answer)}
    snapshot = {'url': 'https://example.test/application', 'elements': [
        {'label': 'Languages', 'tag': 'select', 'multiple': True, 'selected_values': ['en'],
         'options': [{'label': 'English', 'value': 'en'}, {'label': 'Spanish', 'value': 'es'}]}
    ]}
    manager._reconcile_diagnostics(snapshot)
    assert manager._answer_diagnostics['q4']['status'] == 'ANSWER_INSERTED'


def test_role_question_mentioning_job_search_is_an_applicant_field():
    field = {'label': 'Which of these roles resonates the most with your job search and why?',
             'tag': 'textarea', 'type': 'textarea', 'operations': ['TYPE_TEXT']}
    assert application_input_elements({'elements': [field]}) == [field]


def test_review_operations_use_exact_generated_answer_and_multiselection():
    fields = [
        {'index': 1, 'document_id': 'doc', 'node_id': 1, 'label': 'Why this role?',
         'role': 'textbox', 'type': 'textarea', 'value': '', 'operations': ['TYPE_TEXT']},
        {'index': 2, 'document_id': 'doc', 'node_id': 2, 'label': 'Languages',
         'role': 'combobox', 'tag': 'select', 'type': 'select-multiple', 'multiple': True,
         'value': '', 'selected_values': [], 'operations': ['SELECT'],
         'options': [{'label': 'English', 'value': 'en'}, {'label': 'Spanish', 'value': 'es'}]},
        {'index': 3, 'document_id': 'doc', 'node_id': 3, 'label': 'Current company',
         'role': 'textbox', 'type': 'text', 'value': 'Former Employer', 'operations': ['TYPE_TEXT', 'CLEAR']},
    ]
    answers = {
        'Why this role?': {'answer': 'A genuine reviewed answer.', 'leave_blank': False,
                           'fact_ids': ['evidence'], 'kind': 'motivation'},
        'Languages': {'answer': 'English, Spanish', 'form_value': ['English', 'Spanish'],
                      'leave_blank': False, 'fact_ids': ['languages'], 'kind': 'language'},
        'Current company': {'answer': None, 'leave_blank': True, 'confidence_level': 'EXACT',
                            'fact_ids': ['explicit-blank'], 'root_cause': None},
    }
    preview, keys = section_plan({'elements': fields}, fields[0], ATSAdapter(), [], [],
        {'_answer_resolutions': answers}, lambda *_: [])
    assert preview[0]['answer'] == 'A genuine reviewed answer.'
    assert action_key(fields[1], 'SELECT', ['en', 'es']) in set(keys)
    assert action_key(fields[2], 'CLEAR') in set(keys)
    assert preview[2]['answer'] == ''


def test_landing_search_and_chat_are_not_an_application_form():
    links = [{'tag': 'a', 'role': 'link', 'label': 'Apply', 'form_id': 'page',
              'href': target, 'operations': ['OPEN_TAB']} for target in
             ['https://example.test/about-applying', 'https://example.test/application',
              'https://example.test/application']]
    fields = [{'label': 'Job search input', 'type': 'text', 'operations': ['TYPE_TEXT']},
              {'label': 'Your message…', 'type': 'textarea', 'operations': ['TYPE_TEXT']}]
    snapshot = {'elements': links + fields, 'frames': [{'job_context': {'title': 'Engineer'},
                'text': 'Job Description: build services.'}]}
    assert application_input_elements(snapshot) == []
    assert application_entry_target(snapshot)['href'] == 'https://example.test/application'
    # A real applicant control prevents treating the form as entry navigation.
    snapshot['elements'].append({'label': 'First name', 'operations': ['TYPE_TEXT']})
    assert application_entry_target(snapshot) is None
