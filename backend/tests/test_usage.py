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
