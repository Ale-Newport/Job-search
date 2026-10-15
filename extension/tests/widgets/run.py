"""Real-widget compatibility suite for the Meridian Assistant fill engine and scanner.

    cd extension && npm run typecheck && node build.mjs --variant=e2e && ../.venv/bin/python tests/widgets/run.py
        [-k substring[,substring]] [--browser chromium|webkit|all] [--world isolated|main|both] [--no-build] [--list] [--headed] [-x]

The REAL content bundle (e2e build) runs against REAL widget libraries (react-select, Headless UI, MUI, intl-tel-input, ...) and
hand-rolled ATS replicas, driven with the real keyboard through the content rig's mock background (canned suggestions). Chromium
runs the content script in an isolated world, like production; WebKit has no isolated worlds in Playwright (main world).
Exit code 0 only if every widget verdict is PASS or an expected XFAIL (documented hand-off).
Also: tests/widgets/real_run.py (real worker + backend), tests/widgets/bundle-report.mjs (production bundle cost), README.md.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import widget_scenarios as scenarios  # noqa: E402  (registers the scenarios)
from widget_rig import EXTENSION, Outcome, Recorder, Servers, WidgetRig  # noqa: E402

OWN_BUILD = EXTENSION / "dist" / "widgets-e2e"
SCENARIO_TIMEOUT = 120.0


def unit_tests() -> bool:
    """The pure helpers (phone numbers a widget rewrites, combobox placeholders): fast, run first."""
    result = subprocess.run(["node", "--import", "./tests/unit/register.mjs", "--test", "tests/widgets/unit/*.test.mjs"], cwd=EXTENSION, capture_output=True, text=True)
    tail = [line for line in result.stdout.splitlines() if line.startswith(("ℹ pass", "ℹ fail"))]
    print("unit tests: " + (" ".join(tail) if tail else result.stdout[-300:]), flush=True)
    if result.returncode != 0:
        print(result.stdout[-2000:] + result.stderr[-1000:])
    return result.returncode == 0


def build() -> Path:
    """The suite builds its OWN e2e bundle (other workstreams rebuild dist/e2e at any time)."""
    result = subprocess.run(["node", "build.mjs", "--variant=e2e", f"--out={OWN_BUILD.relative_to(EXTENSION)}"], cwd=EXTENSION, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout + result.stderr)
        raise SystemExit("e2e build failed")
    helpers = subprocess.run(["node", "tests/widgets/build-helpers.mjs", str(OWN_BUILD / "visibility-test.js"), "."], cwd=EXTENSION, capture_output=True, text=True)
    if helpers.returncode != 0:
        print(helpers.stdout + helpers.stderr)
        raise SystemExit("helper bundle build failed")
    return OWN_BUILD / "content.js"


async def run_pass(browser: str, world: str, selected: list[scenarios.Scenario], servers: Servers, content_js: Path, recorder: Recorder,
                   stop_first: bool, headed: bool) -> None:
    rig = WidgetRig(browser, servers, content_js, headless=not headed)
    await rig.start()
    try:
        for sc in selected:
            label = f"{browser}/{world}:{sc.name}"
            print(f"  {label}", flush=True)
            started = time.time()
            ctx = scenarios.Ctx(rig, recorder, f"{browser}/{world}:{sc.name}", world)
            before = len(recorder.outcomes)
            try:
                await asyncio.wait_for(sc.fn(ctx), SCENARIO_TIMEOUT)
            except Exception as exc:  # noqa: BLE001
                recorder.add(Outcome(ctx.scenario, "(scenario crashed)", "FAIL", f"{type(exc).__name__}: {exc}"[:500]))
            finally:
                await rig.reset()
            bad = [o for o in recorder.outcomes[before:] if o.status in ("FAIL", "XPASS")]
            print(f"  {label} done in {time.time() - started:.1f}s", flush=True)
            if stop_first and bad:
                break
    finally:
        await rig.stop()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("-k", dest="keyword", default="")
    parser.add_argument("--browser", default="chromium", choices=["chromium", "webkit", "all"])
    parser.add_argument("--world", default="isolated", choices=["isolated", "main", "both"])
    parser.add_argument("--no-build", action="store_true")
    parser.add_argument("--content-js", default="", help="use this content.js instead of building one")
    parser.add_argument("-x", dest="stop_first", action="store_true")
    parser.add_argument("--skip-unit", action="store_true", help="do not run the node unit tests of the pure helpers first")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--json", default="", help="write the verdict table to this file")
    args = parser.parse_args()
    if args.list:
        for sc in scenarios.SCENARIOS:
            print(f"{sc.name}{'' if sc.webkit else '  (chromium only)'}")
        return 0
    if not args.skip_unit and not args.keyword and not unit_tests():
        return 1
    if args.content_js:
        content_js = Path(args.content_js)
    elif args.no_build:
        content_js = OWN_BUILD / "content.js"
        if not content_js.exists():
            raise SystemExit("no previous build: run without --no-build")
    else:
        content_js = build()
    servers = Servers()
    recorder = Recorder()
    started = time.time()
    try:
        browsers = ["chromium", "webkit"] if args.browser == "all" else [args.browser]
        for browser in browsers:
            worlds = ["isolated", "main"] if args.world == "both" else [args.world]
            if browser == "webkit":
                worlds = ["main"]  # Playwright WebKit has no isolated worlds
            for world in worlds:
                keys = [k for k in args.keyword.split(",") if k]
                selected = [s for s in scenarios.SCENARIOS if (not keys or any(k in s.name for k in keys)) and (browser == "chromium" or s.webkit)]
                await run_pass(browser, world, selected, servers, content_js, recorder, args.stop_first, args.headed)
    finally:
        servers.close()
    counts: dict[str, int] = {}
    for o in recorder.outcomes:
        counts[o.status] = counts.get(o.status, 0) + 1
    print("\n=== widget verdicts ===")
    for o in recorder.outcomes:
        if o.status != "PASS":
            print(f"{o.status:5} {o.scenario} :: {o.widget}\n        {o.detail}" + (f"\n        hand-off: {o.handoff}" if o.handoff else ""))
    print(f"\n{counts}  total={len(recorder.outcomes)}  wall={time.time() - started:.0f}s")
    if args.json:
        Path(args.json).write_text(json.dumps([o.__dict__ for o in recorder.outcomes], indent=1))
    bad = counts.get("FAIL", 0) + counts.get("XPASS", 0)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
