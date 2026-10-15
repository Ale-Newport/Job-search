import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from jobagent.db import Database
from jobagent.usage import Reservation, effective_rates, is_peak, reconcile, request_cost, validate_budget


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


# ---- what a request really costs: cache hits and off-peak hours (the ledger said $20 where the provider's dashboard said $9)
DEEPSEEK_PRO = {"input_cost_per_million": 1.32, "output_cost_per_million": 3.96}
PEAK = datetime(2026, 10, 14, 8, 30, tzinfo=timezone.utc)      # Wednesday 08:30 UTC
OFF_PEAK = datetime(2026, 10, 14, 14, 0, tzinfo=timezone.utc)  # Wednesday 14:00 UTC
WEEKEND = datetime(2026, 10, 17, 8, 30, tzinfo=timezone.utc)   # Saturday 08:30 UTC


def test_published_schedule_applies_only_to_the_published_rates_of_a_listed_model():
    rates = effective_rates("api.deepseek.com", "deepseek-v4-pro", DEEPSEEK_PRO)
    assert rates["cached_input_cost_per_million"] == 0.044 and rates["off_peak_multiplier"] == 0.5
    # another rate, another host or another model: trusted as configured, never discounted behind the user's back
    assert "cached_input_cost_per_million" not in effective_rates("api.deepseek.com", "deepseek-v4-pro", {**DEEPSEEK_PRO, "input_cost_per_million": 2})
    assert "off_peak_multiplier" not in effective_rates("api.example.com", "deepseek-v4-pro", DEEPSEEK_PRO)
    assert "off_peak_multiplier" not in effective_rates("api.deepseek.com", "another-model", DEEPSEEK_PRO)
    # what the user configured always wins
    mine = effective_rates("api.deepseek.com", "deepseek-v4-pro", {**DEEPSEEK_PRO, "cached_input_cost_per_million": 0.1, "off_peak_multiplier": 0.8})
    assert mine["cached_input_cost_per_million"] == 0.1 and mine["off_peak_multiplier"] == 0.8


@pytest.mark.parametrize("at,expected", [(PEAK, True), (OFF_PEAK, False), (WEEKEND, False),
                                         (datetime(2026, 10, 14, 4, 0, tzinfo=timezone.utc), False),
                                         (datetime(2026, 10, 14, 1, 0, tzinfo=timezone.utc), True),
                                         (datetime(2026, 10, 14, 10, 0, tzinfo=timezone.utc), False)])
def test_deepseek_peak_hours_are_weekday_mornings_utc(at, expected):
    assert is_peak(at, [[1, 4], [6, 10]], True) is expected


def test_cache_hits_and_off_peak_hours_lower_the_cost_and_nothing_else_does():
    rates = effective_rates("api.deepseek.com", "deepseek-v4-pro", DEEPSEEK_PRO)
    cold_peak = request_cost(rates, 39_000, 0, 540, PEAK)
    assert cold_peak == pytest.approx((39_000 * 1.32 + 540 * 3.96) / 1e6)  # the old arithmetic, exactly
    warm_peak = request_cost(rates, 39_000, 36_000, 540, PEAK)
    assert warm_peak == pytest.approx((3_000 * 1.32 + 36_000 * 0.044 + 540 * 3.96) / 1e6)
    assert request_cost(rates, 39_000, 36_000, 540, OFF_PEAK) == pytest.approx(warm_peak / 2)
    assert request_cost(rates, 39_000, 36_000, 540, WEEKEND) == pytest.approx(warm_peak / 2)
    # a figure that cannot be true (more hits than tokens) cannot make a request cost less than all-hits
    assert request_cost(rates, 100, 10_000, 0, PEAK) == pytest.approx(100 * 0.044 / 1e6)
    # configured rates without any schedule are charged in full whatever the clock says
    assert request_cost(DEEPSEEK_PRO, 39_000, 36_000, 540, OFF_PEAK) == pytest.approx(cold_peak)


def test_reconcile_sets_the_month_to_the_providers_figure_and_keeps_a_record(tmp_path):
    db = Database(tmp_path / "reconcile.db")
    held = Reservation(db, 0.5, 20, "openai-compatible")
    held.settle(19.0, 1000, 10)
    Reservation(db, 0.1, 20, "ollama").settle(0, 10, 1)
    usage = reconcile(db, 9.0, month=held.key[-7:], note="DeepSeek dashboard")
    assert usage["cost"] == 9.0
    assert usage["providers"]["openai-compatible"]["cost"] == pytest.approx(9.0)
    assert usage["providers"]["ollama"]["cost"] == 0
    assert usage["adjustments"][-1]["from"] == pytest.approx(19.0) and usage["adjustments"][-1]["to"] == 9.0
    assert usage["calls"] == 2 and usage["input_tokens"] == 1010
    stored = json.loads(db.one("SELECT value FROM settings WHERE key=?", (held.key,))["value"])
    assert stored["cost"] == 9.0
    # the budget check reads the corrected figure: the month is open again
    Reservation(db, 5, 20, "openai-compatible")


@pytest.mark.parametrize("amount", [-1, float("nan"), float("inf"), True, "9", None, 1e9])
def test_reconcile_refuses_amounts_that_are_not_money(tmp_path, amount):
    with pytest.raises(ValueError):
        reconcile(Database(tmp_path / "bad.db"), amount)
