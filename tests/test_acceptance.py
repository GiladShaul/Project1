"""Section 6 minimum acceptance examples.

Sec 6: "Before any performance report, an implementation must pass these
examples.  They are acceptance cases, **not executed test results**."

Only the cases that bind LB-OPEN are here; the SMT/PSP/+45 cases belong to
Q90-PSP, which this package does not implement.  Each test names the case from
the Sec 6 table verbatim in its docstring.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lb_open import calendar as cal
from lb_open.core import (
    BASELINE,
    NY,
    STRESS,
    UTC,
    Bar,
    Direction,
    ny_datetime,
    round_to_tick,
    wall_clock_valid,
)
from lb_open.data import MinuteStore, MissingData
from lb_open.execution import (
    Fill,
    limit_fillable,
    resolve_entry_minute,
    resolve_open_position,
    stop_fill_price,
)


def minutes(start_ny: datetime, ohlc: list[tuple[float, float, float, float]]) -> list[Bar]:
    """Build consecutive one-minute bars from NY-local start."""
    return [
        Bar(ts=(start_ny + timedelta(minutes=i)).astimezone(UTC), open=o, high=h, low=l, close=c)
        for i, (o, h, l, c) in enumerate(ohlc)
    ]


# --------------------------------------------------------------------------
# "NY 09:30 in winter versus summer | Both target the same NY event with
#  different UTC offsets"
# --------------------------------------------------------------------------

def test_ny_open_same_event_across_dst():
    winter = ny_datetime(date(2026, 1, 15), time(9, 30))
    summer = ny_datetime(date(2026, 7, 15), time(9, 30))
    assert winter.utcoffset() == timedelta(hours=-5)
    assert summer.utcoffset() == timedelta(hours=-4)
    # Same NY wall-clock event, different UTC instants-of-day.
    assert winter.astimezone(UTC).hour == 14
    assert summer.astimezone(UTC).hour == 13
    assert winter.hour == summer.hour == 9


def test_dst_transition_wall_clocks_flagged():
    """Sec 5 L3: ambiguous/non-existent local starts remove the candidate."""
    # 2026-03-08 02:30 NY does not exist (spring forward).
    assert not wall_clock_valid(date(2026, 3, 8), time(2, 30))
    # 2026-11-01 01:30 NY occurs twice (fall back).
    assert not wall_clock_valid(date(2026, 11, 1), time(1, 30))
    assert wall_clock_valid(date(2026, 6, 1), time(9, 30))


# --------------------------------------------------------------------------
# "Stress cost scenario | Planned stop loss $67; LB minimum target distance
#  69.50 points"
# --------------------------------------------------------------------------

def test_stress_cost_scenario_constants():
    assert BASELINE.planned_loss == 63.0
    assert BASELINE.min_target_points() == 64.25
    assert STRESS.planned_loss == 67.0
    assert STRESS.min_target_points() == 69.50


def test_reward_risk_gate_is_the_binding_constraint():
    """Sec 5 L6: "(abs(T-E)*2 - 2.50)/63 >= 2.0" in baseline."""
    assert BASELINE.reward_risk(100.0, 164.25) >= 2.0
    assert BASELINE.reward_risk(100.0, 164.00) < 2.0
    assert STRESS.reward_risk(100.0, 169.50) >= 2.0
    assert STRESS.reward_risk(100.0, 169.25) < 2.0


# --------------------------------------------------------------------------
# "Limit reaction candle traded through entry earlier | No retrospective entry
#  in that candle"
# --------------------------------------------------------------------------

def test_limit_requires_trade_through_and_never_improves():
    t = datetime(2026, 9, 15, 14, 0, tzinfo=UTC)
    # Touching the limit exactly is not a fill: one tick THROUGH is required.
    assert not limit_fillable(Bar(t, 101, 102, 100.00, 101), 100.0, Direction.LONG, BASELINE)
    assert limit_fillable(Bar(t, 101, 102, 99.75, 101), 100.0, Direction.LONG, BASELINE)
    # Sec 7 stress requires two ticks of trade-through.
    assert not limit_fillable(Bar(t, 101, 102, 99.75, 101), 100.0, Direction.LONG, STRESS)
    assert limit_fillable(Bar(t, 101, 102, 99.50, 101), 100.0, Direction.LONG, STRESS)


# --------------------------------------------------------------------------
# "Stop and target touched in an unordered bar | Do not assume target first"
# --------------------------------------------------------------------------

def test_unordered_stop_and_target_takes_the_stop():
    t = datetime(2026, 9, 15, 14, 0, tzinfo=UTC)
    bar = Bar(t, open=100.0, high=200.0, low=50.0, close=150.0)
    r = resolve_open_position(bar, Direction.LONG, stop=70.0, target=164.25, cost=BASELINE)
    assert r.kind is Fill.STOP
    assert r.ambiguous is True


def test_target_already_executable_at_open_fills_first():
    """Sec 8.4 lesson 9: "a resting target already executable at the minute open
    fills before a later stop"."""
    t = datetime(2026, 9, 15, 14, 0, tzinfo=UTC)
    bar = Bar(t, open=170.0, high=175.0, low=50.0, close=60.0)
    r = resolve_open_position(bar, Direction.LONG, stop=70.0, target=164.25, cost=BASELINE)
    assert r.kind is Fill.TARGET
    assert r.price == 164.25
    assert r.ambiguous is False


