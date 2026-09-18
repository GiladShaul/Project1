"""Frozen daily context for LB-OPEN: L1 bias, L2 London filter, L4 range proxy.

Sec 5 L1 freezes the day's directional context at the 09:29 NY snapshot, L2
classifies the overnight London leg against Asia and requires agreement with
that bias, and L4 freezes the overnight range used to locate a candidate entry
in discount/premium terms.  Together they are the daily precheck of Sec 5 L7:
"A failed daily bias/London/data/risk precheck ends the day."

Two rejection channels are deliberately distinct, because Sec 2 requires that
"Predetected incomplete sessions are reported as data-quality exclusions" while
"All remaining eligible no-trade days are included":

  * ``NoTrade``    - the rules looked at complete data and declined the day.
  * ``MissingData`` - mandatory context was absent; the day is not scored.

Every read here routes through ``MinuteStore.available``/``last_close_at`` with
an explicit ``as_of``, per Sec 2: "A decision at time `t` can access only
observations with availability time at or before `t`."
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional, Sequence

from .calendar import REGULAR_CLOSE, REGULAR_OPEN, SNAPSHOT_1, is_eligible_session
from .core import (
    BASELINE,
    Bar,
    CostScenario,
    Direction,
    OHLC,
    aggregate,
    ny_datetime,
    round_to_tick,
    to_ny,
)
from .data import MinuteStore, MissingData

# Sec 5 L2: "Asia is previous day [18:00,00:00) NY ... London is today
# [00:00,06:00)."  Sec 2 lists the same fixed six-hour model windows.
ASIA_START = time(18, 0)
LONDON_START = time(0, 0)
LONDON_END = time(6, 0)

# Sec 5 L1: "completed full regular-session MNQ daily candles in the preceding
# 13 calendar weeks with the same weekday as today".
LOOKBACK_WEEKS = 13

# Sec 5 L2 classification labels.
BULLISH = "bullish"
BEARISH = "bearish"

# Sec 5 L4 region labels for the overnight_range_proxy feature.
EXTREME_DISCOUNT = "extreme_discount"
DISCOUNT = "discount"
DEAD_ZONE = "dead_zone"
PREMIUM = "premium"
EXTREME_PREMIUM = "extreme_premium"


class NoTrade(Exception):
    """A clean, logged "no setup today" - a rule-based rejection, not a data error.

    Sec 2: "All remaining eligible no-trade days are included" in the study,
    unlike data-quality exclusions, which are reported separately.  Sec 6
    requires an "exclusion reason" field, carried here as ``.reason``.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


# ---------------------------------------------------------------------------
# clock helpers
# ---------------------------------------------------------------------------


def snapshot_at(session_date: date, t: time = SNAPSHOT_1) -> datetime:
    """The NY decision instant of a snapshot (Sec 5 L1/L3: S=09:29, then 09:44).

    Sec 5 L3: "Use local wall-clock date/time subtraction, then timezone
    conversion"; 00:00/06:00/09:29/18:00 are never ambiguous NY wall clocks, as
    US transitions occur at 02:00 on a Sunday.
    """
    return ny_datetime(session_date, t)


