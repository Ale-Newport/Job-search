"""Native approval of a browser pairing: a macOS code-entry dialog plus a passive notification.

Why a code and not an Approve button: a stray Return key (or any process injecting key events) must never be able to
connect a browser. The extension shows a six-digit code; the dialog has no default Approve and only a correct code
typed by the user connects. The in-app Approve button (``/api/extension/pairings/{id}/approve``) is the parallel path;
whichever decision arrives first wins and cancels the other (see ``pairing.PairingManager``).

* ``osascript`` receives the script and every value as separate ``argv`` entries, so attacker-controlled text (the
  extension's name, its origin) is displayed literally and can never be interpreted as AppleScript.
* The dialog runs as a separate detached process (``stdin=DEVNULL``, own session): it works when the Meridian window
  is hidden and is killed when the pairing is decided elsewhere or the sidecar shuts down.
* Wrong codes: at most three (the caller persists them), an empty answer is ignored, 90 seconds timeout.
* Skipped (``dialog_allowed``) when the platform is not macOS, under ``MERIDIAN_TEST``/``MERIDIAN_NO_NOTIFICATIONS``,
  or when the setting ``extension_pairing_dialog`` is off. The in-app approval always remains available.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import logging
import os
import re
import sys
from pathlib import Path

logger = logging.getLogger("meridian.bridge")

OSASCRIPT = "/usr/bin/osascript"
OPEN = "/usr/bin/open"
MAX_WRONG = 3
CODE_DIALOG = """on run argv
	set theTitle to item 1 of argv
	set theMessage to item 2 of argv
	set giveUp to (item 3 of argv) as integer
	tell current application to activate
	try
		set r to display dialog theMessage with title theTitle default answer "" buttons {"Deny", "Connect"} default button "Connect" cancel button "Deny" with icon note giving up after giveUp
		if gave up of r then return "timeout"
		return "code:" & (text returned of r)
	on error number n
		if n is -128 then return "deny"
		error number n
	end try
end run"""


def _clean(text: str, limit: int) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[\x00-\x1f\x7f]", " ", text or "")).strip()[:limit]


def quiet() -> bool:
    return bool(os.environ.get("MERIDIAN_TEST") or os.environ.get("MERIDIAN_NO_NOTIFICATIONS"))


def dialog_allowed(setting_enabled: bool = True) -> bool:
    """True when the native dialog may be shown on this machine right now."""
    return sys.platform == "darwin" and setting_enabled and not quiet()


def build_message(client_name: str, browser: str, origin: str, recognised: bool = True) -> str:
    lines = [
        f"{_clean(client_name, 60) or 'A browser extension'} ({_clean(browser, 20)}) wants to connect to Meridian.",
        "",
        f"Extension: {_clean(origin, 80)}",
    ]
    if not recognised:
        lines += ["", "UNRECOGNISED EXTENSION: this is not the Meridian extension that ships with this app. "
                      "Only continue if you loaded it yourself."]
    lines += ["", "Type the 6-digit code shown in the extension to allow it."]
    return "\n".join(lines)


async def _spawn(script: str, title: str, message: str, seconds: int):
    return await asyncio.create_subprocess_exec(
        OSASCRIPT, "-e", script, title, message, str(seconds),
        stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )


async def ask_for_code(
    client_name: str,
    browser: str,
    origin: str,
    expected: str,
    *,
    seconds: int = 90,
    max_wrong: int = MAX_WRONG,
    recognised: bool = True,
    on_wrong=None,
    spawn=None,
) -> str:
    """-> 'approved' | 'denied' | 'timeout' | 'unavailable'.

    A wrong code re-prompts (``on_wrong`` is called for each), an empty answer is ignored, Deny and Esc deny. The
    subprocess is killed when this coroutine is cancelled (decision taken elsewhere, or shutdown)."""
    message = build_message(client_name, browser, origin, recognised)
    spawn = spawn or _spawn
    wrong = 0
    deadline = asyncio.get_running_loop().time() + seconds
    while wrong < max_wrong:
        remaining = int(deadline - asyncio.get_running_loop().time())
        if remaining < 2:
            return "timeout"
        try:
            process = await spawn(CODE_DIALOG, "Meridian", message, remaining)
        except OSError:
            return "unavailable"
        try:
            out, _ = await asyncio.wait_for(process.communicate(), remaining + 10)
        except asyncio.CancelledError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            raise
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            return "timeout"
        answer = (out or b"").decode(errors="replace").strip()
        if answer.startswith("code:"):
            # ASCII digits only: ``\D`` would keep digits of other scripts, and hmac.compare_digest refuses to compare
            # a non-ASCII str (it raises TypeError), which silently ended the whole dialog.
            typed = re.sub(r"[^0-9]", "", answer[5:])
            if not typed:
                continue  # a stray Return must not burn an attempt
            if hmac.compare_digest(typed, expected):
                return "approved"
            wrong += 1
            if on_wrong is not None:
                on_wrong()
            continue
        return "denied" if answer == "deny" else "timeout" if answer == "timeout" else "unavailable"
    return "denied"


async def announce(db, browser: str) -> None:
    """Passive hint that a pairing is waiting. The notification never contains the code (lock-screen exposure)."""
    label = {"chrome": "Chrome", "safari": "Safari"}.get(browser, "A browser")
    try:
        from ..main import notify

        await notify("Meridian", f"{label} wants to connect to Meridian. Open Meridian to approve.", db)
    except Exception:
        logger.info("pairing_notification_unavailable")
    await bring_app_forward()


async def bring_app_forward() -> None:
    """Raise the Meridian window (Tauri maps a Reopen to show-window). Only a packaged app sets MERIDIAN_APP_BUNDLE."""
    bundle = os.environ.get("MERIDIAN_APP_BUNDLE", "")
    if quiet() or sys.platform != "darwin" or not bundle:
        return
    path = Path(bundle)
    if path.suffix != ".app" or not path.is_dir():
        return
    try:
        process = await asyncio.create_subprocess_exec(
            OPEN, str(path), stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL, start_new_session=True,
        )
        await asyncio.wait_for(process.wait(), timeout=5)
    except (OSError, asyncio.TimeoutError):
        logger.info("app_activation_unavailable")
