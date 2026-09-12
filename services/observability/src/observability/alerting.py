"""Alerting, not babysitting (master spec Section 16.4): "Section 9.3's
checkpoints, Section 18's stuck-detection, and Section 16.2's budget
thresholds push to the team's existing alerting channel (Slack,
PagerDuty, or equivalent) rather than requiring a human to keep a
dashboard open."

`AlertSink` is the integration seam a real deployment implements against
a real channel (a PagerDuty Events API call, a Slack `chat.postMessage`,
etc.) -- there is no live alerting channel in this environment, so
`InMemoryAlertSink`/`FileAlertSink` below are real, working stand-ins:
every alert genuinely is pushed and genuinely is readable back, just not
delivered to an actual paging system.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class Alert:
    kind: str  # e.g. "checkpoint_stuck" | "checkpoint_time_cost" | "checkpoint_risk" | "checkpoint_size"
    message: str
    run_id: str | None
    tenant_id: str | None
    trace_id: str | None
    attributes: dict[str, Any]
    created_at_ns: int


class AlertSink(Protocol):
    def push(self, alert: Alert) -> None: ...


class InMemoryAlertSink:
    """A real, in-process `AlertSink` -- what a unit test reaches for
    when it does not need cross-process durability."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.alerts: list[Alert] = []

    def push(self, alert: Alert) -> None:
        with self._lock:
            self.alerts.append(alert)


class FileAlertSink:
    """A real, file-backed `AlertSink` -- the same continuous,
    fsync-on-write durability discipline as `sink.EventSink`, standing in
    for a real PagerDuty/Slack webhook POST. A real deployment swaps this
    class for one whose `push()` makes that HTTP call instead of
    appending a line; every call site elsewhere in this library only
    depends on the `AlertSink` Protocol above, so nothing else changes.
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._fh = open(self._path, "a", buffering=1, encoding="utf-8")

    def push(self, alert: Alert) -> None:
        with self._lock:
            self._fh.write(json.dumps(asdict(alert)) + "\n")
            self._fh.flush()
            os.fsync(self._fh.fileno())

    def close(self) -> None:
        with self._lock:
            self._fh.close()

    @staticmethod
    def read_all(path: str | Path) -> list[Alert]:
        p = Path(path)
        if not p.exists():
            return []
        alerts = []
        with open(p, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                alerts.append(Alert(**json.loads(line)))
        return alerts


def make_alert(
    *,
    kind: str,
    message: str,
    run_id: str | None = None,
    tenant_id: str | None = None,
    trace_id: str | None = None,
    **attributes: Any,
) -> Alert:
    return Alert(
        kind=kind,
        message=message,
        run_id=run_id,
        tenant_id=tenant_id,
        trace_id=trace_id,
        attributes=attributes,
        created_at_ns=time.time_ns(),
    )
