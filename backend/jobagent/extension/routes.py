"""Bridge routes of the assistant (mounted at ``/v1`` by ``app.create_bridge_app``): sessions, suggestions, long-poll,
drafts, feedback, tracking, research corrections and approved documents. Every route needs a paired client
(``auth.current_client``); bodies are validated by ``schemas`` (unknown keys dropped); errors are ``{detail, code}``.

The services live on the MAIN application's ``app.state`` (``extension_sessions`` ...): the bridge app is a separate
FastAPI instance whose ``state.main`` points at it. A plain app that carries the services itself works too (tests).

Wire conventions the extension relies on (the shapes themselves are ``schemas.py`` / ``protocol.ts``):

* The page sends EVERY control of a frame it scans (``observation.js`` shape, no typed text). A radio set or multi-checkbox
  group is ONE assist field: its suggestion carries the ``node_id`` of the group's FIRST control (request order) and
  ``flags.members`` lists every member. ``mode: "focus"`` answers only the field the focused control belongs to.
* ``skipped`` lists controls that are not fields (``button``, ``auxiliary``, ``group_member``, ``not_a_field``); fields the
  assistant deliberately leaves alone (password, hidden, readonly, disabled, a CV-less file input, challenge/MFA/login
  pages) come back in ``results`` with state ``SKIP`` and ``why.rules[0]`` naming the reason.
* ``question_id`` is stable across reloads of the same page (scoped by the session, not by the document) and is the key
  of ``regenerate`` and ``feedback``. ``knowledge_version`` moves when the profile, a policy or the protected-Tab switch
  moves, never when research learns more about the page.
* A session id survives a reload and a restart of Meridian (it is the ``extension_sessions`` row); after a restart the
  in-memory session is gone and every route answers 410 ``expired`` until the page is opened again with ``POST /sessions``.
* Long answers arrive as ``suggestion`` events on ``/poll`` (progress, then the validated draft); research changes arrive as
  ``research`` events; the profile or tracking changing inside Meridian arrives as ``knowledge`` / ``tracked`` events.
* A browser holds ONE ``POST /v1/poll`` for all of its pages (at most 16 listed, one wait of <= 25 s that ends as soon as any
  of them has events) instead of one connection per page; ``POST /sessions/{sid}/poll`` stays for a single page. A newer poll
  of a page makes the older one return at once, releasing a page ends its poll, and a browser that hangs up frees it too.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Request

from .auth import BridgeError, Client, current_client
from .schemas import (FeedbackRequest, PollManyRequest, PollRequest, RegenerateRequest, ResearchCompanyRequest,
                      ResearchSiteRequest, SessionCreateRequest, SuggestRequest, TrackRequest)

router = APIRouter()


def registry(request: Request):
    state = request.app.state
    main = getattr(state, "main", None)
    service = getattr(main.state if main is not None else state, "extension_sessions", None)
    if service is None:
        raise BridgeError(503, "The browser assistant is not available right now", "unavailable")
    return service


@router.post("/sessions")
async def create_session(payload: SessionCreateRequest, request: Request, client: Client = Depends(current_client)):
    return await registry(request).create(client, payload)


@router.delete("/sessions/{session_id}")
async def release_session(session_id: str, request: Request, client: Client = Depends(current_client)):
    await registry(request).release(client, session_id)
    return {"released": True}


@router.post("/sessions/{session_id}/suggest")
async def suggest(session_id: str, payload: SuggestRequest, request: Request, client: Client = Depends(current_client)):
    return await registry(request).suggest(client, session_id, payload)


@router.post("/sessions/{session_id}/poll")
async def poll(session_id: str, payload: PollRequest, request: Request, client: Client = Depends(current_client)):
    return await registry(request).poll(client, session_id, payload, disconnected=request.is_disconnected)


@router.post("/poll")
async def poll_many(payload: PollManyRequest, request: Request, client: Client = Depends(current_client)):
    """One long-poll for all the pages of a browser: events grouped by page, per-page ``status`` (see ``PollManyEntry``)."""
    return await registry(request).poll_batch(client, payload, disconnected=request.is_disconnected)


@router.post("/sessions/{session_id}/regenerate")
async def regenerate(session_id: str, payload: RegenerateRequest, request: Request,
                     client: Client = Depends(current_client)):
    return await registry(request).regenerate(client, session_id, payload)


@router.post("/sessions/{session_id}/feedback")
async def feedback(session_id: str, payload: FeedbackRequest, request: Request, client: Client = Depends(current_client)):
    return await registry(request).feedback(client, session_id, payload)


@router.post("/sessions/{session_id}/track")
async def track(session_id: str, payload: TrackRequest, request: Request, client: Client = Depends(current_client)):
    return await registry(request).track(client, session_id, payload)


@router.post("/sessions/{session_id}/research/company")
async def research_company(session_id: str, payload: ResearchCompanyRequest, request: Request,
                           client: Client = Depends(current_client)):
    return await registry(request).research_company(client, session_id, payload.company)


@router.post("/sessions/{session_id}/research/site")
async def research_site(session_id: str, payload: ResearchSiteRequest, request: Request,
                        client: Client = Depends(current_client)):
    return await registry(request).research_site(client, session_id, payload.domain)


@router.post("/sessions/{session_id}/research/refresh")
async def research_refresh(session_id: str, request: Request, client: Client = Depends(current_client)):
    return await registry(request).research_refresh(client, session_id)


@router.get("/documents")
async def documents(request: Request, session_id: str | None = None, client: Client = Depends(current_client)):
    """The user's approved CVs, default first. ``?session_id=`` (a page of this browser) also lists the CV written for that
    job, so a panel can offer a choice among the CVs that may go to that employer."""
    service = registry(request)
    session = service.get(client, session_id) if session_id else None  # 404 / 410 for a page that is not this browser's
    return await asyncio.to_thread(service.documents, session)  # database reads and one stat per CV


@router.get("/documents/{document_id}/content")
async def document_content(document_id: str, request: Request, client: Client = Depends(current_client)):
    """Up to 10 MiB read, hashed and base64-encoded: in a worker thread, not in the event loop the main API shares."""
    return await asyncio.to_thread(registry(request).document, document_id)
