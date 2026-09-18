"""One-minute swing structure for Sec 5 L5 (swing liquidity).

Sec 5 L5: "Use one-minute candles.  A swing high is strictly higher than both
immediate neighbors; a swing low is strictly lower than both.  Confirm only
after the right neighbor closes.  Equal highs/lows do not form a swing."

This module owns swing formation, confirmation timing, consumption and the
candidate choice of L5/L6.  The sweep/reaction test that follows selection
belongs to the engine; the strict-penetration predicate it needs is exported
here so sweep and consumption cannot drift apart.

Sec 2: "A decision at time `t` can access only observations with availability
time at or before `t`."  Every function here takes an explicit `as_of` and
reads through `MinuteStore.available`, which drops bars that had not closed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Optional, Sequence

from .core import Bar, round_to_tick
from .data import MinuteStore

ONE_MINUTE = timedelta(minutes=1)

SwingKind = Literal["high", "low"]
HIGH: SwingKind = "high"
LOW: SwingKind = "low"


@dataclass(frozen=True, slots=True)
class Swing:
    """A confirmed three-candle swing at one-minute resolution.

    `mid_ts` is the START of the extreme candle `j`.  `confirmed_at` is the
    availability time of the confirming candle `j+1`.  Sec 2: "A three-candle
    swing at candle `j` is confirmed only after `j+1` closes."  Sec 6 acceptance
    case: "Swing middle candle appears extreme | Unavailable until next candle
    closes."
    """

    kind: SwingKind
    level: float
    mid_ts: datetime      # start of the extreme candle j
    confirmed_at: datetime  # close/availability time of the confirming candle j+1

    @property
    def is_high(self) -> bool:
        return self.kind == HIGH

    @property
    def is_low(self) -> bool:
        return self.kind == LOW

    @property
    def confirm_ts(self) -> datetime:
        """Start of the confirming candle `j+1` (its close is `confirmed_at`)."""
        return self.confirmed_at - ONE_MINUTE


def crosses(swing: Swing, bar: Bar) -> bool:
    """True when one completed minute strictly penetrates the swing level.

    Sec 5 L5: "strictly crossed its level"; "Strict penetration on the valid
    tick grid is the complete sweep threshold."  Sec 2: "Strict comparisons
    mean strict" - touching the level exactly is not a cross.  Both sides are
    snapped to the tick grid so strictness is judged on that grid (Sec 2).
    """
    if swing.is_high:
        return round_to_tick(bar.high) > swing.level
    return round_to_tick(bar.low) < swing.level


def first_cross(
    store: MinuteStore,
    swing: Swing,
    start: datetime,
    end: datetime,
    as_of: datetime,
) -> Optional[Bar]:
    """First completed minute in [start, end) that strictly crosses `swing`.

    Sec 2: half-open interval, by bar START; only bars available at `as_of`.
    The engine uses this both for the pre-activation sweep check ("If the
    selected swing is swept during 09:29-09:30 ...") and for "the first candle
    inside the window that strictly sweeps the selected swing" (Sec 5 L5).
    """
    for bar in store.available(start, end, as_of):
        if crosses(swing, bar):
            return bar
    return None


def find_swings(
    store: MinuteStore,
    start: datetime,
    end: datetime,
    as_of: datetime,
) -> list[Swing]:
    """Confirmed swings whose extreme candle starts in [start, end).

    Sec 5 L5: "At each window's snapshot, search confirmed swings formed since
    previous day 18:00" - the caller supplies that search interval.  A swing is
    returned only when its confirming candle `j+1` has closed by `as_of`.

    Sec 5 L5 "immediate neighbors" are the adjacent one-minute records.  Sec 2:
    "An exchange-closed interval is unavailable, not a flat candle.  ... Never
    forward-fill" - so when a minute is absent the bars either side of the gap
    are not neighbors and form no swing.
    """
    # The extreme candle may start at `start` (needing the minute before it) or
    # at end-1min (needing the minute at `end` to confirm it).
    scan = store.available(start - ONE_MINUTE, end + ONE_MINUTE, as_of)
    found: list[Swing] = []
    for i in range(1, len(scan) - 1):
        prev, mid, nxt = scan[i - 1], scan[i], scan[i + 1]
        # True adjacency only: a missing minute breaks the three-candle pattern.
        if prev.end != mid.ts or mid.end != nxt.ts:
            continue
        if not (start <= mid.ts < end):
            continue
        confirmed_at = nxt.available_at
        if confirmed_at > as_of:
            continue  # Sec 2 causality; `available` already enforced this.
        # Sec 5 L5: strictly higher/lower than BOTH neighbors; "Equal
        # highs/lows do not form a swing."  One outside candle can be both a
        # swing high and a swing low: the two tests are independent.
        if round_to_tick(mid.high) > round_to_tick(prev.high) and round_to_tick(
            mid.high
        ) > round_to_tick(nxt.high):
            found.append(
                Swing(
                    kind=HIGH,
                    level=round_to_tick(mid.high),  # Sec 2: prices on the tick grid.
                    mid_ts=mid.ts,
                    confirmed_at=confirmed_at,
                )
            )
        if round_to_tick(mid.low) < round_to_tick(prev.low) and round_to_tick(
            mid.low
        ) < round_to_tick(nxt.low):
            found.append(
                Swing(
                    kind=LOW,
                    level=round_to_tick(mid.low),
                    mid_ts=mid.ts,
                    confirmed_at=confirmed_at,
                )
            )
    found.sort(key=lambda s: (s.mid_ts, s.kind))
    return found


def is_consumed(store: MinuteStore, swing: Swing, as_of: datetime) -> bool:
    """True when the swing's level has been strictly crossed since confirmation.

    Sec 5 L5: "A swing is unconsumed if no observation after its confirmation
    has strictly crossed its level.  A cross on the confirmation candle itself
    also marks it consumed if present." - so the scan starts at the confirming
    candle `j+1` itself, not at the minute after it.

    Sec 2: only minutes available at `as_of` are observations; consumption is
    always evaluated as of a snapshot.
    """
    # `end` is exclusive by bar START; `available` drops the minute starting at
    # `as_of`, which has not closed yet.
    return (
        first_cross(store, swing, swing.confirm_ts, as_of + ONE_MINUTE, as_of)
        is not None
    )


def unconsumed_swings(
    store: MinuteStore,
    start: datetime,
    end: datetime,
    as_of: datetime,
) -> list[Swing]:
    """Confirmed, not-yet-crossed swings as of `as_of` (Sec 5 L5, Sec 5 L6.1)."""
    return [s for s in find_swings(store, start, end, as_of) if not is_consumed(store, s, as_of)]


def _nearest(candidates: Sequence[Swing], prefer_higher: bool) -> Optional[Swing]:
    """Nearest level, breaking exact ties by most recent confirmation.

    Sec 5 L5: "Equal-priced candidates use the most recently confirmed swing."
    """
    best: Optional[Swing] = None
    for s in candidates:
        if best is None:
            best = s
            continue
        nearer = s.level > best.level if prefer_higher else s.level < best.level
        if nearer or (s.level == best.level and s.confirmed_at > best.confirmed_at):
            best = s
    return best


def nearest_swing_low_below(swings: Sequence[Swing], price: float) -> Optional[Swing]:
    """Sec 5 L5 Long: "nearest unconsumed confirmed swing low strictly below E".

    Sec 5 L5: "Missing swing rejects this LB candidate" - the caller treats
    None as a rejection of that candidate, not as a day-level veto.
    """
    p = round_to_tick(price)  # Sec 2: compare on the tick grid, strictly.
    return _nearest([s for s in swings if s.is_low and s.level < p], prefer_higher=True)


def nearest_swing_high_above(swings: Sequence[Swing], price: float) -> Optional[Swing]:
    """Sec 5 L5 Short: "nearest unconsumed confirmed swing high strictly above E"."""
    p = round_to_tick(price)
    return _nearest([s for s in swings if s.is_high and s.level > p], prefer_higher=False)
