from __future__ import annotations

from datetime import datetime, timezone

from gates.business_calendar import add_business_days, is_business_day, is_sla_breached, sla_deadline


def test_weekend_boundary_case_explicitly():
    """Friday 10:00 + 2 business days must land on Tuesday 10:00 --
    Saturday/Sunday are skipped entirely (Fri->Mon is business day 1,
    Mon->Tue is business day 2), the exact case Sec. 12.1's "two
    business days" default is meant to handle correctly."""
    friday = datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc)  # a real Friday
    assert friday.weekday() == 4

    deadline = add_business_days(friday, 2)
    assert deadline == datetime(2026, 9, 15, 10, 0, tzinfo=timezone.utc)  # the following Tuesday
    assert deadline.weekday() == 1


def test_a_single_business_day_from_saturday_lands_on_monday():
    saturday = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)
    assert saturday.weekday() == 5
    deadline = add_business_days(saturday, 1)
    assert deadline == datetime(2026, 9, 14, 9, 0, tzinfo=timezone.utc)


def test_zero_business_days_is_immediate():
    now = datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc)
    assert add_business_days(now, 0) == now
    assert is_sla_breached(now, now, business_days=0) is True
    assert is_sla_breached(now, now - __import__("datetime").timedelta(seconds=1), business_days=0) is False


def test_is_business_day():
    assert is_business_day(datetime(2026, 9, 11).date()) is True  # Friday
    assert is_business_day(datetime(2026, 9, 12).date()) is False  # Saturday
    assert is_business_day(datetime(2026, 9, 13).date()) is False  # Sunday
    assert is_business_day(datetime(2026, 9, 14).date()) is True  # Monday


def test_sla_not_breached_before_deadline_and_breached_at_or_after():
    opened = datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc)  # Friday
    deadline = sla_deadline(opened, business_days=2)
    just_before = deadline - __import__("datetime").timedelta(minutes=1)
    assert is_sla_breached(opened, just_before, business_days=2) is False
    assert is_sla_breached(opened, deadline, business_days=2) is True
    assert is_sla_breached(opened, deadline + __import__("datetime").timedelta(minutes=1), business_days=2) is True


def test_holidays_extend_the_deadline():
    friday = datetime(2026, 9, 11, 10, 0, tzinfo=timezone.utc)
    monday_holiday = datetime(2026, 9, 14).date()
    deadline_without_holiday = add_business_days(friday, 2)
    deadline_with_holiday = add_business_days(friday, 2, holidays={monday_holiday})
    assert deadline_with_holiday > deadline_without_holiday
