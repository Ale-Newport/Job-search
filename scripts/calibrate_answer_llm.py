"""Compatibility CLI for the bundled answer calibration workflow."""

from jobagent.calibration.answers import (
    QUESTIONS as QUESTIONS,
    calibration_usage as calibration_usage,
    configure_from_live as configure_from_live,
    main as main,
    run as run,
)


if __name__ == "__main__":
    main()
