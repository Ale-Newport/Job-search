"""Explicit local CLI workflows included in the signed backend sidecar."""

import argparse
import sys


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(
        prog="meridian-backend --meridian-calibration",
        description="Run authorized calibration with shared usage accounting; never submit applications.",
    )
    parser.add_argument("mode", choices=("answers", "form", "mail"))
    mode = parser.parse_args(arguments[:1]).mode
    if mode == "answers":
        from .answers import main as run
    elif mode == 'form':
        from .form import main as run
    else:
        from .mail import main as run
    return run(arguments[1:])
