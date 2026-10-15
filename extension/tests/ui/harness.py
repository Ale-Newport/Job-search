"""Shared helpers of the UI preview rig: report, geometry, vocabulary check and page opening."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

WIDE = [
    {"name": "wide-en-light", "width": 1100, "height": 700, "lang": "en", "theme": "light"},
    {"name": "wide-es-dark", "width": 1100, "height": 700, "lang": "es", "theme": "dark"},
]
RESPONSIVE = [
    {"name": "narrow-en-dark", "width": 360, "height": 640, "lang": "en", "theme": "dark"},
    {"name": "narrow-es-light", "width": 360, "height": 640, "lang": "es", "theme": "light"},
    {"name": "frame-en-light", "width": 300, "height": 420, "lang": "en", "theme": "light"},
    {"name": "frame-es-dark", "width": 300, "height": 420, "lang": "es", "theme": "dark"},
]
# 200% and 400% browser zoom of a 1100x700 window are, for the page, viewports of 550x350 and 275x175 CSS px; the second is
# rounded to the 320 px width of WCAG reflow.
ZOOM = [
    {"name": "zoom200-en-light", "width": 550, "height": 350, "lang": "en", "theme": "light"},
    {"name": "zoom400-es-dark", "width": 320, "height": 256, "lang": "es", "theme": "dark"},
]
EXTRA_VOCAB = {"tab", "esc", "alt", "meridian", "r", "z", "cv", "ai", "ia", "acme", "us", "s", "m", "shift", "ctrl", "y"}
# month names come from Intl in the chosen language (a recorded date), not from the dictionary
EXTRA_VOCAB |= {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec"}
EXTRA_VOCAB |= {"ene", "abr", "ago", "dic"}


@dataclass
class Report:
    failures: list[str] = field(default_factory=list)
    checks: int = 0
    scenes: int = 0
    shots: list[tuple[str, str, Path]] = field(default_factory=list)
    min_contrast: float = 99.0

    def check(self, ok: bool, message: str) -> None:
        self.checks += 1
        if not ok:
            self.failures.append(message)

    def fail(self, message: str) -> None:
        self.checks += 1
        self.failures.append(message)


# What a content script sees of the extension runtime: it can message the worker (the worker keeps the panel's look), but
# extension storage is closed to it (storage.local access level TRUSTED_CONTEXTS), so every storage call rejects and is
# counted. `__bgMode.fail` = "reject" | "orphan" simulates an unreachable worker / a reloaded extension;
# `__bgMode.delayGet` delays ui:get answers. The ui:get/ui:set handling mirrors storage.ts normalizeUiPrefs.
FAKE_RUNTIME = """
(() => {
  const KEY = "__fake_worker_ui";
  const KEYRE = /^[A-Za-z0-9_:.-]{1,40}$/;
  window.__bgLog = [];
  window.__bgStats = { storageTouches: 0 };
  window.__bgMode = { fail: null, delayGet: 0 };
  const normalize = (raw) => {
    const src = raw && typeof raw === "object" && !Array.isArray(raw) ? raw : {};
    const dismissed = {};
    const entries = src.dismissed && typeof src.dismissed === "object" && !Array.isArray(src.dismissed) ? Object.entries(src.dismissed) : [];
    for (const [k, v] of entries) {
      if (Object.keys(dismissed).length >= 24) break;
      if (KEYRE.test(k) && typeof v === "number" && Number.isFinite(v)) dismissed[k] = v;
    }
    return { dock: src.dock === "left" ? "left" : "right", dismissed };
  };
  const denied = () => { window.__bgStats.storageTouches += 1; return Promise.reject(new Error("Access to storage is not allowed from this context.")); };
  window.chrome = {
    storage: { local: { get: denied, set: denied, remove: denied } },
    runtime: {
      id: "rig-extension",
      onMessage: { addListener() {}, removeListener() {} },
      sendMessage(msg) {
        window.__bgLog.push(JSON.parse(JSON.stringify(msg)));
        const mode = window.__bgMode;
        if (mode.fail === "orphan") return Promise.reject(new Error("Extension context invalidated."));
        if (mode.fail === "reject") return Promise.reject(new Error("The message port closed before a response was received."));
        const answer = () => {
          if (msg.t === "ui:get") return { ok: true, data: normalize(JSON.parse(localStorage.getItem(KEY) || "null")) };
          if (msg.t === "ui:set") { const clean = normalize(msg.value); localStorage.setItem(KEY, JSON.stringify(clean)); return { ok: true, data: clean }; }
          return { ok: false, error: { code: "invalid", detail: "unknown request" } };
        };
        const wait = msg.t === "ui:get" ? mode.delayGet : 0;
        return new Promise((resolve) => setTimeout(() => resolve(answer()), wait));
      },
    },
  };
})();
"""


# ---- helpers --------------------------------------------------------------------------------------------------------
def overlap(a: dict[str, float], b: dict[str, float]) -> float:
    width = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    height = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return max(0.0, width) * max(0.0, height)


def words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.lower())


def leaked(texts: list[str], data: list[str], vocabulary: list[str]) -> list[str]:
    allowed = set(vocabulary) | EXTRA_VOCAB
    data_words: set[str] = set()
    for item in data:
        data_words.update(words(item))
    allowed |= data_words
    bad: set[str] = set()
    for original in texts:
        text = original
        for item in sorted(data, key=len, reverse=True):
            if item and item in text:
                text = re.sub(rf"(?<!\w){re.escape(item)}(?!\w)", " ", text)
        for word in words(text):
            if word.isdigit() or word in allowed:
                continue
            if len(word) >= 2 and any(known.startswith(word) for known in data_words):  # truncated employer text
                continue
            bad.add(f"{word} (in {original[:60]!r})")
    return sorted(bad)


async def open_page(
    browser,
    base: str,
    cfg: dict,
    *,
    bundle: str = "open",
    path: str = "rig.html",
    extra: str = "",
    init: str | None = None,
    reduced_motion: bool = False,
):
    context = await browser.new_context(
        viewport={"width": cfg["width"], "height": cfg["height"]},
        device_scale_factor=2,
        color_scheme=cfg["theme"],
        locale="en-GB" if cfg["lang"] == "en" else "es-ES",
        reduced_motion="reduce" if reduced_motion else "no-preference",
    )
    await context.add_init_script(FAKE_RUNTIME)
    if init:
        await context.add_init_script(init)
    page = await context.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda exc: errors.append(f"pageerror: {exc}"))
    page.on(
        "console",
        lambda msg: errors.append(f"console.{msg.type}: {msg.text}") if msg.type in ("error", "warning") else None,
    )
    await page.goto(f"{base}/{path}?theme={cfg['theme']}&bundle={bundle}{extra}")
    await page.wait_for_selector("html[data-rig=ready]", state="attached")
    page.rig_errors = errors  # type: ignore[attr-defined]
    return context, page
