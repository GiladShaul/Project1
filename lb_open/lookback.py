"""Historical LookBack candles, entry zones and ranking (Sec 5 L3).

Sec 5 L3: "For each anchor construct a historical 30-minute candle beginning at
the local NY timestamp obtained by subtracting 3 hours, 6 hours, 12 hours, one
calendar day, or seven calendar days.  The interval is `[start,start+30
minutes)`, aggregated from minute data.  It must be entirely completed by `S`."

This module owns the candle set for one window: construction, the zone/entry
derivation and the L3 ranking.  It deliberately stops there.  The location gate
(L4), liquidity gate (L5) and target gate (L6) are applied by the engine in that
order over the ranked list, so a rejected alternative stays visible in the log
(Sec 6: "For LB also save all candidates and ranking; hiding drawings must not
erase rejected alternatives").

Sec 2: "A decision at time `t` can access only observations with availability
time at or before `t`."  Every read here is routed through
`MinuteStore.available` with the window's selection snapshot as `as_of`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional, Sequence

from .core import (
    Direction,
    OHLC,
    aggregate,
    ny_datetime,
    round_to_tick,
    to_ny,
    wall_clock_valid,
)
from .data import MinuteStore

# Sec 5 L3: the historical candle is 30 minutes of trade data, half-open (Sec 2).
CANDLE = timedelta(minutes=30)
CANDLE_MINUTES = int(CANDLE.total_seconds() // 60)

# Sec 5 L3 offsets and their tie-break order: "Rank by `abs(E-P)`, then shorter
# offset in the order 3h, 6h, 12h, 24h, 1w."  Each entry is
# (name, wall-clock hours back, calendar days back); exactly one of the two
# magnitudes is non-zero.  Sec 5 L3: "Use local wall-clock date/time
# subtraction, then timezone conversion; do not implement day/week offsets with
# modulo-1440 clock arithmetic" - hence days are carried as calendar days, not
# as 24/168 hours.
OFFSETS: tuple[tuple[str, int, int], ...] = (
    ("3h", 3, 0),
    ("6h", 6, 0),
    ("12h", 12, 0),
    ("24h", 0, 1),
    ("1w", 0, 7),
)

_RANK: dict[str, int] = {name: i for i, (name, _h, _d) in enumerate(OFFSETS)}


@dataclass(frozen=True, slots=True)
class LBCandidate:
    """One eligible historical LB candle with its frozen zone and limit entry.

    Sec 5 L3: "Freeze the chosen zone, entry, swing and target for the window."
    `zone_low`/`zone_high` are the inclusive edges of the entry zone; `entry` is
    the proposed passive limit `E` (Sec 5 L5 submits the retest limit at `E`).
    """

    offset: str          # '3h' | '6h' | '12h' | '24h' | '1w'
    candle: OHLC
    entry: float         # E, on the tick grid (Sec 2)
    zone_low: float
    zone_high: float
    distance: float      # abs(E - P), the primary ranking key


def candidate_start(anchor_ny: datetime, offset: str) -> Optional[datetime]:
    """Local NY start of the historical candle for `offset`, or None if invalid.

    Sec 5 L3: "Use local wall-clock date/time subtraction, then timezone
    conversion."  The subtraction is therefore performed on the naive NY wall
    clock; the result is only afterwards bound to America/New_York.  Day and
    week offsets move the local DATE and keep the wall-clock time, so the 24h
    candidate for a 09:30 anchor is yesterday's dated 09:30-10:00 interval
    (Sec 6 acceptance case: "LB 24h at today's 09:30 | Select yesterday's dated
    interval; never today's 09:30 candle").

    Returns None when the resulting wall clock is ambiguous or non-existent.
    Sec 5 L3: "If a local start is ambiguous/nonexistent during a clock change
    ... remove that candidate.  Do not silently substitute a nearby date."
    """
    for name, hours, days in OFFSETS:
        if name == offset:
            break
    else:
        raise ValueError(f"unknown LB offset {offset!r}; expected one of {[o[0] for o in OFFSETS]}")

    local = to_ny(anchor_ny).replace(tzinfo=None)  # naive wall clock, no zone arithmetic
    if days:
        # Calendar-day subtraction on the DATE, same wall-clock time (Sec 5 L3).
        start_date = local.date() - timedelta(days=days)
        start_time = local.time()
    else:
        shifted = local - timedelta(hours=hours)
        start_date, start_time = shifted.date(), shifted.time()

    if not wall_clock_valid(start_date, start_time):
        return None
    return ny_datetime(start_date, start_time)


def available_candles(
    store: MinuteStore,
    anchor_ny: datetime,
    snapshot_ny: datetime,
) -> list[tuple[str, OHLC]]:
    """The historical LB candles that exist and are complete by `S`, in offset order.

    Sec 5 L3: the candle "must be entirely completed by `S`".  Sec 2: "Require
    complete minute records for ... each selected LB historical interval" and
    "An exchange-closed interval is unavailable, not a flat candle.  Do not
    substitute Friday for an unavailable Sunday LB candle."  A short or absent
    interval therefore drops the candidate; it is never forward-filled and no
    neighbouring date is substituted (Sec 6: "Missing Sunday 24h candidate on
    Monday | Candidate unavailable; do not substitute Friday").
    """
    snapshot = to_ny(snapshot_ny)
    out: list[tuple[str, OHLC]] = []
    for name, _hours, _days in OFFSETS:
        start = candidate_start(anchor_ny, name)
        if start is None:
            continue  # DST-invalid local start (Sec 5 L3)
        # Sec 5 L3: "The interval is `[start,start+30 minutes)`".  Adding a
        # timedelta to a zone-aware datetime is WALL-CLOCK arithmetic in Python,
        # which is the L3 reading ("Use local wall-clock date/time subtraction");
        # across a fall-back hour it would span 90 real minutes, and the
        # exactly-30-records requirement below then drops such an interval
        # (Sec 2: complete minute records).  The L3 offsets never place a start
        # inside a clock change from a 09:30/09:45 anchor, so the two readings
        # cannot diverge here.
        end = start + CANDLE
        if end > snapshot:
            continue  # Sec 5 L3: not "entirely completed by S"
        # Sec 2 causality: only bars whose close was published by the snapshot.
        bars = store.available(start, end, snapshot)
        if len(bars) != CANDLE_MINUTES:
            continue  # incomplete/unavailable interval -> drop the candidate
        candle = aggregate(bars)
        if candle is not None:
            out.append((name, candle))
    return out


def build_candidates(
    store: MinuteStore,
    anchor_ny: datetime,
    snapshot_ny: datetime,
    direction: Direction,
    p: float,
) -> list[LBCandidate]:
    """Ranked LB candidates for one window, before the L4/L5/L6 gates.

    `p` is `P`, "the last completed MNQ minute close at `S`" (Sec 5 L3); the
    caller reads it through `MinuteStore.last_close_at(S)`.

    Sec 5 L3:
      - "Long zone: `[max(open,close), high]`, proposed limit entry `E=high`,
         require `E < P`."
      - "Short zone: `[low,min(open,close)]`, limit entry `E=low`, require `E > P`."
      - "Zero-width wick zones are ineligible."
      - "Rank by `abs(E-P)`, then shorter offset in the order 3h, 6h, 12h, 24h, 1w."
    """
    candidates: list[LBCandidate] = []
    for name, candle in available_candles(store, anchor_ny, snapshot_ny):
        if direction is Direction.LONG:
            # Sec 5 L3: the long zone is the upper wick; a zero-width upper wick
            # is not a zone at all.
            if candle.is_zero_width_up_wick:
                continue
            zone_low = round_to_tick(max(candle.open, candle.close))
            zone_high = round_to_tick(candle.high)
            entry = zone_high
            if not entry < p:
                continue  # Sec 2: strict comparisons mean strict; E == P fails
        else:
            if candle.is_zero_width_down_wick:
                continue
            zone_low = round_to_tick(candle.low)
            zone_high = round_to_tick(min(candle.open, candle.close))
            entry = zone_low
            if not entry > p:
                continue
        # Sec 5 L3: "Zero-width wick zones are ineligible."  The rule is about the
        # zone that is actually frozen, so it is re-tested on the tick-grid edges
        # (Sec 2: "Prices and orders must conform to the instrument's tick grid").
        # On conforming data this is the same verdict as the raw test above; on a
        # feed whose extreme sits off the grid it stops a collapsed zone from
        # being frozen as if it had width.
        if zone_low == zone_high:
            continue
        candidates.append(
            LBCandidate(
                offset=name,
                candle=candle,
                entry=entry,
                zone_low=zone_low,
                zone_high=zone_high,
                # Both E and P lie on the 0.25 grid (Sec 2), so this difference is
                # exact in binary floating point and equal distances tie exactly.
                distance=abs(entry - p),
            )
        )

    # Sec 5 L3 ranking; the secondary key is the fixed offset order above.
    candidates.sort(key=lambda c: (c.distance, _RANK[c.offset]))
    return candidates


def other_candle_boundaries(
    candles: Sequence[tuple[str, OHLC]] | Sequence[LBCandidate],
    chosen: LBCandidate,
) -> list[float]:
    """Target source 2 of Sec 5 L6, for the candidate currently being evaluated.

    Sec 5 L6: "High and low boundaries of the other available historical LB
    candles for this window."  The source set is the AVAILABLE candle set of
    `available_candles`, not the entry-eligible subset returned by
    `build_candidates`.  "Available" is the rulebook's data word throughout -
    Sec 5 L3 "the required trading interval is unavailable, remove that
    candidate", Sec 2 "An exchange-closed interval is unavailable", Sec 6
    "Missing Sunday 24h candidate on Monday | Candidate unavailable" - so a
    candle whose interval was complete is an available LB candle even when its
    own zone cannot be traded (zero-width wick, or `E<P`/`E>P` failing).  Those
    are exactly the candles carrying the structure in front of the trade: for a
    Long every entry-eligible candle has `E=high<P`, so restricting this source
    to them would discard all overhead boundaries and push the target farther
    away - the jump Sec 5 L6 forbids ("choose the **nearest**.  Do not jump
    over a nearer target to advertise a better reward/risk ratio").

    Candles are identified by offset, which is unique within a window, so the
    candle being evaluated is excluded by name.  Prices are returned on the tick
    grid and deduplicated in offset order; Sec 5 L6's own "Deduplicate tick
    prices" then merges them with the swing-based target candidates.

    A sequence of `LBCandidate` is still accepted for backwards compatibility,
    but callers with the store in hand must pass `available_candles(...)`.
    """
    levels: list[float] = []
    seen: set[float] = set()
    for item in candles:
        if isinstance(item, LBCandidate):
            offset, candle = item.offset, item.candle
        else:
            offset, candle = item
        if offset == chosen.offset:
            continue
        for level in (round_to_tick(candle.high), round_to_tick(candle.low)):
            if level not in seen:
                seen.add(level)
                levels.append(level)
    return levels
