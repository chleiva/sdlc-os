"""The source-control service's MCP-shaped result envelope, matching the
three-way `outcome` discriminant (`ok` / `empty` / `error`) that
services/mcp-stubs/source-control/schema/source-control.schema.json's
oneOf requires for every tool's output_schema (spec Section 7.6).

`SourceControlService` (service.py) is the only place that catches
`errors.SourceControlError` and turns it into the error branch here --
callers of the service never see a raw exception.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Generic, TypeVar

from source_control.errors import ErrorCode, SourceControlError, is_retryable

T = TypeVar("T")


class Outcome(str, Enum):
    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"


@dataclass(frozen=True)
class ErrorInfo:
    code: ErrorCode
    message: str
    retryable: bool
    retry_after_seconds: int | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Result(Generic[T]):
    outcome: Outcome
    data: T | None = None
    reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    error: ErrorInfo | None = None

    @staticmethod
    def ok(data: T) -> "Result[T]":
        return Result(outcome=Outcome.OK, data=data)

    @staticmethod
    def empty(reason: str, **extra: Any) -> "Result[T]":
        return Result(outcome=Outcome.EMPTY, reason=reason, extra=extra)

    @staticmethod
    def fail(code: ErrorCode, message: str, *, retry_after_seconds: int | None = None, details: dict | None = None) -> "Result[T]":
        return Result(
            outcome=Outcome.ERROR,
            error=ErrorInfo(
                code=code,
                message=message,
                retryable=is_retryable(code),
                retry_after_seconds=retry_after_seconds,
                details=details or {},
            ),
        )

    @staticmethod
    def from_exception(exc: SourceControlError) -> "Result[T]":
        return Result.fail(
            exc.code,
            exc.message,
            retry_after_seconds=exc.retry_after_seconds,
            details=exc.details,
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
        """Render as the plain-dict shape the F3 schema's output_schema
        expects: exactly one of the three mutually-exclusive branches."""
        if self.outcome is Outcome.OK:
            return {"outcome": "ok", "data": self.data}
        if self.outcome is Outcome.EMPTY:
            payload: dict[str, Any] = {"outcome": "empty", "reason": self.reason}
            payload.update(self.extra)
            return payload
        assert self.error is not None
        err: dict[str, Any] = {
            "code": self.error.code.value,
            "message": self.error.message,
            "retryable": self.error.retryable,
            "retry_after_seconds": self.error.retry_after_seconds,
        }
        if self.error.details:
            err["details"] = self.error.details
        return {"outcome": "error", "error": err}
