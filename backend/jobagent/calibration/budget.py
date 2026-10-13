"""Time-limited uncapped validation authorization with restoration on exit."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import json
from uuid import uuid4

from jobagent.db import Database


@contextmanager
def uncapped_validation(path):
    db = Database(path)
    owner = str(uuid4())
    keys = ('ai_budget_unlimited', 'ai_budget_unlimited_until', 'ai_budget_validation_lease')
    with db.transaction() as connection:
        previous = {key: connection.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone() for key in keys}
        if previous[keys[2]]:
            from jobagent.usage import unlimited_authorized
            if unlimited_authorized(connection):
                raise ValueError('Another timed validation owns the shared spending authorization')
        values = (True, (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(), owner)
        for key, value in zip(keys, values):
            connection.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (key, json.dumps(value)))
    try:
        yield
    finally:
        with db.transaction() as connection:
            current = connection.execute('SELECT value FROM settings WHERE key=?', (keys[2],)).fetchone()
            if current and json.loads(current['value']) == owner:
                for key in keys:
                    if previous[key]:
                        connection.execute('INSERT OR REPLACE INTO settings(key,value) VALUES(?,?)', (key, previous[key]['value']))
                    else:
                        connection.execute('DELETE FROM settings WHERE key=?', (key,))
