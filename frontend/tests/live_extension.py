"""A stand-in for the browser extension's service worker, for UI checks against the REAL backend.

It speaks the bridge protocol over loopback exactly like the extension does (hello with the version header, pairing, then
bearer calls with the extension's Origin) so that the desktop UI sees genuine data: a detected browser, a pairing request with
its real code, a connected client, pages, inserted fields and saved answers. It is not the extension (no page, no content
script) and it never leaves the machine: the only peer is the temporary backend started by ``live_stack.LiveStack``.
"""
from __future__ import annotations

import json
from typing import Any

import httpx

# The extension id Meridian itself ships (backend/jobagent/extension/__init__.py CHROME_EXTENSION_IDS); any other id is
# "unrecognised" in the app and is shown with a red warning.
RECOGNISED_ID = "ophngbnbiekjhemmabbpildjedfphlbc"
UNRECOGNISED_ID = "pqrstuvwxyzabcdefghijklmnopqrstu"
# Safari extension origins carry a per-install UUID.
SAFARI_ORIGIN = "safari-web-extension://3F2A9C1E-5B7D-4E61-9A0C-7D2B8E4F1A63"
VERSION = "0.1.0"

JOB_URL = "https://boards.greenhouse.io/acmerobotics/jobs/4001"


def posting(url: str = JOB_URL, title: str = "Software Engineer", company: str = "Acme Robotics") -> dict:
    """The page report of a Greenhouse-style posting (JSON-LD only: no network is needed to understand it)."""
    job = {
        "@context": "https://schema.org", "@type": "JobPosting", "title": title,
        "description": "<p>" + "Build and run reliable payment services used by millions of customers every day. " * 8 + "</p>",
        "hiringOrganization": {"@type": "Organization", "name": company, "url": "https://acmerobotics.example/"},
        "jobLocation": {"address": {"addressLocality": "London", "addressCountry": "UK"}},
    }
    page = {"url": url, "title": f"{title} - {company}", "h1": title, "lang": "en", "company_hint": None, "meta": {},
            "canonical": None, "jsonld": [json.dumps(job)], "description_text": "", "links": [], "user": {}}
    return {"page": page, "tab_key": "live-tab-1", "locale": "en-GB"}


def control(node_id: int, label: str, **kwargs: Any) -> dict:
    """One text control as the content script reports it (observation.js shape, pruned)."""
    base = {
        "node_id": node_id, "role": "textbox", "tag": "input", "type": "text", "label": label, "context": "",
        "description": "", "placeholder": "", "section_id": "page", "section_label": "Application details", "form_id": 1,
        "group": "", "field_path": "", "name": "", "id": "", "autocomplete": "", "required": False, "enabled": True,
        "readonly": False, "multiple": False, "checked": False, "expanded": False, "has_value": False, "value": "",
        "selected_text": "", "selected_values": [], "operations": ["TYPE_TEXT", "CLEAR"], "options": [],
    }
    return {**base, **kwargs}


def suggest_body(elements: list[dict], url: str = JOB_URL) -> dict:
    return {"document_id": "doc-1", "frame_url": url, "frame_key": "top", "elements": elements, "mode": "scan",
            "challenge": False, "mfa": False, "login": False}


class FakeExtension:
    """One browser profile with the Meridian extension installed."""

    def __init__(self, bridge_base: str, extension_id: str = RECOGNISED_ID, *, origin: str | None = None) -> None:
        self.origin = origin or f"chrome-extension://{extension_id}"
        self.http = httpx.AsyncClient(base_url=bridge_base, timeout=30, trust_env=False)
        self.client_id: str | None = None
        self.token: str | None = None

    async def close(self) -> None:
        await self.http.aclose()

    def _headers(self, *, auth: bool = False) -> dict[str, str]:
        headers = {"Origin": self.origin, "Content-Type": "application/json", "X-Meridian-Version": VERSION}
        if auth:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    async def hello(self) -> dict:
        """What the worker does first on every start: find Meridian. An unpaired hello makes the app say 'found it'."""
        response = await self.http.get("/v1/hello", headers=self._headers())
        response.raise_for_status()
        return response.json()

    async def start_pairing(self, name: str = "Google Chrome") -> dict:
        """-> {pairing_id, code, expires_in, poll_after_ms}: the code the extension shows to the user."""
        response = await self.http.post(
            "/v1/pair/start", headers=self._headers(), json={"client_name": name, "extension_version": VERSION, "locale": "en-GB"})
        response.raise_for_status()
        return response.json()

    async def claim(self, pairing_id: str) -> dict:
        """One poll of the pairing: the token is handed over once, after the user approved it."""
        response = await self.http.get(f"/v1/pair/{pairing_id}", headers=self._headers())
        response.raise_for_status()
        data = response.json()
        if data.get("token"):
            self.token, self.client_id = data["token"], data.get("client_id")
        return data

    async def call(self, method: str, path: str, body: Any = None, expect: int | tuple[int, ...] = 200) -> Any:
        response = await self.http.request(method, path, headers=self._headers(auth=True), json=body)
        allowed = (expect,) if isinstance(expect, int) else expect
        assert response.status_code in allowed, f"{method} {path} -> {response.status_code}: {response.text[:300]}"
        return response.json() if response.content else None

    async def open_page(self, page: dict | None = None) -> dict:
        """POST /v1/sessions: the user opened an application form and clicked the toolbar icon."""
        return await self.call("POST", "/v1/sessions", page or posting())

    async def suggest(self, session_id: str, elements: list[dict]) -> dict[int, dict]:
        data = await self.call("POST", f"/v1/sessions/{session_id}/suggest", suggest_body(elements))
        return {item["node_id"]: item for item in data["results"]}

    async def feedback(self, session_id: str, suggestion: dict, outcome: str, **body: Any) -> dict:
        payload = {"question_id": suggestion["question_id"], "frame_key": suggestion["frame_key"], "outcome": outcome,
                   "request_id": suggestion["request_id"], "suggestion_state": suggestion["state"], **body}
        return await self.call("POST", f"/v1/sessions/{session_id}/feedback", payload)
