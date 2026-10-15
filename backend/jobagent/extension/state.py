"""Lifecycle of the assistant's services on ``app.state`` and the settings they read.

``main.create_app`` calls :func:`install_extension_state` right after ``app.state.text_ai`` exists and
:func:`close_extension_state` on shutdown (before the automation is closed). Everything created here is inert until a
paired browser uses it: no task, socket, file or model call exists before the first session, so it is harmless when
the bridge is disabled (tests, packaged workers).

``app.state.extension_research``    ``ResearchManager``   company and role research (no model calls)
``app.state.extension_sessions``    ``SessionRegistry``   page sessions, suggestions, feedback, tracking, documents
``app.state.extension_generation``  ``GenerationQueue``   background drafting of long answers
"""

from __future__ import annotations

import json
import logging

logger = logging.getLogger("meridian.extension")
CACHE_PREFIXES = ("validated_answer_cache:",)


def read_settings(db) -> dict:
    """``core.get_settings`` without loading the private answer caches (they can be megabytes of candidate text)."""
    from ..core import DEFAULT_SETTINGS
    from ..usage import unlimited_authorized

    result = dict(DEFAULT_SETTINGS)
    for row in db.query("SELECT key,value FROM settings WHERE key NOT LIKE 'validated_answer_cache:%'"):
        try:
            result[row["key"]] = json.loads(row["value"])
        except ValueError:
            result[row["key"]] = row["value"]
    if result.get("ai_budget_unlimited") is True and result.get("ai_budget_unlimited_until"):
        connection = db._connect()
        try:
            result["ai_budget_unlimited"] = unlimited_authorized(connection)
        finally:
            connection.close()
    return result


def research_settings(db) -> dict:
    """What the research manager reads (a handful of rows, never the whole table)."""
    from ..core import DEFAULT_SETTINGS

    keys = ("extension_research_enabled", "extension_web_lookup", "extension_research_concurrency",
            "extension_company_sites")
    values = {key: DEFAULT_SETTINGS.get(key) for key in keys}
    marks = ",".join("?" * len(keys))
    for row in db.query(f"SELECT key,value FROM settings WHERE key IN ({marks})", keys):
        try:
            values[row["key"]] = json.loads(row["value"])
        except ValueError:
            continue
    pins = values["extension_company_sites"] if isinstance(values["extension_company_sites"], dict) else {}
    concurrency = values["extension_research_concurrency"]
    return {"research_enabled": values["extension_research_enabled"] is not False,
            "web_lookup": values["extension_web_lookup"] is not False,
            "concurrency": concurrency if isinstance(concurrency, int) and not isinstance(concurrency, bool) else 2,
            "pins": pins}


def install_extension_state(app) -> None:
    """Create the research manager, generation queue and session registry. Idempotent; never raises."""
    if getattr(app.state, "extension_sessions", None) is not None:
        return
    try:
        from .generation import GenerationQueue
        from .research import ResearchManager
        from .session import SessionRegistry

        db, root = app.state.db, app.state.data_dir
        research = ResearchManager(root, db, settings_getter=lambda: research_settings(db))
        generation = GenerationQueue(llm_getter=lambda: getattr(getattr(app.state, "text_ai", None), "answer_llm", None))
        app.state.extension_research = research
        app.state.extension_generation = generation
        app.state.extension_sessions = SessionRegistry(app, research, generation)
    except Exception as exc:  # the assistant is optional: a failure here must never stop Meridian
        logger.error("extension_state_unavailable %s", type(exc).__name__)
        app.state.extension_research = app.state.extension_generation = app.state.extension_sessions = None


async def close_extension_state(app) -> None:
    """Stop accepting work, cancel QUEUED drafts and unfinished research, restore the model's progress hook. Never raises."""
    for name in ("extension_sessions", "extension_generation", "extension_research"):
        service = getattr(app.state, name, None)
        if service is None:
            continue
        try:
            await service.close()
        except Exception as exc:
            logger.warning("extension_close_failed %s %s", name, type(exc).__name__)