def test_opening_stop_gap_uses_worse_of_stop_and_open():
    """Sec 3: "For a long stop gapped through, use the worse of stop and
    minute-open price, then adverse slippage"."""
    t = datetime(2026, 9, 15, 14, 0, tzinfo=UTC)
    bar = Bar(t, open=60.0, high=90.0, low=55.0, close=80.0)
    r = resolve_open_position(bar, Direction.LONG, stop=70.0, target=164.25, cost=BASELINE)
    assert r.kind is Fill.STOP
    assert r.price == round_to_tick(60.0 - BASELINE.slippage_points)  # 59.75


# --------------------------------------------------------------------------
# "Entry limit and target touched, order unknown, no stop | Do not credit target
#  profit; carry the position forward"
# --------------------------------------------------------------------------

def test_entry_and_target_same_minute_not_credited():
    t = datetime(2026, 9, 15, 14, 0, tzinfo=UTC)
    bar = Bar(t, open=101.0, high=170.0, low=99.5, close=168.0)
    entered, result = resolve_entry_minute(
        bar, Direction.LONG, entry=100.0, stop=70.0, target=164.25, cost=BASELINE
    )
    assert entered is True
    assert result.kind is Fill.NONE       # position carried forward
    assert result.ambiguous is True


def test_entry_and_stop_same_minute_takes_the_stop():
    t = datetime(2026, 9, 15, 14, 0, tzinfo=UTC)
    bar = Bar(t, open=101.0, high=170.0, low=65.0, close=120.0)
    entered, result = resolve_entry_minute(
        bar, Direction.LONG, entry=100.0, stop=70.0, target=164.25, cost=BASELINE
    )
    assert entered is True
    assert result.kind is Fill.STOP
    assert result.ambiguous is True


# --------------------------------------------------------------------------
# Sec 2 causality and completeness
# --------------------------------------------------------------------------

def test_decision_cannot_see_a_bar_that_has_not_closed():
    """Sec 2: "A decision at time t can access only observations with
    availability time at or before t"."""
    start = ny_datetime(date(2026, 9, 15), time(9, 0))
    store = MinuteStore(minutes(start, [(100 + i, 101 + i, 99 + i, 100.5 + i) for i in range(10)]))
    as_of = (start + timedelta(minutes=5)).astimezone(UTC)
    bar = store.last_close_at(as_of)
    # The minute [09:04,09:05) closed at 09:05 and is visible; [09:05,09:06) is not.
    assert bar.ts == (start + timedelta(minutes=4)).astimezone(UTC)
    assert bar.available_at == as_of


def test_missing_mandatory_interval_raises():
    """Sec 2: "missing mandatory context vetoes the setup"."""
    start = ny_datetime(date(2026, 9, 15), time(9, 0))
    bars = minutes(start, [(100, 101, 99, 100.5)] * 10)
    store = MinuteStore([b for i, b in enumerate(bars) if i != 4])
    with pytest.raises(MissingData):
        store.require_complete(
            start.astimezone(UTC), (start + timedelta(minutes=10)).astimezone(UTC), "probe"
        )


def test_absent_minute_is_not_a_flat_candle():
    """Sec 2: "An exchange-closed interval is unavailable, not a flat candle"."""
    start = ny_datetime(date(2026, 9, 15), time(9, 0))
    bars = minutes(start, [(100, 101, 99, 100.5)] * 10)
    store = MinuteStore([b for i, b in enumerate(bars) if i not in (3, 4)])
    assert store.aggregate_if_complete(
        start.astimezone(UTC), (start + timedelta(minutes=10)).astimezone(UTC)
    ) is None
    assert store.coverage(
        start.astimezone(UTC), (start + timedelta(minutes=10)).astimezone(UTC)
    ).present == 8


# --------------------------------------------------------------------------
# Sec 2 calendar
# --------------------------------------------------------------------------

def test_calendar_excludes_holidays_and_early_closes():
    assert not cal.is_eligible_session(date(2025, 1, 9))    # Sec 8.5 exceptional closure
    assert not cal.is_eligible_session(date(2026, 1, 1))    # New Year's Day
    assert not cal.is_eligible_session(date(2026, 11, 27))  # day after Thanksgiving (early close)
    assert not cal.is_eligible_session(date(2026, 9, 7))    # Labor Day
    assert not cal.is_eligible_session(date(2026, 9, 19))   # Saturday
    assert cal.is_eligible_session(date(2026, 9, 15))
