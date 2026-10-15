"""``PYTHONPATH=backend/tests/fixtures/engine MERIDIAN_TEST=1 .venv/bin/python -m metamorphic [options]``: the whole suite
without pytest, with the report printed (and written to --report)."""
from __future__ import annotations

import argparse
import os
import sys
import time

os.environ.setdefault("MERIDIAN_TEST", "1")
os.environ.setdefault("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")

from . import bases as B  # noqa: E402
from . import driver, plan, report  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="metamorphic")
    parser.add_argument("--budget", type=int, default=plan.BUDGET, help="cases of the main plan (default %(default)s)")
    parser.add_argument("--seed", type=int, default=plan.SEED)
    parser.add_argument("--family", help="only the cases of this family (plain bases and wrapped)")
    parser.add_argument("--group", help="only this wrapper group (and the plain bases)")
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--examples", type=int, default=3, help="examples per failure class in the full listing")
    parser.add_argument("--report", help="write the full report to this file")
    parser.add_argument("--quiet", action="store_true", help="print the summary only")
    args = parser.parse_args(argv)
    cases = plan.build_plan(args.budget, args.seed)
    if args.family:
        cases = [c for c in cases if B.BY_ID[c.base].family == args.family]
    if args.group:
        cases = [c for c in cases if c.rendering is None or c.rendering.group == args.group]
    began = time.perf_counter()
    outcome = driver.execute(cases, log=lambda m: print(m, file=sys.stderr))
    text = report.render(outcome, command=" ".join(["python -m metamorphic", *(argv if argv is not None else sys.argv[1:])]),
                         top=args.top, examples=args.examples, full=not args.quiet)
    if args.report:
        with open(args.report, "w") as handle:
            handle.write(text + "\n")
    print(text)
    print(f"\nwall time {time.perf_counter() - began:.0f}s", file=sys.stderr)
    return 1 if outcome.violations else 0


if __name__ == "__main__":
    raise SystemExit(main())
