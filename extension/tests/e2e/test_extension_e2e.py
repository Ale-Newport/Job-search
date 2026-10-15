"""Real-browser end-to-end suite of the Meridian Assistant extension.

    /Users/alejandro/Projects/Job-search/.venv/bin/python extension/tests/e2e/test_extension_e2e.py \
        [-k NAME] [--repeat N] [--recipe chromium|chrome] [--headed] [--list] [--run-blocked] [--strict] [--junit FILE]

Each scenario gets its own browser profile, fixture servers and, when it needs one, its own REAL backend (temp data
directory, in-memory secrets, fake loopback writing model). Scenarios whose prerequisites are not in this checkout yet
are reported BLOCKED with the workstream that owns the gap (``--run-blocked`` runs them anyway, ``--strict`` makes any
blocked scenario fail the run). Screenshots of failures go to a temp directory that is printed at the end.
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import contextvars
import importlib
import os
import sys
import time
from pathlib import Path

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))

from playwright.async_api import TimeoutError as PlaywrightTimeout  # noqa: E402
from playwright.async_api import async_playwright, expect  # noqa: E402

import harness  # noqa: E402
from scale import SLOWDOWN, millis  # noqa: E402

# Playwright's default of 5 s for ``expect(...)`` is for a quiet machine: a loaded one (other projects' browsers beside the suite, load average
# over 100) needs longer to attach a file or settle a widget. An assertion that holds still passes at once, so a pass is never slower; only a
# failure takes longer to be reported. Explicit ``timeout=`` arguments are unchanged.
EXPECT_TIMEOUT_MS = millis(5_000)  # Playwright's default of 5 s, times the slowdown factor of scale.py (3: 15 s)
expect.set_options(timeout=EXPECT_TIMEOUT_MS)
import scenarios_harness  # noqa: E402,F401  (registers scenarios)
from backend import cleanup_temp, prune_stale_temp, temp_root  # noqa: E402
from framework import (  # noqa: E402
    BLOCKED, FAIL, FINDINGS, PASS, SCENARIOS, SKIP, XFAIL, XPASS, Env, Result, Skip, reap_leftovers, write_junit,
)

for _module in ("scenarios_connection", "scenarios_forms", "scenarios_assist", "scenarios_wave2", "scenarios_journeys", "scenarios_wave5", "scenarios_engine", "scenarios_g5", "scenarios_sec6"):  # register further scenarios
    if (Path(__file__).resolve().parent / f"{_module}.py").exists():
        importlib.import_module(_module)


class TaskOutput:
    """With --jobs > 1 every scenario writes into its own buffer, printed in one piece when it ends."""

    def __init__(self, real) -> None:
        self.real = real
        self.buffer: contextvars.ContextVar[list[str] | None] = contextvars.ContextVar("scenario_output", default=None)

    def write(self, text: str) -> int:
        target = self.buffer.get()
        if target is None:
            return self.real.write(text)
        target.append(text)
        return len(text)

    def flush(self) -> None:
        self.real.flush()

    def __getattr__(self, name):
        return getattr(self.real, name)


def default_jobs() -> int:
    return max(1, min(4, (os.cpu_count() or 2) // 2))


def select(patterns: str) -> list[str]:
    if not patterns:
        return list(SCENARIOS)
    wanted = [p.strip() for p in patterns.split(",") if p.strip()]
    return [name for name in SCENARIOS if any(p in name for p in wanted)]


async def run_buffered(p, args, name: str, iteration: int, output: TaskOutput, gate: asyncio.Semaphore) -> Result:
    async with gate:
        if args.jobs == 1:
            return await run_one(p, args, name, iteration)
        lines: list[str] = []
        output.buffer.set(lines)
        try:
            return await run_one(p, args, name, iteration)
        finally:
            output.buffer.set(None)
            output.real.write("".join(lines))
            output.real.flush()


async def run_one(p, args, name: str, iteration: int) -> Result:
    spec = SCENARIOS[name]
    gaps = harness.missing(spec.needs)
    if gaps and not args.run_blocked:
        return Result(name, BLOCKED, 0.0, "needs " + "; ".join(gaps), iteration=iteration)
    expected_failure = spec.xfail or ("blocked: " + "; ".join(gaps) if gaps else None)
    env = Env(p, args, name)
    started = time.monotonic()
    print(f"[{iteration}/{args.repeat}] {name}" + (f"  (blocked: {'; '.join(gaps)})" if gaps else ""), flush=True)
    result: Result
    try:
        await asyncio.wait_for(spec.fn(env), spec.timeout * SLOWDOWN)
        status = XPASS if expected_failure else PASS
        result = Result(name, status, time.monotonic() - started, expected_failure or "", iteration=iteration)
        print(f"  {'XPASS (flip its xfail)' if expected_failure else 'ok'} ({result.seconds:.1f}s)", flush=True)
    except Skip as why:
        result = Result(name, SKIP, time.monotonic() - started, str(why), iteration=iteration)
        print(f"  SKIPPED: {why}", flush=True)
    except BaseException as exc:  # noqa: BLE001 - every failure kind is a scenario failure
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        message = f"{type(exc).__name__}: {str(exc)[:600]}" if str(exc) else type(exc).__name__
        if isinstance(exc, asyncio.TimeoutError):
            message = f"timed out after {spec.timeout * SLOWDOWN:.0f}s"
        # An expected failure is a PRODUCT failure (assertion / Playwright timeout); anything else is a bug of the test.
        product_failure = isinstance(exc, (AssertionError, PlaywrightTimeout, asyncio.TimeoutError))
        artifacts = await env.failure_artifacts(exc) if not (expected_failure and product_failure) else []
        status = XFAIL if expected_failure and product_failure else FAIL
        result = Result(name, status, time.monotonic() - started, message, artifacts, iteration)
        print(f"  {status.upper()} {name}: {message}", flush=True)
        for artifact in artifacts:
            print(f"    artifact: {artifact}", flush=True)
    finally:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(env.close(), 60)
    return result


def summarise(results: list[Result], elapsed: float) -> int:
    counts = {s: sum(r.status == s for r in results) for s in (PASS, FAIL, BLOCKED, SKIP, XFAIL, XPASS)}
    print("\n" + "=" * 100)
    for result in results:
        mark = {PASS: "PASS   ", FAIL: "FAIL   ", BLOCKED: "BLOCKED", SKIP: "SKIP   ", XFAIL: "XFAIL  ",
                XPASS: "XPASS  "}[result.status]
        detail = f"  {result.detail[:150]}" if result.status != PASS else ""
        print(f"{mark} {result.name:34} {result.seconds:6.1f}s{detail}")
    print("=" * 100)
    if FINDINGS:
        print("KNOWN ISSUES seen while the scenarios ran (xfail rows of expectations.py; each names what to fix):")
        for item in FINDINGS:
            print("  - " + item)
    if harness.SLOW_ACTIVATIONS:
        print(f"SLOW ACTIVATIONS (toolbar click to visible assistant took over 5 s): {len(harness.SLOW_ACTIVATIONS)}, "
              f"slowest {max(harness.SLOW_ACTIVATIONS):.1f}s (the machine was loaded, or the product is slow to start)")
    print("RESULT " + ("FAILED" if counts[FAIL] or counts[XPASS] else "PASSED"),
          " ".join(f"{k}={v}" for k, v in counts.items()), f"known_issues={len(FINDINGS)}", f"total={elapsed:.0f}s")
    return 1 if counts[FAIL] or counts[XPASS] else 0


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-k", default="", help="comma separated substrings of scenario names")
    parser.add_argument("--repeat", type=int, default=1, help="run the selection N times (flakiness check)")
    parser.add_argument("--recipe", default="chromium", choices=["chromium", "chrome"])
    parser.add_argument("--headed", action="store_true")
    parser.add_argument("--jobs", type=int, default=default_jobs(),
                        help="scenarios run in parallel (default %(default)s; each has its own browser and backend)")
    parser.add_argument("--list", action="store_true", help="list scenarios and what each needs, then exit")
    parser.add_argument("--run-blocked", action="store_true", help="run scenarios whose prerequisites are missing")
    parser.add_argument("--strict", action="store_true",
                        help="a BLOCKED scenario or a KNOWN ISSUE counts as a failure (the release gate)")
    parser.add_argument("--junit", default="", help="write a junit xml summary to this file")
    parser.add_argument("--update-golden", action="store_true", help="rewrite fixtures/scan_golden.json (fixtures_scan)")
    args = parser.parse_args()
    names = select(args.k)
    if args.list:
        caps = harness.capabilities()
        for name in names:
            spec = SCENARIOS[name]
            gaps = [need for need in spec.needs if not caps[need][0]]
            print(f"{name:34} {'BLOCKED by ' + ', '.join(gaps) if gaps else 'runnable':40} {spec.doc.splitlines()[0] if spec.doc else ''}")
        return 0
    if not names:
        print("no scenario matches -k", args.k)
        return 2
    results: list[Result] = []
    started = time.monotonic()
    stale = prune_stale_temp()
    if stale:
        print(f"removed {len(stale)} stale e2e temp folder(s) from earlier runs", flush=True)
    print(f"profile/temp root: {temp_root()}  recipe={args.recipe} headed={args.headed} slowdown={SLOWDOWN:g} (MERIDIAN_E2E_SLOWDOWN)", flush=True)
    try:
        output = TaskOutput(sys.stdout)
        sys.stdout = output  # type: ignore[assignment]
        for iteration in range(1, args.repeat + 1):
            async with async_playwright() as p:
                gate = asyncio.Semaphore(max(1, args.jobs))
                batch = await asyncio.gather(*(run_buffered(p, args, name, iteration, output, gate) for name in names))
                results.extend(batch)
    finally:
        sys.stdout = getattr(sys.stdout, "real", sys.stdout)
        leftovers = reap_leftovers()
        if leftovers:
            print("LEFTOVER PROCESSES killed:\n  " + "\n  ".join(leftovers))
        shown = harness._FAILURE_DIR
        harness.cleanup_all()
        cleanup_temp()
        if shown is not None:
            print(f"failure artifacts kept in: {shown}")
    if args.strict:
        for result in results:
            if result.status == BLOCKED:
                result.status = FAIL
    if args.junit:
        write_junit(Path(args.junit), results)
    code = summarise(results, time.monotonic() - started)
    if args.strict and FINDINGS:
        print(f"--strict: {len(FINDINGS)} known issue(s) still open")
        code = code or 1
    if leftovers:
        code = code or 1
    return code


if __name__ == "__main__":
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    sys.exit(asyncio.run(main()))
