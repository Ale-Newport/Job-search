"""Run the content-script rig (Playwright, real keyboard, mock extension background).

    cd extension && npm run typecheck && node build.mjs --variant=e2e && \
        ../.venv/bin/python tests/content/run.py [-k substring] [--browser chromium|webkit|all] [--no-build] [-x] [--list]

Every scenario opens its own browser context; nothing touches real user data and no network beyond 127.0.0.1/localhost
fixture servers on OS-assigned ports is used. The same scenarios run under bundled Chromium (`channel="chromium"`) and
Playwright WebKit; native Tab differs between engines (Safari-like WebKit does not Tab to buttons), the assertions that
depend on it say so.

`-k a,b` selects scenarios whose names contain any of the comma-separated substrings. `MERIDIAN_CONTENT_JS=<dir>/content.js`
points the rig (and the build step) at a private e2e bundle, so parallel work never races on extension/dist/e2e.

The pure rules (eligibility in state.ts, the Alt+letter keys in util.ts) have a browser-free test:
`cd extension && node --import ./tests/unit/register.mjs --test tests/content/logic.test.mjs`.

Optional: `MERIDIAN_REACT_UMD_DIR=<folder with react.development.js and react-dom.development.js>` also runs the form
against REAL React 18 (the repository does not vendor React; the default React-like fixture emulates its value tracking).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import time
import traceback
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import scenarios  # noqa: E402  (registers the scenarios)
from harness import CONTENT_JS, EXT, Rig, Servers  # noqa: E402

SCENARIO_TIMEOUT = 90.0


def build() -> None:
    command = ["node", "build.mjs", "--variant=e2e"]
    if os.environ.get("MERIDIAN_CONTENT_JS"):  # a private build directory (the content bundle sits in it)
        command.append(f"--out={CONTENT_JS.parent}")
    result = subprocess.run(command, cwd=EXT, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout + result.stderr)
        raise SystemExit("e2e build failed")
    if not CONTENT_JS.exists():
        raise SystemExit(f"missing {CONTENT_JS}")


async def run_browser(name: str, selected: list[scenarios.Scenario], servers: Servers, stop_first: bool, headed: bool) -> list[tuple[str, str, bool, float, str]]:
    results: list[tuple[str, str, bool, float, str]] = []
    rig = Rig(name, servers, headless=not headed)
    await rig.start()
    try:
        for sc in selected:
            started = time.time()
            ctx = scenarios.Ctx(rig, servers)
            try:
                await asyncio.wait_for(sc.fn(ctx), SCENARIO_TIMEOUT)
                results.append((name, sc.name, True, time.time() - started, ""))
                print(f"PASS  {name:8} {sc.name}  ({time.time() - started:.1f}s)", flush=True)
            except BaseException as exc:  # noqa: BLE001 - report every failure kind, including timeouts
                detail = "".join(traceback.format_exception_only(type(exc), exc)).strip()
                tb = traceback.format_exc().strip().splitlines()
                results.append((name, sc.name, False, time.time() - started, detail))
                print(f"FAIL  {name:8} {sc.name}  ({time.time() - started:.1f}s)\n      {detail}", flush=True)
                print("      " + "\n      ".join(tb[-6:]), flush=True)
                if isinstance(exc, KeyboardInterrupt):
                    raise
                if stop_first:
                    break
            finally:
                await rig.reset()
    finally:
        await rig.stop()
    return results


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-k", dest="keyword", default="", help="comma-separated substrings of scenario names")
    parser.add_argument("--browser", default="all", choices=["chromium", "webkit", "all"])
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("-x", dest="stop_first", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--headed", action="store_true")
    args = parser.parse_args()
    if args.list:
        for sc in scenarios.SCENARIOS:
            print(f"{sc.name}{'' if sc.webkit else '  (chromium only)'}")
        return 0
    if not args.no_build:
        build()
    elif not CONTENT_JS.exists():
        raise SystemExit("run `node build.mjs --variant=e2e` first")
    servers = Servers()
    results: list[tuple[str, str, bool, float, str]] = []
    try:
        for browser in (["chromium", "webkit"] if args.browser == "all" else [args.browser]):
            needles = [k for k in args.keyword.split(",")]
            selected = [s for s in scenarios.SCENARIOS if any(k in s.name for k in needles) and (browser == "chromium" or s.webkit)]
            results += await run_browser(browser, selected, servers, args.stop_first, args.headed)
    finally:
        servers.close()
    failed = [r for r in results if not r[2]]
    print(f"\n{len(results) - len(failed)} passed, {len(failed)} failed, {len(results)} total")
    for browser, name, _, _, detail in failed:
        print(f"  FAILED {browser}:{name}: {detail}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
