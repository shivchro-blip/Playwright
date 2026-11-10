"""Five-field cron expressions (minute hour day-of-month month day-of-week), in the machine's local time.

Supports *, lists (1,15), ranges (1-5), steps (*/15, 8-18/2) and day/month names (mon, jan). As in cron,
when both day-of-month and day-of-week are restricted, a day matches if either does.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

FIELDS = (("minute", 0, 59), ("hour", 0, 23), ("day of month", 1, 31), ("month", 1, 12), ("day of week", 0, 7))
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), start=1)}
DAYS = {d: i for i, d in enumerate(("sun", "mon", "tue", "wed", "thu", "fri", "sat"))}
DAY_NAMES = ("Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday")
SEARCH_DAYS = 366 * 5  # far enough for "29 Feb" style expressions


class CronError(ValueError):
    pass


def _value(text: str, names: dict[str, int], name: str, lo: int, hi: int) -> int:
    key = text.strip().lower()
    if key in names:
        return names[key]
    if not key.isdigit():
        raise CronError(f"{name}: {text!r} is not a number")
    value = int(key)
    if not lo <= value <= hi:
        raise CronError(f"{name}: {value} is outside {lo}-{hi}")
    return value


def _field(text: str, name: str, lo: int, hi: int, names: dict[str, int]) -> frozenset[int]:
    values: set[int] = set()
    for part in text.split(","):
        if not part:
            raise CronError(f"{name}: empty list item")
        base, _, step_text = part.partition("/")
        step = _value(step_text, {}, name, 1, hi) if step_text else 1
        if base == "*":
            start, end = lo, hi
        elif "-" in base:
            a, b = base.split("-", 1)
            start, end = _value(a, names, name, lo, hi), _value(b, names, name, lo, hi)
            if start > end:
                raise CronError(f"{name}: range {base} runs backwards")
        else:
            start = _value(base, names, name, lo, hi)
            end = hi if step_text else start
        values.update(range(start, end + 1, step))
    return frozenset(values)


@dataclass(frozen=True)
class Cron:
    expression: str
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]  # 0 = Sunday
    any_day: bool
    any_weekday: bool

    @classmethod
    def parse(cls, expression: str) -> Cron:
        parts = (expression or "").split()
        if len(parts) != 5:
            raise CronError("Use five fields: minute hour day-of-month month day-of-week (e.g. 30 6 * * 1-5)")
        names = ({}, {}, {}, MONTHS, DAYS)
        sets = [_field(p, n, lo, hi, nm) for p, (n, lo, hi), nm in zip(parts, FIELDS, names)]
        weekdays = frozenset(d % 7 for d in sets[4])
        return cls(" ".join(parts), sets[0], sets[1], sets[2], sets[3], weekdays,
                   any_day=parts[2] == "*", any_weekday=parts[4] == "*")

    def _day_matches(self, d: datetime) -> bool:
        dom, dow = d.day in self.days, (d.weekday() + 1) % 7 in self.weekdays
        if self.any_day and self.any_weekday:
            return True
        if self.any_day:
            return dow
        if self.any_weekday:
            return dom
        return dom or dow

    def matches(self, d: datetime) -> bool:
        return (d.minute in self.minutes and d.hour in self.hours and d.month in self.months
                and self._day_matches(d))

    def next_after(self, after: datetime) -> datetime | None:
        """The first matching minute strictly after `after` (seconds dropped), or None."""
        t = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        end = t + timedelta(days=SEARCH_DAYS)
        while t < end:
            if t.month not in self.months:
                t = (t.replace(day=1, hour=0, minute=0) + timedelta(days=32)).replace(day=1)
            elif not self._day_matches(t):
                t = t.replace(hour=0, minute=0) + timedelta(days=1)
            elif t.hour not in self.hours:
                t = t.replace(minute=0) + timedelta(hours=1)
            elif t.minute not in self.minutes:
                t += timedelta(minutes=1)
            else:
                return t
        return None

    def describe(self) -> str:
        """Plain English for the common shapes; the expression itself otherwise."""
        minute, hour, dom, month, dow = self.expression.split()
        if month != "*" or dom != "*":
            return f"cron {self.expression}"
        if hour == "*" and minute.isdigit():
            when = f"every hour at :{int(minute):02d}"
        elif minute.isdigit() and hour.isdigit():
            when = f"at {int(hour):02d}:{int(minute):02d}"
        elif minute.isdigit() and hour.startswith("*/") and hour[2:].isdigit():
            when = f"every {hour[2:]} hours at :{int(minute):02d}"
        elif minute.startswith("*/") and hour == "*":
            when = f"every {minute[2:]} minutes"
        else:
            return f"cron {self.expression}"
        if dow == "*":
            days = "every day"
        elif self.weekdays == frozenset(range(1, 6)):
            days = "on weekdays"
        elif self.weekdays == frozenset({0, 6}):
            days = "at weekends"
        else:
            days = "on " + ", ".join(DAY_NAMES[d][:3] for d in sorted(self.weekdays, key=lambda d: (d + 6) % 7))
        text = f"{days} {when}" if not when.startswith("every") else f"{when}, {days}"
        return text[:1].upper() + text[1:]
