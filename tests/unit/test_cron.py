"""Five-field cron: parsing, matching, next run, plain-English description."""
from __future__ import annotations

from datetime import datetime

import pytest

from webapp.services.cron import Cron, CronError

THU = datetime(2026, 10, 29, 13, 20)  # a Thursday


@pytest.mark.parametrize("expression, expected", [
    ("30 6 * * 1-5", datetime(2026, 10, 30, 6, 30)),
    ("0 */4 * * *", datetime(2026, 10, 29, 16, 0)),
    ("*/15 * * * *", datetime(2026, 10, 29, 13, 30)),
    ("0 2 * * sat,sun", datetime(2026, 10, 31, 2, 0)),
    ("0 0 29 2 *", datetime(2028, 2, 29, 0, 0)),
    ("0 9 1 * mon", datetime(2026, 11, 1, 9, 0)),  # day-of-month OR day-of-week: 1 Nov is a Sunday
    ("0 0 * jan 0", datetime(2027, 1, 3, 0, 0)),
    ("20 13 * * *", datetime(2026, 10, 30, 13, 20)),  # strictly after
    ("0 9 * * 7", datetime(2026, 11, 1, 9, 0)),  # 7 = Sunday too
])
def test_next_after(expression, expected):
    assert Cron.parse(expression).next_after(THU) == expected


@pytest.mark.parametrize("expression", ["", "* * * *", "60 * * * *", "* 24 * * *", "5-1 * * * *", "a * * * *",
                                        "*/0 * * * *", "1,,2 * * * *", "* * 0 * *"])
def test_invalid(expression):
    with pytest.raises(CronError):
        Cron.parse(expression)


def test_matches_and_describe():
    cron = Cron.parse("30 6 * * 1-5")
    assert cron.matches(datetime(2026, 10, 30, 6, 30, 45)) and not cron.matches(datetime(2026, 10, 31, 6, 30))
    assert cron.describe() == "On weekdays at 06:30"
    assert Cron.parse("0 2 * * 0,6").describe() == "At weekends at 02:00"
    assert Cron.parse("0 22 * * 5").describe() == "On Fri at 22:00"
    assert Cron.parse("15 */3 * * *").describe() == "Every 3 hours at :15, every day"
    assert Cron.parse("0 0 1 * *").describe() == "cron 0 0 1 * *"
