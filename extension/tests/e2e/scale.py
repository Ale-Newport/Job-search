"""How much slower than a quiet machine this run should be patient.

The waits of the suite were chosen on a quiet machine. Beside other projects' browsers (a load average of 70-190 was measured while this suite
ran) the same product needs several times longer to start a backend, answer the first suggestions or attach a file, and a wait that is only
long enough for a quiet machine fails scenarios that have nothing wrong. Every wait that is an EVENT wait (it returns the moment its condition
holds, so a pass is never slower) is multiplied by ``SLOWDOWN``: ``flows.poll_until`` and its default, the default of every ``expect(...)``,
the chip budget ``ui.DEFAULT_TIMEOUT`` and the per-scenario time limit. Assertions are never weakened; a real failure is only reported later.

``MERIDIAN_E2E_SLOWDOWN=1`` gives the original speeds (a quiet CI machine); the default is 3.
"""

from __future__ import annotations

import os


def _factor() -> float:
    try:
        return max(1.0, float(os.environ.get("MERIDIAN_E2E_SLOWDOWN", "3")))
    except ValueError:
        return 3.0


SLOWDOWN = _factor()


def seconds(value: float) -> float:
    return value * SLOWDOWN


def millis(value: int) -> int:
    return int(value * SLOWDOWN)
