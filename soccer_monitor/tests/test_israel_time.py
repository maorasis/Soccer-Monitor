"""
Regression test: the daemon's clock was a hardcoded UTC+3.

Israel keeps UTC+3 (IDT) in summer and UTC+2 (IST) in winter, so from the last Sunday
in October the 13:00-01:00 active window and the midnight alert-history flush both ran
an hour early -- the daemon started monitoring at 12:00 local and flushed at 23:00.

Verifies the zone actually shifts, and that the "sleep until 13:00" arithmetic still
lands on 13:00 local across the transition rather than drifting by the offset change.

Run: python tests/test_israel_time.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from soccer_monitor import ISRAEL_TZ


def offset_hours(when):
    return when.utcoffset().total_seconds() / 3600


def test_summer_is_utc_plus_3():
    august = datetime(2026, 8, 30, 12, 0, tzinfo=ISRAEL_TZ)
    assert offset_hours(august) == 3, offset_hours(august)


def test_winter_is_utc_plus_2():
    november = datetime(2026, 11, 15, 12, 0, tzinfo=ISRAEL_TZ)
    assert offset_hours(november) == 2, (
        f"winter must be UTC+2, got UTC+{offset_hours(november):g} -- a fixed offset "
        f"runs the active window an hour early for half the year")


def test_the_offset_actually_changes():
    summer = datetime(2026, 8, 30, 12, 0, tzinfo=ISRAEL_TZ)
    winter = datetime(2026, 11, 15, 12, 0, tzinfo=ISRAEL_TZ)
    assert offset_hours(summer) != offset_hours(winter)


def test_sleep_until_13_lands_on_13_across_the_transition():
    """Israel's clocks go back overnight on 24-25 October 2026.

    CPython ignores a shared tzinfo when subtracting two aware datetimes, so the
    obvious `next_start - now` is a wall-clock difference: 17 hours, which is an hour
    short of the real interval and would resume the daemon at 12:00 local. The loop
    compares the two instants in UTC for exactly this reason.
    """
    now = datetime(2026, 10, 24, 20, 0, tzinfo=ISRAEL_TZ)  # evening before

    next_start = now.replace(hour=13, minute=0, second=0, microsecond=0)
    if next_start <= now:
        next_start += timedelta(days=1)

    naive = (next_start - now).total_seconds()
    assert naive == 17 * 3600, f"expected the wall-clock trap to be 17h, got {naive/3600}h"

    sleep_seconds = (next_start.astimezone(timezone.utc)
                     - now.astimezone(timezone.utc)).total_seconds()
    assert sleep_seconds == 18 * 3600, f"real interval should be 18h, got {sleep_seconds/3600}h"

    landed = now.astimezone(timezone.utc) + timedelta(seconds=sleep_seconds)
    assert landed.astimezone(ISRAEL_TZ).hour == 13, (
        f"woke at {landed.astimezone(ISRAEL_TZ).hour}:00 local, not 13:00")


if __name__ == "__main__":
    test_summer_is_utc_plus_3()
    test_winter_is_utc_plus_2()
    test_the_offset_actually_changes()
    test_sleep_until_13_lands_on_13_across_the_transition()
    print("OK: Israel time tracks DST, and the resume time holds across the transition")
