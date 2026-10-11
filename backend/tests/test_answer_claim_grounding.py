"""Full-prose grounding checks, independent of generator claim metadata."""
import pytest

from jobagent.intelligence.answer_evidence import AnswerEvidenceValidator


SAVED_UNSUPPORTED_ANSWER = """The hardest technical challenge I've faced was building the Stock Market Simulator, where I had to coordinate concurrent trader threads with a price-time priority matching engine. The core difficulty was ensuring that independently scheduled price ticks and concurrent trader strategies could place orders on independent order books without corrupting shared state or violating matching invariants.

I designed the system so that each trader thread places orders on its own order book, while the matching engine uses price-time priority for limit orders. A ScheduledExecutorService drives price ticks, and pluggable strategies run on trader threads. This separation meant I had to reason carefully about thread safety and ordering, especially when a price tick and a trader order arrived nearly simultaneously.

The hardest part was debugging race conditions that only appeared under specific interleavings. I had to trace through the Java threading model and ensure that the matching engine processed orders atomically with respect to price updates. I also built a JavaFX dashboard to display prices, trades, and trader profit and loss, which helped me observe the system's behavior in real time.

This project taught me to think rigorously about concurrency and to design components with clear ownership boundaries. It also reinforced the value of observable state for diagnosing subtle timing issues, a lesson I carry into infrastructure work."""


@pytest.fixture
def market_facts():
    common = {'entity_id': 'market-project', 'entity': 'Stock Market Simulator', 'category': 'project'}
    return [
        common | {'id': 'architecture', 'key': 'public_architecture_tradeoffs', 'value':
                  'The project separates market, engine, order, trader and UI components; independently scheduled price ticks and concurrent trader strategies feed order matching and live dashboard updates.'},
        common | {'id': 'ownership', 'key': 'contribution', 'value':
                  'Whole system: order books, matching engine, price engine, threading model and dashboard.'},
        common | {'id': 'implementation', 'key': 'public_implementation_details', 'value': [
            'Java trader threads place orders on independent order books.',
            'Matching engine uses price-time priority for limit orders.',
            'ScheduledExecutorService drives price ticks and pluggable strategies run on trader threads.',
            'JavaFX dashboard displays prices, trades and trader profit and loss.',
        ]},
    ]


def check(answer, facts, claims=(), intent='CHALLENGE'):
    generated = {'question_id': 'q', 'intent': intent, 'answer': answer,
                 'facts_used': [fact['id'] for fact in facts], 'claims': list(claims),
                 'unsupported_claims': [], 'answers_question': True, 'follows_instructions': True}
    return AnswerEvidenceValidator().validate(generated, facts, 'q', intent, {})


def test_real_saved_answer_rejects_incident_and_guarantees_omitted_from_claim_metadata(market_facts):
    claims = [{'text': 'I built Stock Market Simulator with concurrent trader threads and a price-time priority matching engine.',
               'fact_ids': ['architecture', 'ownership', 'implementation']}]
    issues = check(SAVED_UNSUPPORTED_ANSWER, market_facts, claims)
    assert any('undocumented debugging incident' in issue for issue in issues)
    assert any('implementation guarantee atomicity' in issue for issue in issues)
    assert any('implementation guarantee corruption_prevention' in issue for issue in issues)
    assert any('per-actor resource ownership' in issue for issue in issues)


def test_truthful_architecture_description_does_not_imply_a_debugging_incident(market_facts):
    answer = ('I built the Stock Market Simulator, separating market, engine, order, trader and UI components. '
              'Java trader threads place orders on independent order books, and the matching engine uses price-time priority. '
              'ScheduledExecutorService drives price ticks while pluggable strategies run on trader threads. '
              'I built the JavaFX dashboard to display prices, trades and trader profit and loss. '
              'This architecture makes concurrency and component boundaries an interesting design challenge.')
    assert check(answer, market_facts) == []


def test_verified_incident_and_actions_support_full_prose_without_claim_metadata(market_facts):
    incident = market_facts[0] | {'id': 'incident', 'key': 'challenge', 'value':
        'In Stock Market Simulator, I debugged race conditions that occurred under specific interleavings. '
        'I traced the Java threading model, reproduced the failure and fixed it with a single critical section '
        'around matching orders and price updates, making these operations atomic.'}
    answer = ('In Stock Market Simulator, I debugged race conditions that occurred under specific interleavings. '
              'I traced the Java threading model and fixed the problem. '
              'The matching engine processed orders atomically with respect to price updates.')
    assert check(answer, [*market_facts, incident]) == []


def test_incident_for_another_project_does_not_license_missing_market_history(market_facts):
    unrelated = {'id': 'other-incident', 'entity_id': 'other-project', 'entity': 'Other Project', 'category': 'project',
                 'key': 'challenge', 'value': 'I debugged race conditions that occurred under specific interleavings and fixed the Java threading model.'}
    issues = check('I built Stock Market Simulator. I debugged race conditions that occurred under specific interleavings.',
                   [*market_facts, unrelated])
    assert any('undocumented debugging incident' in issue for issue in issues)


