"""Transactional, conservative shared monthly budget for text and browser providers."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone


def price_config(db, provider):
    row = db.one("SELECT config FROM integrations WHERE provider=?", (provider,))
    prices = json.loads(row["config"]) if row else {}
    keys = ("input_cost_per_million", "output_cost_per_million")
    if any(not isinstance(prices.get(k), (int, float)) or not math.isfinite(prices[k]) or prices[k] < 0 for k in keys):
        raise ValueError(f"Configure finite nonnegative token prices for {provider} to enforce the monthly budget")
    return prices


class Reservation:
    def __init__(self, db, maximum_cost: float, budget: float, provider: str):
        self.db, self.maximum_cost, self.provider = db, maximum_cost, provider
        self.key = "ai_usage:" + datetime.now(timezone.utc).strftime("%Y-%m")
        self.settled = False
        with db.transaction() as connection:
            usage = self._read(connection)
            if maximum_cost > 0 and usage["cost"] + maximum_cost > budget:
                raise ValueError("Monthly AI budget would be exceeded")
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

    def settle(self, actual_cost: float, input_tokens=0, output_tokens=0):
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
            usage["providers"][self.provider]["cost"] = max(0, usage["providers"][self.provider]["cost"] + adjustment)
            connection.execute("INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)", (self.key, json.dumps(usage)))
        self.settled = True


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
