"""Date values for calendar-widget fields: MM/DD/YYYY, YYYY-MM-DD, "today", or +30d / -7d from today."""
import re
from datetime import date, timedelta

_RELATIVE = re.compile(r"^([+-]\d{1,4})\s*d(?:ays?)?$", re.IGNORECASE)


def parse_date_value(value, today: date | None = None) -> date:
    text = str(value or "").strip()
    today = today or date.today()
    if not text:
        raise ValueError("A date is needed, e.g. 11/30/2026, 2026-11-30 or +30d.")
    if text.lower() == "today":
        return today
    m = _RELATIVE.match(text)
    if m:
        return today + timedelta(days=int(m.group(1)))
    for pattern, order in ((r"^(\d{1,2})/(\d{1,2})/(\d{4})$", "mdy"), (r"^(\d{4})-(\d{1,2})-(\d{1,2})$", "ymd")):
        m = re.match(pattern, text)
        if m:
            a, b, c = (int(g) for g in m.groups())
            month, day, year = (a, b, c) if order == "mdy" else (b, c, a)
            try:
                return date(year, month, day)
            except ValueError:
                break
    raise ValueError(f"Not a date I can pick: {text!r}. Use MM/DD/YYYY, YYYY-MM-DD, today, or +30d / -7d.")


_MONTH_NAMES = ("january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
                "november", "december")


def months_to(label: str, wanted: date) -> int:
    """How many months to page forward (negative: back) from a calendar header such as "November 2026"."""
    m = re.search(r"([A-Za-z]{3,})\.?,?\s+(\d{4})", label or "")
    if not m:
        raise ValueError(f"Could not read the month from the calendar header {label!r}.")
    word = m.group(1).lower()
    month = next((i for i, name in enumerate(_MONTH_NAMES, 1) if name[:3] == word[:3]), None)
    if month is None:
        raise ValueError(f"Could not read the month from the calendar header {label!r}.")
    return (wanted.year * 12 + wanted.month) - (int(m.group(2)) * 12 + month)
