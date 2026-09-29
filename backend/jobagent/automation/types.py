"""The decision boundary: the model may choose only an operation and observed index."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
import math
from typing import Any


class Operation(StrEnum):
    CLICK = "CLICK"
    TYPE_TEXT = "TYPE_TEXT"
    SELECT = "SELECT"
    CHECK = "CHECK"
    UNCHECK = "UNCHECK"
    UPLOAD = "UPLOAD"
    SCROLL_UP = "SCROLL_UP"
    SCROLL_DOWN = "SCROLL_DOWN"
    OPEN_TAB = "OPEN_TAB"
    CLOSE_TAB = "CLOSE_TAB"
    WAIT = "WAIT"
    BACK = "BACK"
    DONE = "DONE"
    BLOCKED = "BLOCKED"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


TARGET_OPERATIONS = {
    Operation.CLICK, Operation.TYPE_TEXT, Operation.SELECT, Operation.CHECK,
    Operation.UNCHECK, Operation.UPLOAD, Operation.OPEN_TAB,
}


class AutomationError(RuntimeError):
    code = "automation_error"


class StaleState(AutomationError):
    code = "stale_state"


class HumanRequired(AutomationError):
    code = "human_required"


class Paused(AutomationError):
    code = "paused"


class InvalidDecision(AutomationError):
    code = "invalid_decision"


@dataclass(frozen=True)
class Decision:
    operation: Operation
    target: int | None = None
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if not isinstance(self.operation, Operation):
            object.__setattr__(self, "operation", Operation(self.operation))
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)):
            raise InvalidDecision("Confidence must be a finite number.")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise InvalidDecision("Confidence must be between zero and one.")
        if self.operation in TARGET_OPERATIONS and (type(self.target) is not int or self.target < 1):
            raise InvalidDecision("The operation requires an observed element index.")
        if self.operation not in TARGET_OPERATIONS and self.target is not None:
            raise InvalidDecision("This operation does not accept a target.")

    @classmethod
    def from_dict(cls, data: dict) -> "Decision":
        if set(data) - {"operation", "target", "confidence", "metadata"}:
            raise InvalidDecision("Decision contains unsupported executable data.")
        try:
            return cls(**data)
        except (TypeError, ValueError) as error:
            raise InvalidDecision(str(error)) from error
