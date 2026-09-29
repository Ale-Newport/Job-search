import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from jobagent.db import Database
from jobagent.usage import Reservation


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
