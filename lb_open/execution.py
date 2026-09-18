"""Bar-based execution primitives (Sec 3, "Bar-based execution convention").

Sec 3: "This rulebook deliberately uses a reproducible one-minute management
baseline.  It does not claim to reproduce immediate tick-by-tick discretionary
management."

The ordering policy here is the rulebook's, not a convenience:

  Sec 3.4  "If available intrabar data cannot establish the sequence of entry and
           exits, use the adverse feasible sequence and flag the trade.  If both
           stop and target can follow entry, assume the stop first."
  Sec 8.4  "An independent review corrected the replay so a resting target
           already executable at the minute open fills before a later stop;
           opening stop gaps and noon flattening also have explicit priority."
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from .core import TICK, Bar, CostScenario, Direction, round_to_tick


class Fill(Enum):
    NONE = "none"
    TARGET = "target"
    STOP = "stop"
    FLAT = "noon_flat"


@dataclass(frozen=True, slots=True)
class FillResult:
    kind: Fill
    price: float
    ambiguous: bool = False
    note: str = ""


def limit_fillable(bar: Bar, limit: float, direction: Direction, cost: CostScenario) -> bool:
    """Sec 3: "Require trading one tick through the limit; fill at the limit, no
    favorable improvement assumed."  Sec 7 stress requires two ticks.

    A long entry limit rests below the market, so price must trade DOWN through
    it; a short entry limit rests above and price must trade UP through it.
    """
    through = cost.limit_verification_ticks * TICK
    if direction is Direction.LONG:
        return bar.low <= limit - through
    return bar.high >= limit + through


def stop_fill_price(bar: Bar, stop: float, direction: Direction, cost: CostScenario) -> float:
    """Sec 3: "For a long stop gapped through, use the worse of stop and minute-open
    price, then adverse slippage; reverse for shorts."
    """
    slip = cost.slippage_points
    if direction is Direction.LONG:
        base = min(stop, bar.open)  # worse of the two for a long exit
        return round_to_tick(base - slip)
    base = max(stop, bar.open)
    return round_to_tick(base + slip)


def market_fill_price(bar: Bar, direction: Direction, cost: CostScenario) -> float:
    """Sec 3: "Historical market entry is the next minute's open plus adverse
    slippage."  Also used for the Sec 3 noon flatten ("close any remaining
    position at the first executable price").
    """
    slip = cost.slippage_points
    return round_to_tick(bar.open + direction.d * slip)


def flatten_price(bar: Bar, direction: Direction, cost: CostScenario) -> float:
    """Exiting at market moves against the holder, i.e. opposite of entry."""
    slip = cost.slippage_points
    return round_to_tick(bar.open - direction.d * slip)


def touched(bar: Bar, level: float, direction: Direction, favourable: bool) -> bool:
    """Whether a resting order's level is reachable within this minute.

    A long target rests above (favourable), a long stop below (adverse).
    """
    if direction is Direction.LONG:
        return bar.high >= level if favourable else bar.low <= level
    return bar.low <= level if favourable else bar.high >= level


def resolve_open_position(
    bar: Bar,
    direction: Direction,
    stop: float,
    target: float,
    cost: CostScenario,
) -> FillResult:
    """Resolve one minute for an already-open position.

    Priority, per Sec 8.4 lesson 9 and Sec 3.4:
      1. A stop already gapped through at the minute OPEN executes at the open.
      2. A target already executable at the minute OPEN fills before a later stop.
      3. Otherwise, if both levels are reachable inside the minute, the ordering is
         not established, so take the ADVERSE feasible sequence: stop first, flagged.
    """
    d = direction.d
    stop_gapped = (bar.open <= stop) if direction is Direction.LONG else (bar.open >= stop)
    target_open = (bar.open >= target) if direction is Direction.LONG else (bar.open <= target)

    # 1. An opening gap through the stop is an established fact, not an ambiguity.
    if stop_gapped:
        return FillResult(Fill.STOP, stop_fill_price(bar, stop, direction, cost),
                          note="stop gapped at open")

    # 2. Sec 8.4: a resting target already executable at the open fills first.
    if target_open:
        return FillResult(Fill.TARGET, target, note="target executable at open")

    hits_stop = touched(bar, stop, direction, favourable=False)
    hits_target = touched(bar, target, direction, favourable=True)

    # 3. Sec 3.4: "If both stop and target can follow entry, assume the stop first."
    if hits_stop and hits_target:
        return FillResult(Fill.STOP, stop_fill_price(bar, stop, direction, cost),
                          ambiguous=True, note="stop and target both reachable; adverse order")
    if hits_stop:
        return FillResult(Fill.STOP, stop_fill_price(bar, stop, direction, cost))
    if hits_target:
        return FillResult(Fill.TARGET, target)
    return FillResult(Fill.NONE, 0.0)


def resolve_entry_minute(
    bar: Bar,
    direction: Direction,
    entry: float,
    stop: float,
    target: float,
    cost: CostScenario,
) -> tuple[bool, FillResult]:
    """Resolve a minute in which a resting ENTRY limit may fill.

    Returns (entered, exit_result).  Sec 3.4:
      "If an entry limit and target are reached in one minute but target-after-entry
       cannot be established, do not credit a same-minute target exit: retain the
       position unless a feasible stop execution closes it."
      "A target touched before an entry is not profit from that entry."
    """
    if not limit_fillable(bar, entry, direction, cost):
        return False, FillResult(Fill.NONE, 0.0)

    # The entry fills AT the limit (Sec 5 L6: "Limit fills are modeled at E without
    # improvement").  Now decide whether this same minute can also close it.
    hits_stop = touched(bar, stop, direction, favourable=False)
    hits_target = touched(bar, target, direction, favourable=True)

    if hits_stop:
        # A feasible stop execution after entry closes the position; adverse ordering
        # requires assuming it did (Sec 3.4, and Sec 6 "+45 and initial stop touched
        # with unknown ordering | adverse feasible outcome").
        return True, FillResult(
            Fill.STOP,
            stop_fill_price(bar, stop, direction, cost),
            ambiguous=True,
            note="entry and stop in one minute; adverse order",
        )
    if hits_target:
        # Explicitly NOT credited as profit; the position is carried forward.
        return True, FillResult(
            Fill.NONE, 0.0, ambiguous=True,
            note="entry and target in one minute; target not credited (Sec 3.4)",
        )
    return True, FillResult(Fill.NONE, 0.0)


def trade_pnl(entry: float, exit_price: float, direction: Direction,
              cost: CostScenario) -> tuple[float, float, float]:
    """Return (gross, fees, net) in USD for one MNQ contract.

    Sec 3: "Do not charge the same modeled slippage twice: it is included in fill
    prices, while fees are deducted separately."
    """
    from .core import POINT_VALUE

    gross = (exit_price - entry) * direction.d * POINT_VALUE
    fees = cost.round_trip_fees
    return gross, fees, gross - fees