def test_generic_or_future_debugging_evidence_does_not_establish_completed_incident(market_facts):
    generic = market_facts[0] | {'id': 'generic', 'value':
        'If a race condition occurred, I would debug it by tracing the threading model. Debugging is important for concurrent systems.'}
    issues = check('In Stock Market Simulator, I debugged race conditions.', [*market_facts, generic])
    assert any('undocumented debugging incident' in issue for issue in issues)


def test_hypothetical_future_debugging_and_atomic_design_are_allowed(market_facts):
    answer = ('I would enjoy building concurrent systems like Stock Market Simulator. '
              'If a race condition appeared, I would debug the bug and trace the Java threading model. '
              'I would design an atomic matching operation and ensure the implementation was thread-safe.')
    assert check(answer, market_facts, intent='HYPOTHETICAL') == []


def test_conditional_clause_does_not_hide_completed_past_incident(market_facts):
    issues = check('In Stock Market Simulator, I debugged a race condition that occurred if two trader threads placed orders together.', market_facts)
    assert any('undocumented debugging incident' in issue for issue in issues)


def test_subjective_future_interest_does_not_hide_a_past_implementation_guarantee(market_facts):
    answer = 'I would enjoy infrastructure work because I implemented an atomic matching engine in Stock Market Simulator.'
    assert any('implementation guarantee atomicity' in issue for issue in check(answer, market_facts, intent='HYPOTHETICAL'))


def test_documented_debugging_does_not_establish_an_unrecorded_reproduction_action(market_facts):
    incident = market_facts[0] | {'id': 'incident', 'value':
        'In Stock Market Simulator, I debugged a race condition that appeared when traders submitted orders.'}
    answer = 'In Stock Market Simulator, I reproduced the race condition with a custom stress test.'
    assert any('undocumented personal reproduction action' in issue for issue in check(answer, [*market_facts, incident]))


def test_documented_incident_does_not_establish_an_unrecorded_interleaving_failure(market_facts):
    incident = market_facts[0] | {'id': 'incident', 'value':
        'In Stock Market Simulator, I debugged a race condition that appeared when traders submitted orders.'}
    answer = 'In Stock Market Simulator, I debugged race conditions that appeared under specific interleavings.'
    assert any('undocumented incident detail interleaving_failure' in issue for issue in check(answer, [*market_facts, incident]))


@pytest.mark.parametrize('assertion, expected', [
    ('The engine processed orders atomically with respect to price updates.', 'atomicity'),
    ('The matching engine was thread-safe.', 'thread_safety'),
    ('The matching engine was linearizable.', 'linearizability'),
    ('The order matching was race-free.', 'race_free'),
])
def test_undocumented_guarantees_fail_even_when_no_claims_are_reported(market_facts, assertion, expected):
    issues = check('I built Stock Market Simulator. ' + assertion, market_facts)
    assert any('implementation guarantee ' + expected in issue for issue in issues)


def test_same_critical_section_supports_atomicity_but_unrelated_locks_do_not(market_facts):
    answer = 'In Stock Market Simulator, the engine processed orders atomically with respect to price updates.'
    supported = market_facts[0] | {'id': 'synchronization', 'value':
        'A single lock guards matching orders and price updates in the same critical section.'}
    assert check(answer, [*market_facts, supported]) == []
    unsupported = supported | {'value': 'Locks are used for matching orders. A different lock is used for price updates.'}
    assert any('implementation guarantee atomicity' in issue for issue in check(answer, [*market_facts, unsupported]))


def test_documented_per_thread_resource_ownership_is_allowed(market_facts):
    answer = 'In Stock Market Simulator, each trader thread places orders on its own order book.'
    assert any('per-actor resource ownership' in issue for issue in check(answer, market_facts))
    explicit = market_facts[0] | {'id': 'per-thread', 'value': 'Each trader thread places orders on its own order book.'}
    assert check(answer, [*market_facts, explicit]) == []


def test_outcome_metrics_do_not_establish_an_unrecorded_causal_method():
    fact = {'id': 'leadership', 'entity': 'Nformar', 'category': 'experience', 'key': 'story',
            'value': 'At Nformar, I led six engineers and launched the website two weeks early.'}
    answer = 'At Nformar, I led six engineers. By setting clear priorities, we launched the website two weeks early.'
    assert any('undocumented causal method' in issue for issue in check(answer, [fact], intent='LEADERSHIP'))
    detailed = fact | {'id': 'approach', 'key': 'approach', 'value': 'I set clear priorities for the team before launch.'}
    assert check(answer, [fact, detailed], intent='LEADERSHIP') == []
    assert check('At Nformar, I led six engineers. By coordinating the team, we launched the website two weeks early.',
                 [fact], intent='LEADERSHIP') == []