def _expected_minutes(start: datetime, end: datetime) -> int:
    """Wall-clock elapsed minutes, so DST-shortened/lengthened days count right."""
    return int((end - start).total_seconds() // 60)


def _available_bars(
    store: MinuteStore, start: datetime, end: datetime, as_of: datetime
) -> Optional[tuple[Bar, ...]]:
    """Bars of a fully present, fully published [start, end); else None.

    Sec 2: "Never forward-fill a missing asset"; an absent minute makes the
    interval unavailable rather than shorter.
    """
    expected = _expected_minutes(start, end)
    if expected <= 0:
        return None
    bars = store.available(start, end, as_of)
    return bars if len(bars) == expected else None


def _require_bars(
    store: MinuteStore, start: datetime, end: datetime, as_of: datetime, what: str
) -> tuple[Bar, ...]:
    """Sec 2: "missing mandatory context vetoes the setup"."""
    expected = _expected_minutes(start, end)
    bars = store.available(start, end, as_of) if expected > 0 else ()
    if expected <= 0 or len(bars) != expected:
        raise MissingData(
            f"{what}: {len(bars)}/{expected} minutes in "
            f"[{to_ny(start):%Y-%m-%d %H:%M}, {to_ny(end):%Y-%m-%d %H:%M}) NY "
            f"as of {to_ny(as_of):%Y-%m-%d %H:%M}"
        )
    return bars


def _require_ohlc(
    store: MinuteStore, start: datetime, end: datetime, as_of: datetime, what: str
) -> OHLC:
    return aggregate(_require_bars(store, start, end, as_of, what))


def _asia_start(session_date: date) -> datetime:
    """Previous CALENDAR day 18:00 NY.

    Sec 2's daily cycle is "Previous calendar day 18:00 through current day
    18:00".  These are futures-session hours, so the previous calendar day need
    not be an eligible cash session (a Monday looks back to Sunday 18:00); the
    data must still be complete or Sec 2 vetoes the setup.
    """
    return ny_datetime(session_date - timedelta(days=1), ASIA_START)


# ---------------------------------------------------------------------------
# L1 - frozen daily context
# ---------------------------------------------------------------------------


def snapshot_close(
    store: MinuteStore, session_date: date, as_of: Optional[datetime] = None
) -> float:
    """P0: Sec 5 L1 "after the minute ending 09:29 closes ... that MNQ close".

    The bar starting 09:28 becomes available at 09:29 exactly (Sec 2).  An
    older close is not a substitute: Sec 2 forbids forward-filling, so an
    absent snapshot minute is mandatory context missing.
    """
    if as_of is None:
        as_of = snapshot_at(session_date, SNAPSHOT_1)
    bar = store.last_close_at(as_of)
    if bar is None or bar.end != as_of:
        raise MissingData(
            f"P0: minute ending {to_ny(as_of):%Y-%m-%d %H:%M} NY absent"
        )
    return bar.close


def regular_session_daily(
    store: MinuteStore, d: date, as_of: Optional[datetime] = None
) -> Optional[OHLC]:
    """The regular-session daily candle of `d`, or None if it is not eligible.

    Sec 2: "Historical daily candles for LB are regular-session [09:30,16:00)
    MNQ OHLC, with only completed full sessions eligible."  Sec 5 L1 restates
    "completed full regular-session MNQ daily candles".  A holiday, an early
    close or any absent minute removes the candle - Sec 2: "Missing candidates
    can be removed".
    """
    if not is_eligible_session(d):
        return None
    start = ny_datetime(d, REGULAR_OPEN)
    end = ny_datetime(d, REGULAR_CLOSE)
    # The candle cannot exist before its own session has closed.
    if as_of is None:
        as_of = end
    bars = _available_bars(store, start, end, as_of)
    return aggregate(bars) if bars is not None else None


def lookback_daily_candidates(
    store: MinuteStore, session_date: date, as_of: Optional[datetime] = None
) -> list[OHLC]:
    """Sec 5 L1: the preceding 13 calendar weeks with the same weekday as today.

    The 13 dates are exactly -7, -14, ..., -91 calendar days, which preserves
    the weekday by construction; Sec 5 L3's warning against modulo clock
    arithmetic applies to whole-day subtraction here too.  Sec 5 L1 also binds
    these to "the currently traded dated contract": the store is that
    contract's own history (Sec 2: "Historical features must come from the
    newly selected contract's own available prior history").  Chronological,
    oldest first.
    """
    if as_of is None:
        as_of = snapshot_at(session_date, SNAPSHOT_1)
    out: list[OHLC] = []
    for weeks in range(LOOKBACK_WEEKS, 0, -1):
        d = session_date - timedelta(days=7 * weeks)
        candle = regular_session_daily(store, d, as_of)
        if candle is not None:
            out.append(candle)
    return out


def candidate_prices(candidates: Sequence[OHLC]) -> tuple[float, ...]:
    """Sec 5 L1: "take {open, high, low, close}, deduplicate exact tick prices".

    Deduplication is by tick-grid identity (Sec 2: "Prices and orders must
    conform to the instrument's tick grid"), so two representations of the same
    tick cannot survive as two levels.  Ascending.
    """
    prices = {
        round_to_tick(p)
        for c in candidates
        for p in (c.open, c.high, c.low, c.close)
    }
    return tuple(sorted(prices))


def select_brackets(
    candidates: Sequence[OHLC], p0: float
) -> tuple[Optional[float], Optional[float]]:
    """Sec 5 L1 bracket levels around P0, as (B_low, B_high).

    "B_low: closest candidate strictly below P0.  B_high: closest candidate
    strictly above P0."  Strict means strict: a candidate equal to P0 is
    neither.  Deduplicated prices are distinct, so each side has one nearest.
    """
    prices = candidate_prices(candidates)
    p0 = round_to_tick(p0)
    below = [p for p in prices if p < p0]
    above = [p for p in prices if p > p0]
    return (max(below) if below else None, min(above) if above else None)


def bracket_touches(
    store: MinuteStore,
    session_date: date,
    b_low: float,
    b_high: float,
    as_of: Optional[datetime] = None,
) -> tuple[bool, bool]:
    """Sec 5 L1: observe [00:00,09:29) today; did each level get touched?

    "A touch means a recorded minute satisfies low <= level <= high" - the one
    inclusive comparison in this rulebook (Bar.covers).  Sec 5 L1: "Selecting a
    level at 09:29 and examining earlier observations is causal because all
    inputs already exist."  The window is mandatory context: an absent minute
    could conceal a touch and flip the classification, so it is required
    complete rather than scanned as-is.
    """
    if as_of is None:
        as_of = snapshot_at(session_date, SNAPSHOT_1)
    start = ny_datetime(session_date, LONDON_START)
    bars = _require_bars(store, start, as_of, as_of, "L1 touch window [00:00,09:29)")
    touched_low = any(b.covers(b_low) for b in bars)
    touched_high = any(b.covers(b_high) for b in bars)
    return touched_low, touched_high


# ---------------------------------------------------------------------------
# L2 - London filter
# ---------------------------------------------------------------------------


def classify_london(
    store: MinuteStore, session_date: date, as_of: Optional[datetime] = None
) -> Optional[str]:
    """Sec 5 L2: classify London against Asia; None means "neither or both".

    Asia = previous day [18:00,00:00) -> AH/AL.  London = today [00:00,06:00)
    -> LH/LL and last close LC.  Sec 5 L2: "Equality does not satisfy a strict
    take/break condition", so every comparison below is strict.  Sec 5 L2:
    "Freeze the result at the first snapshot" - the caller passes as_of=09:29
    and both windows are long closed by then.
    """
    if as_of is None:
        as_of = snapshot_at(session_date, SNAPSHOT_1)
    midnight = ny_datetime(session_date, LONDON_START)
    asia = _require_ohlc(store, _asia_start(session_date), midnight, as_of,
                         "L2 Asia [18:00,00:00)")
    london = _require_ohlc(store, midnight, ny_datetime(session_date, LONDON_END),
                           as_of, "L2 London [00:00,06:00)")

    ah, al = asia.high, asia.low
    lh, ll, lc = london.high, london.low, london.close

    # Sec 5 L2 formulas, transcribed literally.
    bullish = (ll < al and lh < ah and lc > al) or (lh > ah and lc > ah)
    bearish = (lh > ah and ll > al and lc < ah) or (ll < al and lc < al)

    if bullish == bearish:  # Sec 5 L2: "Neither or both = no trade."
        return None
    return BULLISH if bullish else BEARISH


# ---------------------------------------------------------------------------
# L4 - explicit price-location proxy (overnight_range_proxy)
# ---------------------------------------------------------------------------


def overnight_range(
    store: MinuteStore, session_date: date, as_of: Optional[datetime] = None
) -> tuple[float, float]:
    """Sec 5 L4: freeze (RH, RL) from previous day 18:00 through 09:29 today.

    The interval is half-open by bar start, so its last minute is the one
    starting 09:28 - the same close that fixes P0.  Sec 5 L4: "Require a
    positive range"; a degenerate range is a rule-based veto on a complete
    record, hence NoTrade rather than MissingData.  Raised only at the first
    snapshot: Sec 5 L3 keeps the proxy frozen for the second window.
    """
    if as_of is None:
        as_of = snapshot_at(session_date, SNAPSHOT_1)
    bars = _require_bars(store, _asia_start(session_date), as_of, as_of,
                         "L4 overnight range [prev 18:00,09:29)")
    window = aggregate(bars)
    if not window.high > window.low:
        raise NoTrade(
            f"overnight range not positive: RH={window.high} RL={window.low}"
        )
    return window.high, window.low


def region_of(x: float) -> str:
    """Sec 5 L4 region table for a normalized entry price.

    Extreme Discount x <= 0.20; Discount 0.20 < x < 0.40; Dead Zone
    0.40 <= x <= 0.60; Premium 0.60 < x < 0.80; Extreme Premium x >= 0.80.
    "Values outside [0,1] remain in the corresponding extreme" - the open-ended
    first and last tests already absorb them.
    """
    if x <= 0.20:
        return EXTREME_DISCOUNT
    if x < 0.40:
        return DISCOUNT
    if x <= 0.60:
        return DEAD_ZONE
    if x < 0.80:
        return PREMIUM
    return EXTREME_PREMIUM


# ---------------------------------------------------------------------------
# the frozen context
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DailyContext:
    """Sec 5 L1: "Freeze levels and direction for both windows."

    Sec 5 L3 keeps daily bias, London context and range proxy frozen when the
    second window refreshes only current price and LB selection, so this object
    is immutable and built exactly once per session, at 09:29.
    """

    session_date: date
    p0: float
    b_low: float
    b_high: float
    bias: Direction
    rh: float
    rl: float
    london: str
    candidates_used: int

    @property
    def snapshot(self) -> datetime:
        """The 09:29 NY instant at which everything here became known."""
        return snapshot_at(self.session_date, SNAPSHOT_1)

    @property
    def active_level(self) -> float:
        """Sec 5 L1: "The nearer of the two levels is reported as 'active'; it
        does not override this directional rule."  Report-only; an exact tie in
        distance is reported as the lower level.
        """
        return self.b_low if (self.p0 - self.b_low) <= (self.b_high - self.p0) else self.b_high

    def normalized(self, entry: float) -> float:
        """Sec 5 L4: x = (E - RL) / (RH - RL).  RH > RL was required at L4."""
        return (entry - self.rl) / (self.rh - self.rl)

    def location(self, entry: float) -> str:
        """The Sec 5 L4 region label of a candidate entry."""
        return region_of(self.normalized(entry))

    def location_ok(self, entry: float, direction: Optional[Direction] = None) -> bool:
        """Sec 5 L4: "Long requires x < 0.40; Short requires x > 0.60."

        Tested on x directly, not via the region label: x = 0.40 is Dead Zone
        and also fails the strict long test, and both bounds are strict.
        """
        if direction is None:
            direction = self.bias
        x = self.normalized(entry)
        return x < 0.40 if direction is Direction.LONG else x > 0.60


def build_daily_context(
    store: MinuteStore, session_date: date, cost: CostScenario = BASELINE
) -> DailyContext:
    """Freeze the Sec 5 L1/L2/L4 daily context at the 09:29 NY snapshot.

    Raises ``NoTrade`` for a rule-based rejection and ``MissingData`` for absent
    mandatory context; Sec 2 requires the two be reported separately.

    `cost` is the active scenario for the session's audit record (Sec 6).  No
    gate here depends on it: Sec 3's per-entry risk gate is measured against
    "current closed equity" and Sec 5 L6's reward/risk gate needs E and T, so
    both belong to the session runner, not to the frozen context.

    Coverage of the execution window [09:29,12:00) is Sec 2's separate
    pre-inventory step; it cannot be inspected here without reading past 09:29.
    """
    as_of = snapshot_at(session_date, SNAPSHOT_1)

    # Sec 2: "Trade only on full regular US cash-market sessions."
    if not is_eligible_session(session_date):
        raise NoTrade(f"{session_date}: not a full regular US cash session")

    # Sec 2: "Predetected incomplete sessions are reported as data-quality
    # exclusions" - so the whole mandatory pre-snapshot block is checked before
    # any rule runs, and a data gap never masquerades as a rule-based no-trade.
    # Sec 5 L1: "Required context is complete before this snapshot."
    _require_bars(store, _asia_start(session_date), as_of, as_of,
                  "L1 pre-snapshot context [prev 18:00,09:29)")

    p0 = snapshot_close(store, session_date, as_of)

    candidates = lookback_daily_candidates(store, session_date, as_of)
    # Sec 5 L1: "Need at least one eligible historical candle; otherwise no trade."
    if not candidates:
        raise NoTrade(
            "no eligible historical daily candle in the preceding "
            f"{LOOKBACK_WEEKS} calendar weeks"
        )

    b_low, b_high = select_brackets(candidates, p0)
    # Sec 5 L1: "Missing either level means no trade."
    if b_low is None or b_high is None:
        side = "B_low" if b_low is None else "B_high"
        raise NoTrade(f"{side} absent: no candidate strictly beyond P0={p0}")

    touched_low, touched_high = bracket_touches(store, session_date, b_low, b_high, as_of)
    # Sec 5 L1: "Both or neither touched: no trade for the day."
    if touched_low == touched_high:
        which = "both brackets touched" if touched_low else "neither bracket touched"
        raise NoTrade(f"{which} in [00:00,09:29)")
    # Sec 5 L1: "Only B_low touched: daily bias Long.  Only B_high: Short."
    bias = Direction.LONG if touched_low else Direction.SHORT

    london = classify_london(store, session_date, as_of)
    if london is None:
        raise NoTrade("London classification is neither bullish nor bearish")
    # Sec 5 L2: "Require the classification to agree with daily bias."
    if (london == BULLISH) != (bias is Direction.LONG):
        raise NoTrade(f"London {london} disagrees with daily bias {bias.name}")

    rh, rl = overnight_range(store, session_date, as_of)

    return DailyContext(
        session_date=session_date,
        p0=p0,
        b_low=b_low,
        b_high=b_high,
        bias=bias,
        rh=rh,
        rl=rl,
        london=london,
        candidates_used=len(candidates),
    )
