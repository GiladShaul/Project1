"""Structural target selection and the reward/risk gate (Sec 5 L6).

Sec 5 L6: "At selection snapshot `S`, construct target candidates from:
1. Unconsumed confirmed swing highs for Long or swing lows for Short, using the
same swing history as L5.  2. High and low boundaries of the other available
historical LB candles for this window.  Deduplicate tick prices, retain only
prices strictly in the profitable direction from `E`, and choose the
**nearest**.  Do not jump over a nearer target to advertise a better
reward/risk ratio.  Freeze that target `T` for the window.  TDO/AMO/quarter
targets are excluded in this independent baseline."

This module evaluates ONE LB candidate.  Sec 5 L6: "If the nearest target
fails, reject that LB candidate and evaluate the next ranked LB candidate
before freezing a setup."  The iteration over ranked candidates therefore
belongs to the engine; `choose_target` never falls back to a farther target on
the same candidate, because that is exactly the jump the rulebook forbids.

Sec 2: "A decision at time `t` can access only observations with availability
time at or before `t`."  Target construction consumes swing/candle levels that
the caller already froze at `S`; the only price reads here are in
`target_touched`, which takes an explicit `as_of` and routes through
`MinuteStore.available`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

from .core import CostScenario, Direction, round_to_tick
from .data import MinuteStore
from .swings import Swing

# Sec 5 L6 enumerates its two target sources in this order; the labels are
# carried into the audit record (Sec 6: "For LB also save all candidates and
# ranking").
SOURCE_SWING = "swing"
SOURCE_LB_CANDLE = "lb_candle"


@dataclass(frozen=True, slots=True)
class TargetChoice:
    """The frozen target `T` for one window, with the gate figures that passed.

    Sec 5 L3: "Freeze the chosen zone, entry, swing and target for the window."
    `reward_risk` is the value the Sec 5 L6 gate was evaluated on, retained for
    the audit record rather than recomputed downstream.
    """

    target: float
    source: str            # SOURCE_SWING | SOURCE_LB_CANDLE
    reward_risk: float
    distance_points: float  # abs(T - E)


def _profitable(price: float, entry: float, direction: Direction) -> bool:
    """Sec 5 L6: "retain only prices strictly in the profitable direction from `E`".

    Sec 2: "Strict comparisons mean strict" - a target at `E` is not a target.
    """
    if direction is Direction.LONG:
        return price > entry
    return price < entry


def _labelled_prices(
    swings: Sequence[Swing],
    other_boundaries: Sequence[float],
    entry: float,
    direction: Direction,
) -> list[tuple[float, str]]:
    """Deduplicated (price, source) target candidates, nearest to `E` first.

    Sec 5 L6 source 1: "Unconsumed confirmed swing highs for Long or swing lows
    for Short" - the KIND is fixed by direction, not merely by which side of `E`
    the level sits on.  The caller supplies the same swing history as L5, i.e.
    swings confirmed since previous day 18:00 and unconsumed as of `S`
    (`swings.unconsumed_swings`); consumption is not re-tested here because that
    test needs the store and the snapshot, which the caller already applied.

    Sec 5 L6 source 2: "High and low boundaries of the other available
    historical LB candles for this window" - supplied as tick prices by
    `lookback.other_candle_boundaries`, which excludes the candle being
    evaluated.

    Sec 5 L6: "Deduplicate tick prices".  A price contributed by both sources is
    one target; it keeps the swing label, the source the rulebook lists first.
    """
    e = round_to_tick(entry)  # Sec 2: compare on the 0.25 tick grid.
    want_high = direction is Direction.LONG
    prices: list[tuple[float, str]] = []
    seen: set[float] = set()

    for swing in swings:
        if swing.is_high != want_high:
            continue  # Sec 5 L6.1: highs for Long, lows for Short.
        level = round_to_tick(swing.level)
        if level in seen or not _profitable(level, e, direction):
            continue
        seen.add(level)
        prices.append((level, SOURCE_SWING))

    for boundary in other_boundaries:
        level = round_to_tick(boundary)
        if level in seen or not _profitable(level, e, direction):
            continue
        seen.add(level)
        prices.append((level, SOURCE_LB_CANDLE))

    # Nearest first.  After deduplication every retained price sits strictly on
    # one side of `E`, so distances are distinct and the order is total; the
    # source label is never a tie-break.
    prices.sort(key=lambda item: abs(item[0] - e))
    return prices


def collect_target_prices(
    swings: Sequence[Swing],
    other_boundaries: Sequence[float],
    entry: float,
    direction: Direction,
) -> list[float]:
    """Sec 5 L6 target candidates: deduplicated, profitable-side only, nearest first.

    Sec 5 L6: "TDO/AMO/quarter targets are excluded in this independent
    baseline" - the two sources above are the complete candidate set.
    """
    return [price for price, _source in _labelled_prices(swings, other_boundaries, entry, direction)]


def choose_target(
    swings: Sequence[Swing],
    other_boundaries: Sequence[float],
    entry: float,
    direction: Direction,
    cost: CostScenario,
) -> Optional[TargetChoice]:
    """The frozen target for ONE LB candidate, or None to reject that candidate.

    Sec 5 L6: "choose the **nearest**.  Do not jump over a nearer target to
    advertise a better reward/risk ratio."  Only the nearest candidate is ever
    gated: if it fails, this candidate is rejected and the engine moves to the
    next ranked LB candidate.  Sec 5 L6: "No target means rejection."

    Sec 5 L6 gate: "With point value `$2`, require
    `(abs(T-E)*2 - round_trip_fees) / planned_loss >= 2.0`, using the active
    cost scenario from section 3."  Sec 3: "Recompute these values in a stress
    run rather than retaining baseline constants" - hence `CostScenario`
    supplies both sides of the comparison and no constant is inlined here.
    """
    candidates = _labelled_prices(swings, other_boundaries, entry, direction)
    if not candidates:
        return None  # Sec 5 L6: "No target means rejection."

    target, source = candidates[0]
    e = round_to_tick(entry)
    rr = cost.reward_risk(e, target)
    if rr < cost.min_reward_risk:
        # Sec 5 L6: "If the nearest target fails, reject that LB candidate and
        # evaluate the next ranked LB candidate" - the engine iterates, never
        # this function.
        return None
    return TargetChoice(
        target=target,
        source=source,
        reward_risk=rr,
        distance_points=abs(target - e),
    )


def target_touched(
    store: MinuteStore,
    target: float,
    direction: Direction,
    start: datetime,
    end: datetime,
    as_of: datetime,
) -> bool:
    """True when a completed minute in [start, end) already touched `T`.

    Sec 5 L6: "A target already touched between snapshot and order submission
    expires the selected setup for that window."  The touch test is the
    inclusive one of Sec 5 L1: "A touch means a recorded minute satisfies
    `low <= level <= high`" - the one inclusive case in this rulebook, and
    symmetric, so `direction` does not enter the predicate.  It is accepted to
    keep the call site and audit record explicit about which setup expired.

    Sec 2: half-open interval by bar START, and only bars whose close was
    published by `as_of`.  Sec 2: "A missing interval before order submission
    expires that window, because it may conceal ... a target touch" - absence of
    a touch here is therefore only meaningful once the caller has verified
    coverage over [start, end); this predicate does not veto on its own.
    """
    t = round_to_tick(target)  # Sec 2: the frozen target lives on the tick grid.
    for bar in store.available(start, end, as_of):
        if bar.covers(t):  # Bar.covers is the inclusive Sec 5 L1 test.
            return True
    return False
