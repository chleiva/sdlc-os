"""Business-day math backing Sec. 12.1's escalation SLA ("default: two
business days" at a plan-approval/change-review gate, "immediately" at
a Sec. 9.3 risk/stuck checkpoint).

ASSUMPTION FLAGGED FOR HUMAN REVIEW: this is a weekend-only business
calendar (Mon-Fri are business days, Sat/Sun are not) -- there is no
organization-specific public-holiday calendar available in this
environment, and Sec. 12.1 does not specify one. A human should supply
a real holiday calendar per Sec. 19's "adjustable per organization"
pattern if that precision matters; `add_business_days` accepts an
optional `holidays` set of `date`s for exactly that extension without
changing its signature's meaning.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable


def is_business_day(d: date, *, holidays: Iterable[date] = ()) -> bool:
    return d.weekday() < 5 and d not in set(holidays)  # Mon=0 .. Sun=6


def add_business_days(start: datetime, business_days: float, *, holidays: Iterable[date] = ()) -> datetime:
    """Adds `business_days` whole business days to `start`, preserving
    time-of-day, skipping weekends (and `holidays`, if given).
    `business_days=0` returns `start` unchanged -- the "immediately" SLA
    case (a risk/stuck checkpoint has zero grace period)."""
    if business_days < 0:
        raise ValueError("business_days must be >= 0")
    holiday_set = set(holidays)
    remaining = int(business_days)
    current = start
    while remaining > 0:
        current = current + timedelta(days=1)
        if is_business_day(current.date(), holidays=holiday_set):
            remaining -= 1
    return current


def sla_deadline(opened_at: datetime, *, business_days: float, holidays: Iterable[date] = ()) -> datetime:
    return add_business_days(opened_at, business_days, holidays=holidays)


def is_sla_breached(opened_at: datetime, now: datetime, *, business_days: float, holidays: Iterable[date] = ()) -> bool:
    return now >= sla_deadline(opened_at, business_days=business_days, holidays=holidays)
