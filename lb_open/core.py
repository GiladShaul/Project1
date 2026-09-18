"""Core value types, clock and cost primitives shared by the LB-OPEN engine.

Every rule reference in this package cites the section of Strategy_Rulebook_v1.md
that mandates it.  Where the rulebook is explicit, this module is deliberately
literal rather than clever: the rulebook is the specification of record.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date, time, timedelta
from enum import Enum
from typing import Iterable, Optional, Sequence
from zoneinfo import ZoneInfo

# Sec 2: "Use timezone-aware America/New_York.  Store timestamps in UTC and
# derive NY local dates/times.  Never hard-code an Israel/NY offset."
NY = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

# Sec 2: "MNQ has $2 per index point and a 0.25-point tick, or $0.50 per tick."
TICK = 0.25
POINT_VALUE = 2.0
TICK_VALUE = TICK * POINT_VALUE  # $0.50


def to_ny(ts: datetime) -> datetime:
    """Convert an aware timestamp to NY local time."""
    if ts.tzinfo is None:
        raise ValueError(f"naive timestamp not allowed: {ts!r}")
    return ts.astimezone(NY)


def ny_datetime(d: date, t: time) -> datetime:
    """Build an aware NY timestamp from a local wall-clock date and time.

    Sec 5 L3: "Use local wall-clock date/time subtraction, then timezone
    conversion; do not implement day/week offsets with modulo-1440 clock
    arithmetic."  `fold=0` resolves a repeated wall clock to the first
    occurrence; callers detect ambiguity/non-existence via `wall_clock_valid`.
    """
    return datetime.combine(d, t, tzinfo=NY)


def wall_clock_valid(d: date, t: time) -> bool:
    """False when a local wall clock is non-existent or ambiguous (DST change).

    Sec 5 L3: "If a local start is ambiguous/nonexistent during a clock change,
    or the required trading interval is unavailable, remove that candidate."
    """
    first = datetime.combine(d, t, tzinfo=NY).replace(fold=0)
    second = datetime.combine(d, t, tzinfo=NY).replace(fold=1)
    # Ambiguous: the two folds denote different instants (repeated wall clock).
    if first.utcoffset() != second.utcoffset():
        return False
    # Non-existent: round-tripping through UTC does not reproduce the wall clock.
    return first.astimezone(UTC).astimezone(NY).replace(tzinfo=NY) == first.replace(tzinfo=NY)


def round_to_tick(price: float) -> float:
    """Snap a price onto the instrument's tick grid (Sec 2)."""
    return round(round(price / TICK) * TICK, 10)


def ticks_between(a: float, b: float) -> float:
    return abs(a - b) / TICK


class Direction(Enum):
    """Sec 5 L1 daily bias; Sec 3 uses d=+1 long, d=-1 short."""

    LONG = 1
    SHORT = -1

    @property
    def d(self) -> int:
        return self.value


@dataclass(frozen=True, slots=True)
class Bar:
    """One completed minute of trade OHLC.

    Sec 2: "A completed candle ending at a boundary belongs to the interval
    containing its observations; its close becomes available at that ending
    timestamp."  `ts` is the bar's START; availability is ts + 1 minute.
    """

    ts: datetime  # aware, start of the minute
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0

    @property
    def end(self) -> datetime:
        return self.ts + timedelta(minutes=1)

    @property
    def available_at(self) -> datetime:
        """Sec 2: the close becomes available at the ending timestamp."""
        return self.end

    @property
    def ny(self) -> datetime:
        return to_ny(self.ts)

    def covers(self, level: float) -> bool:
        """Sec 5 L1: "A touch means a recorded minute satisfies low <= level <= high"."""
        return self.low <= level <= self.high

    @property
    def is_doji(self) -> bool:
        return self.close == self.open


@dataclass(frozen=True, slots=True)
class OHLC:
    """An aggregated interval (daily regular session, or a 30-minute LB candle)."""

    start: datetime
    end: datetime
    open: float
    high: float
    low: float
    close: float

    @property
    def is_zero_width_up_wick(self) -> bool:
        """Sec 5 L3: "Zero-width wick zones are ineligible." (long zone)"""
        return self.high == max(self.open, self.close)

    @property
    def is_zero_width_down_wick(self) -> bool:
        """Sec 5 L3 short-side counterpart."""
        return self.low == min(self.open, self.close)


def aggregate(bars: Sequence[Bar]) -> Optional[OHLC]:
    """Aggregate complete minute bars into one interval (Sec 2)."""
    if not bars:
        return None
    ordered = sorted(bars, key=lambda b: b.ts)
    return OHLC(
        start=ordered[0].ts,
        end=ordered[-1].end,
        open=ordered[0].open,
        high=max(b.high for b in ordered),
        low=min(b.low for b in ordered),
        close=ordered[-1].close,
    )


@dataclass(frozen=True, slots=True)
class CostScenario:
    """Sec 3 cost/risk policy.  Sec 7 declares the stress variant.

    Sec 3: "Do not charge the same modeled slippage twice: it is included in
    fill prices, while fees are deducted separately."
    """

    name: str
    per_side_fee: float          # USD per contract per filled side
    slippage_ticks: float        # adverse ticks on market/stop fills
    limit_verification_ticks: int  # ticks of trade-through required for a limit fill
    initial_stop_points: float = 30.0
    starting_equity: float = 50_000.0
    per_entry_risk_fraction: float = 0.0025  # Sec 3: at most 0.25% of closed equity
    daily_loss_gate: float = -120.0
    min_reward_risk: float = 2.0  # Sec 5 L6

    @property
    def round_trip_fees(self) -> float:
        """Sec 3: round_trip_fees = 2 * per_side_fee."""
        return 2.0 * self.per_side_fee

    @property
    def planned_loss(self) -> float:
        """Sec 3: planned_loss = 60 + round_trip_fees + stop_slippage_ticks*0.50."""
        return (
            self.initial_stop_points * POINT_VALUE
            + self.round_trip_fees
            + self.slippage_ticks * TICK_VALUE
        )

    @property
    def slippage_points(self) -> float:
        return self.slippage_ticks * TICK

    def min_target_points(self) -> float:
        """Sec 5 L6: smallest tick-grid distance satisfying the reward/risk gate.

        Gate: (abs(T-E)*2 - round_trip_fees) / planned_loss >= min_reward_risk.
        """
        raw = (self.min_reward_risk * self.planned_loss + self.round_trip_fees) / POINT_VALUE
        # Smallest tick-grid distance at or above the raw requirement.
        import math

        return round(math.ceil(raw / TICK - 1e-9) * TICK, 10)

    def reward_risk(self, entry: float, target: float) -> float:
        return (abs(target - entry) * POINT_VALUE - self.round_trip_fees) / self.planned_loss


# Sec 3 baseline; Sec 7 stress ("double per-side fees to $2.50, use four adverse
# ticks for market/stop fills, and require two ticks of trade-through").
BASELINE = CostScenario(
    name="baseline",
    per_side_fee=1.25,
    slippage_ticks=1.0,
    limit_verification_ticks=1,
)

STRESS = CostScenario(
    name="stress",
    per_side_fee=2.50,
    slippage_ticks=4.0,
    limit_verification_ticks=2,
)
