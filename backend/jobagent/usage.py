"""Transactional, conservative shared monthly budget for text and browser providers."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone

MAX_MONTHLY_BUDGET = 20.0

# What a cloud provider really charges, as it publishes it. The configured per-million rates are what the monthly cap is enforced
# with (the highest, "peak, cache miss" figures: a request is reserved at that price), but what a request COSTS depends on two
# things the provider's own response and calendar tell: how many prompt tokens were served from its context cache (a small
# fraction of the price) and whether the request ran in its off-peak hours (a fraction of every rate). Charging every request at
# the highest rate made the ledger say $20 where the provider's dashboard said $9. A record of ``answer_model_prices`` may carry,
# for its exact model, ``cached_input_cost_per_million``, ``off_peak_multiplier`` (0 < m <= 1), ``peak_hours_utc`` ([[1, 4], [6, 10]],
# end exclusive) and ``peak_weekdays_only``. When a record has none of them and its input and output rates ARE the published peak
# rates of a model listed here, the published schedule applies; any other rate is trusted as configured and never discounted.
PUBLISHED_RATES = {
    "api.deepseek.com": {
        "source": "https://api-docs.deepseek.com/quick_start/pricing",
        "checked": "2026-10-15",
        "off_peak_multiplier": 0.5,
        "peak_hours_utc": [[1, 4], [6, 10]],
        "peak_weekdays_only": True,
        "models": {
            "deepseek-v4-pro": {"input_cost_per_million": 1.32, "cached_input_cost_per_million": 0.044,
                                "output_cost_per_million": 3.96},
            "deepseek-flash": {"input_cost_per_million": 0.3, "cached_input_cost_per_million": 0.006,
                               "output_cost_per_million": 1.2},
        },
    },
}
SCHEDULE_KEYS = ("cached_input_cost_per_million", "off_peak_multiplier", "peak_hours_utc", "peak_weekdays_only")


def _rate(value) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(value) and value >= 0


def effective_rates(host: str | None, model: str, configured: dict) -> dict:
    """The configured rates of ONE model with the published schedule filled in where it applies (never overriding what the user
    configured): ``{input_cost_per_million, output_cost_per_million, [cached_input_cost_per_million, off_peak_multiplier,
    peak_hours_utc, peak_weekdays_only]}``."""
    rates = {key: configured[key] for key in ("input_cost_per_million", "output_cost_per_million",
                                                 "cache_write_cost_per_million") if key in configured}
    # the optional schedule of a record is trusted only in its valid forms: a malformed one is the same as none
    if _rate(configured.get("cached_input_cost_per_million")):
        rates["cached_input_cost_per_million"] = configured["cached_input_cost_per_million"]
    factor = configured.get("off_peak_multiplier")
    if _rate(factor) and 0 < factor <= 1:
        rates["off_peak_multiplier"] = factor
    hours = configured.get("peak_hours_utc")
    if (isinstance(hours, list) and 0 < len(hours) <= 8 and all(
            isinstance(w, (list, tuple)) and len(w) == 2 and all(isinstance(h, int) and not isinstance(h, bool) and 0 <= h <= 24 for h in w)
            for w in hours)):
        rates["peak_hours_utc"] = [list(w) for w in hours]
    if isinstance(configured.get("peak_weekdays_only"), bool):
        rates["peak_weekdays_only"] = configured["peak_weekdays_only"]
    published = (PUBLISHED_RATES.get(str(host or "").lower()) or {})
    entry = (published.get("models") or {}).get(model)
    if entry and all(_rate(configured.get(k)) and abs(float(configured[k]) - entry[k]) < 1e-9
                     for k in ("input_cost_per_million", "output_cost_per_million")):
        rates.setdefault("cached_input_cost_per_million", entry["cached_input_cost_per_million"])
        for key in ("off_peak_multiplier", "peak_hours_utc", "peak_weekdays_only"):
            rates.setdefault(key, published[key])
    return rates


def is_peak(at: datetime, hours, weekdays_only: bool) -> bool:
    at = at.astimezone(timezone.utc)
    if weekdays_only and at.weekday() >= 5:
        return False
    return any(isinstance(window, (list, tuple)) and len(window) == 2 and window[0] <= at.hour < window[1]
               for window in hours or ())


def request_cost(rates: dict, input_tokens: int, cached_tokens: int, output_tokens: int, at: datetime | None = None) -> float:
    """USD for one request: cache-miss prompt tokens at the input rate, cache-hit ones at the cached rate (or the input rate
    when the model has none), completion tokens at the output rate, all scaled by the off-peak multiplier outside peak hours."""
    cached = max(0, min(int(cached_tokens or 0), int(input_tokens)))
    cached_rate = rates.get("cached_input_cost_per_million")
    cached_rate = rates["input_cost_per_million"] if not _rate(cached_rate) else min(cached_rate, rates["input_cost_per_million"])
    total = ((input_tokens - cached) * rates["input_cost_per_million"] + cached * cached_rate
             + output_tokens * rates["output_cost_per_million"]) / 1_000_000
    factor = rates.get("off_peak_multiplier")
    if _rate(factor) and 0 < factor < 1 and rates.get("peak_hours_utc"):
        if not is_peak(at or datetime.now(timezone.utc), rates["peak_hours_utc"], rates.get("peak_weekdays_only") is True):
            total *= factor
    return total


class BudgetExceededError(ValueError):
    """The shared monthly AI cap cannot cover another provider request.

    A ValueError subclass, so existing callers and message checks keep working; ``code`` lets
    callers (the browser extension queue) stop spending without parsing the message.
    """

    code = 'AI_BUDGET_EXCEEDED'


def unlimited_authorized(connection):
    row = connection.execute("SELECT value FROM settings WHERE key='ai_budget_unlimited'").fetchone()
    if not row or json.loads(row['value']) is not True:
        return False
    expiry = connection.execute("SELECT value FROM settings WHERE key='ai_budget_unlimited_until'").fetchone()
    if not expiry or not json.loads(expiry['value']):
        return True  # Explicit permanent selection in the settings UI.
    try:
        deadline = datetime.fromisoformat(json.loads(expiry['value']))
        return deadline.tzinfo is not None and datetime.now(timezone.utc) < deadline
    except (ValueError, TypeError):
        return False


def validate_budget(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= MAX_MONTHLY_BUDGET:
        raise ValueError('Monthly AI budget must be a finite amount between $0 and $20')
    return float(value)


def price_config(db, provider):
    row = db.one("SELECT config FROM integrations WHERE provider=?", (provider,))
    prices = json.loads(row["config"]) if row else {}
    keys = ("input_cost_per_million", "output_cost_per_million")
    if any(isinstance(prices.get(k), bool) or not isinstance(prices.get(k), (int, float)) or not math.isfinite(prices[k]) or prices[k] < 0 for k in keys):
        raise ValueError(f"Configure finite nonnegative token prices for {provider} to enforce the monthly budget")
    return prices


class Reservation:
    def __init__(self, db, maximum_cost: float, budget: float, provider: str, *, unlimited: bool = False):
        budget = validate_budget(budget)
        if isinstance(maximum_cost, bool) or not isinstance(maximum_cost, (int, float)) or not math.isfinite(maximum_cost) or maximum_cost < 0:
            raise ValueError('Invalid maximum AI request cost')
        self.db, self.maximum_cost, self.provider = db, maximum_cost, provider
        self.key = "ai_usage:" + datetime.now(timezone.utc).strftime("%Y-%m")
        self.settled = False
        with db.transaction() as connection:
            # Both the caller and the current authoritative ledger must opt in.
            # A cached QA configuration cannot override a revoked authorization.
            unlimited = unlimited is True and unlimited_authorized(connection)
            configured = connection.execute("SELECT value FROM settings WHERE key='monthly_budget'").fetchone()
            if configured:
                budget = min(budget, validate_budget(json.loads(configured['value'])))
            usage = self._read(connection)
            if not unlimited and maximum_cost > 0 and usage["cost"] + maximum_cost > budget:
                raise BudgetExceededError("Monthly AI budget would be exceeded")
            usage["cost"] += maximum_cost
            usage["calls"] += 1
            entry = usage.setdefault("providers", {}).setdefault(provider, {"cost": 0, "calls": 0})
            entry["cost"] += maximum_cost
            entry["calls"] += 1
            connection.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (self.key, json.dumps(usage)))

    def _read(self, connection):
        row = connection.execute("SELECT value FROM settings WHERE key=?", (self.key,)).fetchone()
        return (
            json.loads(row["value"])
            if row
            else {"cost": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "providers": {}}
        )

    def settle(self, actual_cost: float, input_tokens=0, output_tokens=0, cached_tokens=0):
        if self.settled:
            return
        if not math.isfinite(actual_cost) or actual_cost < 0:
            raise ValueError("Invalid provider usage cost")
        with self.db.transaction() as connection:
            usage = self._read(connection)
            adjustment = actual_cost - self.maximum_cost
            usage["cost"] = max(0, usage["cost"] + adjustment)
            usage["input_tokens"] += input_tokens
            usage["output_tokens"] += output_tokens
            if cached_tokens:
                usage["cache_hit_tokens"] = usage.get("cache_hit_tokens", 0) + cached_tokens
            usage["providers"][self.provider]["cost"] = max(0, usage["providers"][self.provider]["cost"] + adjustment)
            connection.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (self.key, json.dumps(usage)))
        self.settled = True


def reconcile(db, actual_cost: float, *, month: str | None = None, note: str = "") -> dict:
    """Set the month's recorded spend to what the provider's own dashboard says (the ledger is an estimate made from token counts
    and configured rates; the dashboard is the invoice). Every provider's share is scaled in proportion, and the correction is kept
    in ``adjustments`` (who, when, from what to what) so it is never silent. Returns the ledger."""
    if isinstance(actual_cost, bool) or not isinstance(actual_cost, (int, float)) or not math.isfinite(actual_cost) or actual_cost < 0:
        raise ValueError("The amount must be a finite number of dollars, zero or more")
    if actual_cost > 100_000:
        raise ValueError("The amount is not plausible")
    month = month or datetime.now(timezone.utc).strftime("%Y-%m")
    if not isinstance(month, str) or len(month) != 7 or month[4] != "-" or not (month[:4] + month[5:]).isdigit():
        raise ValueError("The month must look like 2026-10")
    key = "ai_usage:" + month
    with db.transaction() as connection:
        row = connection.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        usage = json.loads(row["value"]) if row else {"cost": 0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "providers": {}}
        before = float(usage.get("cost") or 0)
        factor = (actual_cost / before) if before > 0 else 0
        for entry in (usage.get("providers") or {}).values():
            if isinstance(entry, dict):
                entry["cost"] = round(float(entry.get("cost") or 0) * factor, 8)
        if before <= 0 and actual_cost > 0:
            usage.setdefault("providers", {}).setdefault("other", {"cost": 0, "calls": 0})["cost"] = actual_cost
        usage["cost"] = actual_cost
        log = usage.setdefault("adjustments", [])
        log.append({"at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "from": round(before, 6),
                    "to": round(float(actual_cost), 6), "note": str(note)[:200]})
        del log[:-20]
        connection.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (key, json.dumps(usage)))
    return usage


def decision_settings(db, settings: dict, secret_store):
    result = dict(settings)
    if settings.get("browser_engine") not in ("jev", "hybrid"):
        return result
    result["_jev_api_key"] = secret_store.get("jev:secret")
    reservation = None
    prices = None

    def authorize():
        nonlocal reservation, prices
        prices = price_config(db, "jev")
        reservation = Reservation(
            db,
            (64000 * prices["input_cost_per_million"] + 2000 * prices["output_cost_per_million"]) / 1_000_000,
            float(settings.get("monthly_budget", 0)),
            "jev",
            unlimited=settings.get("ai_budget_unlimited") is True,
        )
        return True

    def record(usage):
        if reservation and prices and usage:
            input_tokens, output_tokens = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
            cost = (
                input_tokens * prices["input_cost_per_million"] + output_tokens * prices["output_cost_per_million"]
            ) / 1_000_000
            reservation.settle(cost, input_tokens, output_tokens)

    result.update(_jev_authorize=authorize, _jev_record=record)
    return result
