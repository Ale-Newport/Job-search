"""Text that arrives from a page or a form: the characters no label or answer legitimately contains."""

from __future__ import annotations

import re

# C0 and C1 controls except tab / newline / carriage return, DEL, and the bidirectional embedding / override / isolate
# characters (U+202A-202E, U+2066-2069): they hide or reorder what a reader sees, and NUL cuts a string short in
# SQLite functions. Joiners, marks and the left/right marks (U+200C-200F) stay: right-to-left scripts need them.
CONTROLS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")


def strip_controls(value) -> str:
    """``value`` as text without control and bidirectional-override characters (everything else untouched)."""
    return CONTROLS.sub("", value) if isinstance(value, str) else ""
