"""Run the desktop UI end-to-end set, every script against its OWN isolated stack (see live_stack.py): the real backend on a
temporary data directory plus a vite dev server proxying to it. Nothing here touches real user data, the real Meridian.app,
the Keychain or a employer site, and nothing is left running.

  .venv/bin/python frontend/tests/run_ui_suite.py                     # everything below
  .venv/bin/python frontend/tests/run_ui_suite.py --only ui_smoke,extension_chrome
  .venv/bin/python frontend/tests/run_ui_suite.py --list

Steps: ui_smoke, workflow_e2e, onboarding_e2e, recovery_e2e, networking_e2e (real backend, one fresh stack each),
extension_chrome and extension_webkit (the mocked panel test: only the page is real), extension_live and
extension_live_webkit (the panel against the real backend and bridge; each builds its own stack).
Exit code 0 only when every selected step passed. Machine load can be high: a step has a generous timeout and the log of
every step is kept in --out (default: a temporary folder, printed at the end).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import live_stack

HERE = Path(__file__).resolve().parent
PYTHON = live_stack.REPO / ".venv" / "bin" / "python"
STEP_TIMEOUT = 20 * 60

# name -> (script, extra arguments, needs the real backend behind vite)
STEPS = {
    "ui_smoke": ("ui_smoke.py", [], True),
    "workflow_e2e": ("workflow_e2e.py", [], True),
    "onboarding_e2e": ("onboarding_e2e.py", [], True),
    "recovery_e2e": ("recovery_e2e.py", [], True),
    "networking_e2e": ("networking_e2e.py", [], True),
    "extension_chrome": ("extension_e2e.py", ["--browser", "chrome"], False),
    "extension_webkit": ("extension_e2e.py", ["--browser", "webkit"], False),
    "extension_live": ("extension_e2e.py", ["--live", "--browser", "chrome"], None),
    "extension_live_webkit": ("extension_e2e.py", ["--live", "--browser", "webkit"], None),
}


def run_script(script: str, arguments: list[str], log: Path) -> tuple[int, float]:
    started = time.time()
    with open(log, "wb") as handle:
        try:
            result = subprocess.run(
                [str(PYTHON), str(HERE / script), *arguments], stdout=handle, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                timeout=STEP_TIMEOUT, cwd=str(live_stack.REPO), env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
            code = result.returncode
        except subprocess.TimeoutExpired:
            handle.write(f"\nTIMEOUT after {STEP_TIMEOUT} s\n".encode())
            code = 124
    return code, time.time() - started


async def run_step(name: str, out: Path) -> tuple[int, float]:
    script, extra, real_backend = STEPS[name]
    log = out / f"{name}.log"
    if real_backend is None:  # the step builds its own stack
        return await asyncio.to_thread(run_script, script, [*extra, "--out", str(out)], log)
    if real_backend is False:  # only the page is real: a vite server is enough (every /api call is mocked)
        work = Path(tempfile.mkdtemp(prefix="meridian-ui-mock-"))
        vite = live_stack.ViteServer(api_port=1, work=work)
        try:
            await asyncio.to_thread(vite.start)
            return await asyncio.to_thread(run_script, script, [*extra, "--base-url", vite.url, "--out", str(out)], log)
        finally:
            await asyncio.to_thread(vite.stop)
            import shutil

            shutil.rmtree(work, ignore_errors=True)
    async with live_stack.LiveStack(profile=False, seed=False) as stack:
        code, seconds = await asyncio.to_thread(run_script, script, [*extra, "--base-url", stack.base_url], log)
        if code != 0:
            (out / f"{name}.stack.log").write_text(stack.logs())
        return code, seconds


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--only", default="", help="comma separated step names")
    parser.add_argument("--skip", default="", help="comma separated step names")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--out", default="", help="folder for the logs and screenshots")
    args = parser.parse_args()
    if args.list:
        print("\n".join(STEPS))
        return 0
    chosen = [n for n in (args.only.split(",") if args.only else STEPS) if n]
    unknown = [n for n in chosen + args.skip.split(",") if n and n not in STEPS]
    if unknown:
        print(f"unknown step(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    chosen = [n for n in chosen if n not in args.skip.split(",")]
    out = Path(args.out) if args.out else Path(tempfile.mkdtemp(prefix="meridian-ui-suite-"))
    out.mkdir(parents=True, exist_ok=True)
    results = []
    for name in chosen:
        print(f"== {name}", flush=True)
        try:
            code, seconds = await run_step(name, out)
        except Exception as exc:  # noqa: BLE001 - a stack that cannot start is a failed step, not a crashed runner
            (out / f"{name}.log").write_text(f"stack failed: {type(exc).__name__}: {exc}\n")
            code, seconds = 1, 0.0
        results.append((name, code, seconds))
        print(f"   {'passed' if code == 0 else 'FAILED (exit %d)' % code} in {seconds:.0f} s", flush=True)
        if code != 0:
            tail = (out / f"{name}.log").read_text(errors="replace").splitlines()[-25:]
            print("   " + "\n   ".join(tail), flush=True)
    print("\nSummary")
    for name, code, seconds in results:
        print(f"  {name:18} {'passed' if code == 0 else 'FAILED':7} {seconds:6.0f} s")
    print(f"logs and screenshots: {out}")
    return 0 if all(code == 0 for _, code, _ in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
