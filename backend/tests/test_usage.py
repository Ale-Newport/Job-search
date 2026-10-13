import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from jobagent.db import Database
from jobagent.usage import Reservation, validate_budget


def test_shared_budget_is_atomic_across_providers(tmp_path):
    db = Database(tmp_path / "test.db")

    def reserve(provider):
        try:
            return Reservation(db, 0.7, 1.0, provider)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        reservations = list(pool.map(reserve, ["jev", "text"]))
    assert sum(r is not None for r in reservations) == 1
    accepted = next(r for r in reservations if r)
    accepted.settle(0.2, 100, 5)
    second = Reservation(db, 0.7, 1.0, "other")
    second.settle(0.1, 50, 5)
    usage = json.loads(db.one("SELECT value FROM settings WHERE key=?", (second.key,))["value"])
    assert usage["cost"] == pytest.approx(0.3)
    assert usage["calls"] == 2
    assert usage["input_tokens"] == 150


@pytest.mark.parametrize('value', [-1, 20.01, 100, True, None, '20', float('nan'), float('inf')])
def test_all_provider_budgets_require_a_finite_authorized_amount(value):
    with pytest.raises(ValueError, match='between'):
        validate_budget(value)


def test_twenty_dollar_cap_is_shared_and_live_reductions_override_cached_settings(tmp_path):
    db = Database(tmp_path / 'budget.db')
    db.execute("INSERT INTO settings VALUES('monthly_budget','20')")
    first = Reservation(db, 19, 20, 'openai')
    first.settle(19)
    second = Reservation(db, 1, 20, 'anthropic')
    second.settle(1)
    with pytest.raises(ValueError, match='exceeded'):
        Reservation(db, .001, 20, 'gemini')
    db.execute("UPDATE settings SET value='10' WHERE key='monthly_budget'")
    with pytest.raises(ValueError, match='exceeded'):
        Reservation(db, .001, 20, 'jev')


def test_request_rechecks_live_budget_inside_atomic_reservation(tmp_path):
    db = Database(tmp_path / 'budget.db')
    db.execute("INSERT INTO settings VALUES('monthly_budget','0')")
    with pytest.raises(ValueError, match='exceeded'):
        Reservation(db, .001, 20, 'openai-compatible')


def test_unlimited_requires_live_authorization_and_still_records_usage(tmp_path):
    db = Database(tmp_path / 'unlimited.db')
    db.execute("INSERT INTO settings VALUES('monthly_budget','0')")
    with pytest.raises(ValueError, match='exceeded'):
        Reservation(db, 30, 20, 'text', unlimited=True)
    db.execute("INSERT INTO settings VALUES('ai_budget_unlimited','true')")
    paid = Reservation(db, 30, 20, 'text', unlimited=True)
    paid.settle(25, 100, 50)
    usage = json.loads(db.one('SELECT value FROM settings WHERE key=?', (paid.key,))['value'])
    assert usage['cost'] == 25 and usage['calls'] == 1
    assert usage['input_tokens'] == 100
    with pytest.raises(ValueError, match='exceeded'):
        Reservation(db, .01, 20, 'other')
    db.execute("UPDATE settings SET value='false' WHERE key='ai_budget_unlimited'")
    with pytest.raises(ValueError, match='exceeded'):
        Reservation(db, .01, 20, 'text', unlimited=True)


def test_timed_validation_restores_controls_on_failure_and_expires_on_crash(tmp_path):
    from jobagent.calibration.budget import uncapped_validation
    db = Database(tmp_path / 'lease.db')
    db.execute("INSERT INTO settings VALUES('monthly_budget','20')")
    with pytest.raises(RuntimeError):
        with uncapped_validation(db.path):
            paid = Reservation(db, 25, 20, 'test', unlimited=True)
            paid.settle(21)
            with pytest.raises(ValueError, match='Another timed validation'):
                with uncapped_validation(db.path):
                    pass
            raise RuntimeError('Simulated provider failure')
    assert db.one("SELECT value FROM settings WHERE key='monthly_budget'")['value'] == '20'
    assert db.one("SELECT value FROM settings WHERE key='ai_budget_unlimited'") is None
    db.execute("INSERT INTO settings VALUES('ai_budget_unlimited','true')")
    db.execute('INSERT INTO settings VALUES(?,?)', ('ai_budget_unlimited_until', json.dumps('2000-01-01T00:00:00+00:00')))
    with pytest.raises(ValueError, match='exceeded'):
        Reservation(db, .01, 20, 'test', unlimited=True)
