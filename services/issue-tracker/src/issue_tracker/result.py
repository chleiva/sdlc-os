"""The F3 MCP result envelope: ok / empty / error, mutually exclusive.

Mirrors services/run-registry/src/run_registry/result.py's convention so
the two "real" services in this repo agree on the shape. See
services/mcp-stubs/issue-tracker/schema/issue-tracker.schema.json for the
JSON Schema this must serialize to exactly (`to_wire`).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar

from issue_tracker.errors import RETRYABLE, ErrorCode

T = TypeVar("T")


class Outcome(str, Enum):
    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"


@dataclass(frozen=True)
class ErrorInfo:
    code: ErrorCode
    message: str
    retry_after_seconds: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Result(Generic[T]):
    outcome: Outcome
    data: T | None = None
    reason: str | None = None
    error: ErrorInfo | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def ok(data: dict[str, Any]) -> "Result":
        return Result(outcome=Outcome.OK, data=data)

    @staticmethod
    def empty(reason: str, **extra: Any) -> "Result":
        return Result(outcome=Outcome.EMPTY, reason=reason, extra=extra)

    @staticmethod
    def fail(code: ErrorCode, message: str, *, retry_after_seconds: int | None = None, details: dict | None = None) -> "Result":
        return Result(
            outcome=Outcome.ERROR,
            error=ErrorInfo(
                code=code,
                message=message,
                retry_after_seconds=retry_after_seconds if code == ErrorCode.RATE_LIMITED else None,
                details=details or {},
            ),
        )

    @property
    def is_ok(self) -> bool:
        return self.outcome is Outcome.OK

    @property
    def is_empty(self) -> bool:
        return self.outcome is Outcome.EMPTY

    @property
    def is_error(self) -> bool:
        return self.outcome is Outcome.ERROR

    def to_wire(self) -> dict[str, Any]:
        """Render as the plain-dict shape the F3 schema's oneOf describes."""
        if self.outcome is Outcome.OK:
            return {"outcome": "ok", "data": self.data}
        if self.outcome is Outcome.EMPTY:
            out: dict[str, Any] = {"outcome": "empty", "reason": self.reason}
            out.update(self.extra)
            return out
        assert self.error is not None
        err: dict[str, Any] = {
            "code": self.error.code.value,
            "message": self.error.message,
            "retryable": RETRYABLE[self.error.code],
            "retry_after_seconds": self.error.retry_after_seconds,
        }
        if self.error.details:
            err["details"] = self.error.details
        return {"outcome": "error", "error": err}
