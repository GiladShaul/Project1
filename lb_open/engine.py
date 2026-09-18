"""LB-OPEN session engine: the Sec 5 L7 window/order state machine.

Sec 5 preamble: "This is a fully specified replacement for the source's
discretionary choices.  Its OHLC levels, range proxy and reaction rule are
hypotheses.  It is not claimed to reproduce the unavailable proprietary
indicators or the trader's judgment."

Sec 8.6 records LB-OPEN as "separately specified, unimplemented and untested".
This module is that implementation.  It produces evidence; it does not assert an
edge.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional

from . import calendar as cal
from .context import DailyContext, NoTrade, build_daily_context
from .core import (
    NY,
    Bar,
    CostScenario,
    Direction,
    POINT_VALUE,
    round_to_tick,
    ny_datetime,
    to_ny,
)
from .data import MinuteStore, MissingData
from .execution import (
    Fill,
    FillResult,
    flatten_price,
    resolve_entry_minute,
    resolve_open_position,
    trade_pnl,
)
from .lookback import (
    LBCandidate,
    available_candles,
    build_candidates,
    other_candle_boundaries,
)
from .swings import Swing, nearest_swing_high_above, nearest_swing_low_below, unconsumed_swings
from .targets import TargetChoice, choose_target, target_touched


class DataQuality(Exception):
    """Sec 2: a predetected incomplete session, reported as a data-quality
    exclusion with dates and coverage ratios - never silently included as a
    zero-trade day."""


class RunInvalid(Exception):
    """Sec 2: "An unexpected missing execution interval while an order or position
    is active invalidates that run until the history is repaired: do not bridge
    the gap, invent an exit, or remove only the affected losing/winning trade."
    """


@dataclass(slots=True)
class Setup:
    """A frozen window selection (Sec 5 L3: "Freeze the chosen zone, entry, swing
    and target for the window")."""

    window: int
    direction: Direction
    candidate: LBCandidate
    swing: Swing
    target: TargetChoice
    entry: float
    stop: float
    snapshot: datetime


@dataclass(slots=True)
class Trade:
    date: date
    window: int
    direction: str
    entry: float
    exit: float
    entry_ts: datetime
    exit_ts: datetime
    exit_reason: str
    gross_pnl: float
    fees: float
    net_pnl: float
    ambiguous: bool
    offset: str
    target: float
    stop: float
    swing_level: float
    reward_risk: float


@dataclass(slots=True)
class SessionResult:
    date: date
    trades: list[Trade] = field(default_factory=list)
    no_trade_reason: Optional[str] = None
    excluded: bool = False          # Sec 2 data-quality exclusion
    coverage_ratio: float = 1.0
    candidates_logged: list[dict] = field(default_factory=list)
    context: Optional[DailyContext] = None

    @property
    def net_pnl(self) -> float:
        return sum(t.net_pnl for t in self.trades)


def _window_bounds(session_date: date, window: int) -> tuple[datetime, datetime, datetime, datetime]:
    """Return (snapshot, anchor, window_start, window_end) in NY for a window."""
    if window == 1:
        return (
            ny_datetime(session_date, cal.SNAPSHOT_1),
            ny_datetime(session_date, cal.WINDOW_1_START),
            ny_datetime(session_date, cal.WINDOW_1_START),
            ny_datetime(session_date, cal.WINDOW_1_END),
        )
    return (
        ny_datetime(session_date, cal.SNAPSHOT_2),
        ny_datetime(session_date, cal.WINDOW_2_START),
        ny_datetime(session_date, cal.WINDOW_2_START),
        ny_datetime(session_date, cal.WINDOW_2_END),
    )


def select_setup(
    store: MinuteStore,
    ctx: DailyContext,
    session_date: date,
    window: int,
    cost: CostScenario,
    log: list[dict],
) -> Optional[Setup]:
    """Rank LB candidates and apply the L3 gate order.

    Sec 5 L3: "Apply the location, liquidity and target gates below in that order;
    select the first candidate satisfying all of them.  If none qualifies, that
    window has no setup."
    """
    snapshot, anchor, _, _ = _window_bounds(session_date, window)
    direction = ctx.bias

    last = store.last_close_at(snapshot)
    if last is None:
        raise MissingData(f"no completed minute close at {to_ny(snapshot):%H:%M} NY")
    p = last.close

    candidates = build_candidates(store, anchor, snapshot, direction, p)
    # Sec 5 L6 source 2 is "the other AVAILABLE historical LB candles for this
    # window", which is the complete-by-`S` candle set, not the entry-eligible
    # subset that survives the L3 zone rules.
    avail = available_candles(store, anchor, snapshot)

    # Sec 5 L5 swing history: "search confirmed swings formed since previous day 18:00".
    swing_start = ny_datetime(session_date - timedelta(days=1), time(18, 0))
    swings = unconsumed_swings(store, swing_start, snapshot, snapshot)

    for cand in candidates:
        record = {
            "window": window, "offset": cand.offset, "entry": cand.entry,
            "distance": cand.distance, "rejected": None,
        }

        # Gate 1 - location (Sec 5 L4).
        if not ctx.location_ok(cand.entry, direction):
            record["rejected"] = f"location x={ctx.normalized(cand.entry):.3f} ({ctx.location(cand.entry)})"
            log.append(record)
            continue

        # Gate 2 - liquidity (Sec 5 L5).  "Missing swing rejects this LB candidate."
        if direction is Direction.LONG:
            swing = nearest_swing_low_below([s for s in swings if s.kind == "low"], cand.entry)
        else:
            swing = nearest_swing_high_above([s for s in swings if s.kind == "high"], cand.entry)
        if swing is None:
            record["rejected"] = "no unconsumed swing"
            log.append(record)
            continue

        # Gate 3 - structural target and reward/risk (Sec 5 L6).
        others = other_candle_boundaries(avail, cand)
        directional = [s for s in swings if s.kind == ("high" if direction is Direction.LONG else "low")]
        target = choose_target(directional, others, cand.entry, direction, cost)
        if target is None:
            record["rejected"] = "no target passing reward/risk gate"
            log.append(record)
            continue

        record["rejected"] = None
        record["swing"] = swing.level
        record["target"] = target.target
        record["reward_risk"] = target.reward_risk
        log.append(record)

        stop = round_to_tick(cand.entry - direction.d * cost.initial_stop_points)
        return Setup(
            window=window, direction=direction, candidate=cand, swing=swing,
            target=target, entry=cand.entry, stop=stop, snapshot=snapshot,
        )

    return None


def _reaction_qualifies(bar: Bar, setup: Setup) -> bool:
    """Sec 5 L5 reaction test - all conditions at once.

    Long: "its range intersects the frozen LB zone; its low is strictly below the
    frozen swing low; its close is strictly above both that swing and E."
    Short: mirrored.
    """
    cand = setup.candidate
    intersects = bar.low <= cand.zone_high and bar.high >= cand.zone_low
    if not intersects:
        return False
    if setup.direction is Direction.LONG:
        return (
            bar.low < setup.swing.level
            and bar.close > setup.swing.level
            and bar.close > setup.entry
        )
    return (
        bar.high > setup.swing.level
        and bar.close < setup.swing.level
        and bar.close < setup.entry
    )


def _sweeps(bar: Bar, setup: Setup) -> bool:
    """Sec 5 L5: "Strict penetration on the valid tick grid is the complete sweep
    threshold."  """
    if setup.direction is Direction.LONG:
        return bar.low < setup.swing.level
    return bar.high > setup.swing.level


def run_window(
    store: MinuteStore,
    ctx: DailyContext,
    session_date: date,
    window: int,
    cost: CostScenario,
    equity: float,
    log: list[dict],
) -> tuple[Optional[Setup], Optional[datetime], Optional[str]]:
    """Attempt one window.  Returns (setup, submission_time, expiry_reason).

    A non-None submission_time means an order was submitted, which under Sec 5 L7
    disables the second window regardless of what happens to that order.
    """
    snapshot, _, w_start, w_end = _window_bounds(session_date, window)

    setup = select_setup(store, ctx, session_date, window, cost, log)
    if setup is None:
        return None, None, f"window{window}: no qualifying LB candidate"

    # Sec 3 per-entry risk gate, evaluated against current CLOSED equity.
    if cost.planned_loss > cost.per_entry_risk_fraction * equity:
        return None, None, f"window{window}: per-entry risk gate (equity ${equity:,.2f})"

    # Sec 5 L5: "If the selected swing is swept during 09:29-09:30 for window one,
    # or 09:44-09:45 for window two, that window's setup expires before activation."
    pre = store.bar_starting(snapshot)
    if pre is None:
        return None, None, f"window{window}: missing pre-activation minute"
    if _sweeps(pre, setup):
        return None, None, f"window{window}: swing swept before activation"

    # Sec 5 L5: "the first candle inside the window that strictly sweeps the selected
    # swing is the one opportunity: if it fails the reaction test, this window's
    # setup expires.  A later reclaim cannot resurrect it."
    # Sec 5 L5: confirmation candle must be "wholly inside the entry window";
    # Sec 5 L5: "A candle closing at 09:45 cannot authorize a first-window entry."
    last_confirm_start = w_end - timedelta(minutes=2)
    ts = w_start
    while ts <= last_confirm_start:
        bar = store.bar_starting(ts)
        if bar is None:
            # Sec 2: "A missing interval before order submission expires that window,
            # because it may conceal the first sweep or a target touch."
            return None, None, f"window{window}: missing minute {to_ny(ts):%H:%M} before submission"
        if _sweeps(bar, setup):
            if not _reaction_qualifies(bar, setup):
                return None, None, f"window{window}: sweep failed reaction test at {to_ny(ts):%H:%M}"
            submitted = bar.end  # order submitted after the confirming candle closes
            # Sec 5 L6: "A target already touched between snapshot and order
            # submission expires the selected setup for that window."
            if target_touched(store, setup.target.target, setup.direction,
                              snapshot, submitted, submitted):
                return None, None, f"window{window}: target touched before submission"
            return setup, submitted, None
        ts += timedelta(minutes=1)

    return None, None, f"window{window}: no sweep inside window"


def manage_order(
    store: MinuteStore,
    setup: Setup,
    submitted: datetime,
    expiry: datetime,
    session_date: date,
    cost: CostScenario,
) -> Optional[Trade]:
    """Work a resting entry limit to expiry, then manage any resulting position.

    Sec 5 L7: "First-window pending orders expire at 09:45.  Second-window orders
    expire at 10:00.  No fill at or after expiry is eligible in simulation."
    Sec 5 L7: "Any filled position is managed to its own exit; entry-window end
    does not force-close it."
    """
    noon = ny_datetime(session_date, cal.MANDATORY_FLAT)
    entered_at: Optional[datetime] = None
    pending_exit: Optional[FillResult] = None
    ambiguous = False

    ts = submitted
    while ts < expiry:
        bar = store.bar_starting(ts)
        if bar is None:
            raise RunInvalid(f"missing minute {to_ny(ts):%Y-%m-%d %H:%M} NY with an order active")
        entered, result = resolve_entry_minute(
            bar, setup.direction, setup.entry, setup.stop, setup.target.target, cost
        )
        if entered:
            entered_at = ts
            ambiguous = result.ambiguous
            if result.kind is not Fill.NONE:
                pending_exit = result
            break
        ts += timedelta(minutes=1)

    if entered_at is None:
        return None  # Sec 5 L7: expired unfilled.  The window is still "submitted".

    def close(exit_ts: datetime, result: FillResult, reason: str) -> Trade:
        gross, fees, net = trade_pnl(setup.entry, result.price, setup.direction, cost)
        return Trade(
            date=session_date, window=setup.window,
            direction=setup.direction.name.lower(),
            entry=setup.entry, exit=result.price,
            entry_ts=entered_at, exit_ts=exit_ts, exit_reason=reason,
            gross_pnl=gross, fees=fees, net_pnl=net,
            ambiguous=ambiguous or result.ambiguous,
            offset=setup.candidate.offset, target=setup.target.target,
            stop=setup.stop, swing_level=setup.swing.level,
            reward_risk=setup.target.reward_risk,
        )

    if pending_exit is not None:
        return close(entered_at, pending_exit, pending_exit.kind.value)

    # Manage the open position minute by minute to the noon flatten (Sec 3).
    ts = entered_at + timedelta(minutes=1)
    while ts < noon:
        bar = store.bar_starting(ts)
        if bar is None:
            raise RunInvalid(f"missing minute {to_ny(ts):%Y-%m-%d %H:%M} NY with a position open")
        result = resolve_open_position(
            bar, setup.direction, setup.stop, setup.target.target, cost
        )
        if result.kind is not Fill.NONE:
            return close(ts, result, result.kind.value)
        ts += timedelta(minutes=1)

    # Sec 3: "Mandatory flat time 12:00 NY; cancel entries and close any remaining
    # position at the first executable price."
    noon_bar = store.bar_starting(noon)
    if noon_bar is None:
        raise RunInvalid(f"missing noon flatten minute on {session_date}")
    price = flatten_price(noon_bar, setup.direction, cost)
    return close(noon, FillResult(Fill.FLAT, price), "noon_flat")


def run_session(
    store: MinuteStore,
    session_date: date,
    cost: CostScenario,
    equity: Optional[float] = None,
) -> SessionResult:
    """Run one eligible session end to end (Sec 5 L7)."""
    equity = cost.starting_equity if equity is None else equity
    res = SessionResult(date=session_date)

    if not cal.is_eligible_session(session_date):
        res.no_trade_reason = "not a full regular session"
        res.excluded = True
        return res

    # Sec 2: "Inventory execution coverage before inspecting trade outcomes.  Every
    # scored session requires complete MNQ execution records from 09:29 through the
    # 12:00 flatten execution."
    exec_start = ny_datetime(session_date, cal.SNAPSHOT_1)
    exec_end = ny_datetime(session_date, cal.MANDATORY_FLAT) + timedelta(minutes=1)
    cov = store.coverage(exec_start, exec_end)
    res.coverage_ratio = cov.ratio
    if not cov.complete:
        res.excluded = True
        res.no_trade_reason = f"incomplete execution coverage {cov.present}/{cov.expected}"
        return res

    # Sec 5 L7: "A failed daily bias/London/data/risk precheck ends the day."
    try:
        ctx = build_daily_context(store, session_date, cost)
    except NoTrade as e:
        res.no_trade_reason = str(e)
        return res
    except MissingData as e:
        res.excluded = True
        res.no_trade_reason = f"missing context data: {e}"
        return res
    res.context = ctx

    reasons: list[str] = []
    for window in (1, 2):
        try:
            setup, submitted, reason = run_window(
                store, ctx, session_date, window, cost, equity, res.candidates_logged
            )
        except MissingData as e:
            reasons.append(f"window{window}: {e}")
            break
        if reason:
            reasons.append(reason)
        if setup is None or submitted is None:
            # Sec 5 L7: "A first-window setup that expires or never confirms without
            # submitting an order may be followed by the second window."
            continue

        expiry = ny_datetime(
            session_date, cal.WINDOW_1_END if window == 1 else cal.WINDOW_2_END
        )
        trade = manage_order(store, setup, submitted, expiry, session_date, cost)
        if trade is not None:
            res.trades.append(trade)
        else:
            reasons.append(f"window{window}: order expired unfilled")
        # Sec 5 L7: "If ANY first-window entry order was submitted, the second window
        # is disabled, whether that order fills, remains pending, is canceled, or is
        # rejected."  Sec 5 L7: "Only one entry order may be submitted per day."
        break

    if not res.trades:
        res.no_trade_reason = "; ".join(reasons) if reasons else "no setup"
    return res


def run_study(
    store: MinuteStore,
    dates: list[date],
    cost: CostScenario,
    carry_equity: bool = True,
) -> list[SessionResult]:
    """Run consecutive sessions with carried closed equity (Sec 3)."""
    equity = cost.starting_equity
    out: list[SessionResult] = []
    for d in dates:
        res = run_session(store, d, cost, equity)
        if carry_equity:
            equity += res.net_pnl
        out.append(res)
    return out
