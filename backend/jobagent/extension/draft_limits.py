"""How many drafts a page may make Meridian write on its own (SEC-06).

Drafting is automatic: focus an essay question and a model writes it, from the user's facts and the job text, through the
user's own provider, at the user's cost. Without a cumulative limit a page that rewords its question (a character counter in
the label, a reference number, a hostile script) makes a NEW paid draft for every variant: 112 model calls in 45 s from one
page, and about 60 from an honest ``0/500`` counter while the user simply typed. Three things stop that:

* :func:`stable_text` reads the counters and reference numbers out of the text a draft is keyed by, so the same question with
  another counter is the same draft (it does not touch what the model is asked: only the cache key);
* :class:`DraftLedger` counts the drafts started AUTOMATICALLY per page, per browser and overall in a rolling hour; past a
  limit nothing more is written on its own (a draft the user asks for, "Draft with AI", is never counted: it is their choice
  and their budget);
* when a page hits its limit, that page is told once (``drafts_paused``) and the field says why.
"""

from __future__ import annotations

import re
import time
from collections import deque

SESSION_AUTO_DRAFTS = 8  # per page and rolling hour
CLIENT_AUTO_DRAFTS = 40  # per browser
TOTAL_AUTO_DRAFTS = 80  # per Meridian
WINDOW_SECONDS = 3600.0

# A character / word counter, a reference or attempt number: noise around a question, never part of it.
COUNTERS = [
    (re.compile(r"\b\d+\s*/\s*(\d+)\b"), r"#/\1"),  # '0/500', '12 / 500'
    (re.compile(r"\b\d+\s+(?:of|out of)\s+(\d+)\b", re.I), r"# of \1"),
    (re.compile(r"\b\d+\s+(?:characters?|chars?|words?|letters?)\s+(?:left|remaining|used|so far|typed|to go)\b", re.I),
     "# left"),
    (re.compile(r"\b(?:characters?|chars?|words?)\s+(?:left|remaining|used|typed)\s*:?\s*\d+\b", re.I), "left #"),
    (re.compile(r"[(\[]\s*(?:ref(?:erence)?|id|no\.?|n[ºo°]|attempt|try|version|v|#)\s*[:#.]?\s*[\w-]{1,12}\s*[)\]]", re.I), ""),
    (re.compile(r"\b(?:ref(?:erence)?|attempt|try)\s*[:#]?\s*\d+\b", re.I), ""),
]


def stable_text(value) -> str:
    """The text of a question (or its help text) as the draft cache key sees it: counters and reference numbers read out,
    spacing and case folded. ``"Why Acme? (reference 7)"`` and ``"Why Acme? (reference 8)"`` are the same question."""
    text = str(value or "")
    for pattern, replacement in COUNTERS:
        text = pattern.sub(replacement, text)
    return re.sub(r"\s+", " ", text).strip().casefold()


class DraftLedger:
    """Drafts started on their own, counted per page, per browser and overall, in a rolling window. In memory: a restart of
    Meridian starts the count again (and so does a new hour)."""

    def __init__(self, *, clock=time.monotonic, per_session: int | None = None, per_client: int | None = None,
                 total: int | None = None, window: float = WINDOW_SECONDS):
        self._clock, self._window = clock, window
        self.limits = {"session": per_session if per_session is not None else SESSION_AUTO_DRAFTS,
                       "client": per_client if per_client is not None else CLIENT_AUTO_DRAFTS,
                       "total": total if total is not None else TOTAL_AUTO_DRAFTS}
        self._stamps: dict[tuple, deque] = {}

    def _count(self, key: tuple) -> int:
        stamps = self._stamps.get(key)
        if not stamps:
            return 0
        horizon = self._clock() - self._window
        while stamps and stamps[0] <= horizon:
            stamps.popleft()
        if not stamps:
            self._stamps.pop(key, None)
        return len(stamps)

    def check(self, session_id: str, client_id: str | None) -> tuple[str, str] | None:
        """None when one more automatic draft is allowed, else ``(scope, message)`` for the tightest limit reached."""
        if self._count(("session", session_id)) >= self.limits["session"]:
            return ("session", f"Meridian already wrote {self.limits['session']} drafts for this page on its own. Use "
                               "'Draft with AI' on the questions you want written.")
        if client_id and self._count(("client", client_id)) >= self.limits["client"]:
            return ("client", "Meridian has written many drafts for this browser in the last hour, so it stops writing more "
                              "on its own for a while. Use 'Draft with AI' on the questions you want written.")
        if self._count(("total",)) >= self.limits["total"]:
            return ("total", "Meridian has written many drafts in the last hour, so it stops writing more on its own for a "
                             "while. Use 'Draft with AI' on the questions you want written.")
        return None

    def record(self, session_id: str, client_id: str | None) -> None:
        now = self._clock()
        for key in (("session", session_id), ("client", client_id) if client_id else None, ("total",)):
            if key is not None:
                self._stamps.setdefault(key, deque()).append(now)

    def used(self, session_id: str) -> int:
        return self._count(("session", session_id))
