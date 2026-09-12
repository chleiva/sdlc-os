"""The Registry Service's MCP-shaped result envelope.

Per master spec Section 7.6, every tool contract distinguishes three
outcomes a caller must be able to tell apart without inspecting prose:

  * a success result (`Result.ok`), carrying typed data;
  * a distinct, successful *empty* result (`Result.empty`) -- "nothing
    matched", including the tenant-scoping fail-closed case (Section
    14.13: a query with missing/wrong tenant_id returns nothing, never an
    error that would leak the existence of another tenant's data);
  * a named error condition (`Result.error`), one of the codes in
    `errors.ErrorCode`.

Every RegistryService operation returns a `Result`, never raises a
`RegistryError` across the service boundary -- `service.py` is the only
place that catches those exceptions and turns them into the error branch.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Generic, TypeVar

from run_registry.errors import ErrorCode

T = TypeVar("T")


class Outcome(str, Enum):
    OK = "ok"
    EMPTY = "empty"
    ERROR = "error"


@dataclass(frozen=True)
class ErrorInfo:
    code: ErrorCode
    message: str


@dataclass(frozen=True)
class Result(Generic[T]):
    outcome: Outcome
    data: T | None = None
    error: ErrorInfo | None = None

    @staticmethod
    def ok(data: T) -> "Result[T]":
        return Result(outcome=Outcome.OK, data=data)

    @staticmethod
    def empty() -> "Result[T]":
        return Result(outcome=Outcome.EMPTY)

    @staticmethod
    def fail(code: ErrorCode, message: str) -> "Result[T]":
        return Result(outcome=Outcome.ERROR, error=ErrorInfo(code=code, message=message))

    @property
    def is_ok(self) -> bool:
        return self.outcome is Outcome.OK

    @property
    def is_empty(self) -> bool:
        return self.outcome is Outcome.EMPTY

    @property
    def is_error(self) -> bool:
        return self.outcome is Outcome.ERROR

    def to_wire(self, data_encoder: Any = None) -> dict[str, Any]:
        """Render as the plain-dict shape sent over the MCP transport."""
        if self.outcome is Outcome.OK:
            data = self.data
            if data_encoder is not None:
                data = data_encoder(data)
            return {"outcome": self.outcome.value, "data": data}
        if self.outcome is Outcome.EMPTY:
            return {"outcome": self.outcome.value}
        assert self.error is not None
        return {
            "outcome": self.outcome.value,
            "error": {"code": self.error.code.value, "message": self.error.message},
        }
