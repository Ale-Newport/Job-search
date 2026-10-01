"""A closed-shadow guide in a CDP isolated world: no app tokens in employer JS."""

from __future__ import annotations
import asyncio
import contextlib
import json
from pathlib import Path
import secrets

SCRIPT = Path(__file__).with_suffix(".js").read_text()


class BrowserPrompt:
    def __init__(self):
        self.cdp = None
        self.context_id = None
        self.token = None
        self.handler = None
        self.tasks = set()
        self.busy = False

    async def show(self, page, data, handler):
        await self.close()
        self.handler = handler
        self.token = secrets.token_urlsafe(32)
        self.cdp = await page.context.new_cdp_session(page)
        await self.cdp.send("Page.enable")
        await self.cdp.send("Runtime.enable")
        frame = (await self.cdp.send("Page.getFrameTree"))["frameTree"]["frame"]["id"]
        self.context_id = (
            await self.cdp.send("Page.createIsolatedWorld", {"frameId": frame, "worldName": "Meridian-" + self.token})
        )["executionContextId"]
        binding = "meridianAnswer"
        await self.cdp.send("Runtime.addBinding", {"name": binding, "executionContextId": self.context_id})
        self.cdp.on("Runtime.bindingCalled", self._message)
        result = await self.cdp.send(
            "Runtime.evaluate",
            {
                "contextId": self.context_id,
                "expression": SCRIPT.replace("__DATA__", json.dumps({**data, "token": self.token, "binding": binding})),
            },
        )
        if result.get("exceptionDetails"):
            await self.close()
            raise ValueError("The browser guide could not be displayed")
        await page.bring_to_front()

    def _message(self, event):
        if self.busy or event.get("executionContextId") != self.context_id or event.get("name") != "meridianAnswer":
            return
        try:
            payload = json.loads(event["payload"])
        except (ValueError, KeyError):
            return
        if (
            not isinstance(payload, dict)
            or payload.get("token") != self.token
            or payload.get("action") not in {"answer", "skip", "draft", "rescan", "pause"}
        ):
            return
        if (
            not isinstance(payload.get("answer"), str)
            or len(payload["answer"]) > 20000
            or not isinstance(payload.get("reusable"), bool)
        ):
            return
        self.busy = True
        task = asyncio.create_task(self._dispatch(payload, self.token))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def _dispatch(self, payload, token):
        try:
            update = await self.handler(payload)
            if token == self.token and update:
                await self.update(update)
        except Exception as exc:
            if token == self.token:
                await self.update({"message": str(exc)[:350] + " You can edit your answer or recheck the form."})
        finally:
            if token == self.token:
                self.busy = False

    async def update(self, data):
        if self.cdp:
            with contextlib.suppress(Exception):
                await self.cdp.send(
                    "Runtime.evaluate",
                    {
                        "contextId": self.context_id,
                        "expression": "globalThis.__meridianPrompt?.update(" + json.dumps(data) + ")",
                    },
                )

    async def close(self):
        self.token = None
        if self.cdp:
            with contextlib.suppress(Exception):
                await self.cdp.send(
                    "Runtime.evaluate",
                    {"contextId": self.context_id, "expression": "globalThis.__meridianPrompt?.remove()"},
                )
            with contextlib.suppress(Exception):
                await self.cdp.detach()
        self.cdp = None
        self.context_id = None
        self.busy = False
