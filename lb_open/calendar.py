"""US cash-market session calendar (Sec 2).

Sec 2: "Trade only on full regular US cash-market sessions.  Skip cash-market
holidays and shortened sessions using the published calendar.  Do not remove a
day retrospectively because it was volatile."

Sec 8.5 additionally names "Full holidays, early closes and the 2025-01-09
exceptional closure" as explicit calendar exclusions.
"""

from __future__ import annotations

from datetime import date, time, timedelta
from typing import Iterator

REGULAR_OPEN = time(9, 30)
REGULAR_CLOSE = time(16, 0)

# Sec 5 L1/L3/L7 decision clock.
SNAPSHOT_1 = time(9, 29)
WINDOW_1_START = time(9, 30)
WINDOW_1_END = time(9, 45)
SNAPSHOT_2 = time(9, 44)
WINDOW_2_START = time(9, 45)
WINDOW_2_END = time(10, 0)
MANDATORY_FLAT = time(12, 0)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    offset = (weekday - d.weekday()) % 7
    return d + timedelta(days=offset + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month, 31) if month == 12 else date(year, month + 1, 1) - timedelta(days=1)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def _observed(d: date) -> date:
    """NYSE observance: Saturday -> preceding Friday, Sunday -> following Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _easter(year: int) -> date:
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return date(year, month, day + 1)


def full_holidays(year: int) -> set[date]:
    """NYSE full-closure holidays."""
    h = {
        _observed(date(year, 1, 1)),                      # New Year's Day
        _nth_weekday(year, 1, 0, 3),                      # MLK Jr Day
        _nth_weekday(year, 2, 0, 3),                      # Washington's Birthday
        _easter(year) - timedelta(days=2),                # Good Friday
        _last_weekday(year, 5, 0),                        # Memorial Day
        _observed(date(year, 7, 4)),                      # Independence Day
        _nth_weekday(year, 9, 0, 1),                      # Labor Day
        _nth_weekday(year, 11, 3, 4),                     # Thanksgiving
        _observed(date(year, 12, 25)),                    # Christmas
    }
    if year >= 2022:
        h.add(_observed(date(year, 6, 19)))               # Juneteenth
    # Sec 8.5: the 2025-01-09 exceptional closure (national day of mourning).
    if year == 2025:
        h.add(date(2025, 1, 9))
    if year == 2018:
        h.add(date(2018, 12, 5))
    return h


def early_closes(year: int) -> set[date]:
    """Sessions with a 13:00 close.  Sec 2 excludes shortened sessions entirely."""
    e = set()
    july4 = date(year, 7, 4)
    if july4.weekday() < 5:
        prior = july4 - timedelta(days=1)
        if prior.weekday() < 5:
            e.add(prior)
    e.add(_nth_weekday(year, 11, 3, 4) + timedelta(days=1))  # day after Thanksgiving
    xmas = date(year, 12, 25)
    if xmas.weekday() in (1, 2, 3, 4):
        e.add(xmas - timedelta(days=1))
    return e


def is_eligible_session(d: date) -> bool:
    """Sec 2: full regular sessions only; no weekends, holidays or early closes."""
    if d.weekday() >= 5:
        return False
    if d in full_holidays(d.year):
        return False
    if d in early_closes(d.year):
        return False
    return True


def eligible_sessions(start: date, end: date) -> list[date]:
    """All eligible sessions in the inclusive range, chronologically."""
    out: list[date] = []
    d = start
    while d <= end:
        if is_eligible_session(d):
            out.append(d)
        d += timedelta(days=1)
    return out


def previous_session(d: date) -> date:
    """Nearest preceding eligible session."""
    p = d - timedelta(days=1)
    while not is_eligible_session(p):
        p -= timedelta(days=1)
    return p


# ---------------------------------------------------------------------------
# Contract identity (Sec 2, "Contract identity")
# ---------------------------------------------------------------------------

QUARTERLY_MONTHS = (3, 6, 9, 12)
MONTH_CODE = {3: "H", 6: "M", 9: "U", 12: "Z"}


def quarterly_expiry(year: int, month: int) -> date:
    """Final settlement date of a CME equity-index quarterly: the third Friday."""
    if month not in QUARTERLY_MONTHS:
        raise ValueError(f"{month} is not a quarterly month {QUARTERLY_MONTHS}")
    return _nth_weekday(year, month, 4, 3)


def customary_roll(expiry: date) -> date:
    """CME's customary roll date: the Thursday preceding the third Friday [3]."""
    return expiry - timedelta(days=8)


def research_switch(expiry: date) -> date:
    """Sec 2: "The research switch is at 18:00 NY on the Sunday preceding that
    Monday roll date; all three symbols switch together."

    The date returned is that Sunday; the switch instant is 18:00 NY on it.
    """
    roll = customary_roll(expiry)
    monday = roll + timedelta(days=(0 - roll.weekday()) % 7)
    return monday - timedelta(days=1)


def contract_windows(start: date, end: date) -> list[tuple[str, date, date]]:
    """The dated contracts a `[start, end]` range falls across.

    Returns (ticker_suffix, window_start, window_end) per contract, where the
    window boundaries are the Sec 2 research switches.  Used to enforce Sec 2:
    "Do not splice price levels across contract expiries or use retrospectively
    back-adjusted continuous prices for absolute historical levels."
    """
    switches: list[tuple[date, str]] = []
    for year in range(start.year - 1, end.year + 2):
        for month in QUARTERLY_MONTHS:
            exp = quarterly_expiry(year, month)
            switches.append((research_switch(exp), f"{MONTH_CODE[month]}{year}"))
    switches.sort()

    windows: list[tuple[str, date, date]] = []
    for i, (switch, code) in enumerate(switches):
        w_start = switch
        w_end = switches[i + 1][0] - timedelta(days=1) if i + 1 < len(switches) else end
        if w_end < start or w_start > end:
            continue
        windows.append((code, max(w_start, start), min(w_end, end)))
    return windows
