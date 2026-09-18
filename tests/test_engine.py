"""End-to-end engine tests on a fully specified synthetic session.

The fixture's geometry is chosen so that every Sec 5 gate has a hand-derivable
answer; these tests assert those answers rather than whatever the code happens
to produce.
"""

from __future__ import annotations

import sys
from datetime import date, time, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fixtures import build_long_session, explicit
from lb_open.core import BASELINE, STRESS, UTC, Bar, Direction, ny_datetime
from lb_open.data import MinuteStore
from lb_open.engine import RunInvalid, run_session
from lb_open.context import build_daily_context

SESSION = date(2026, 9, 15)


@pytest.fixture(scope="module")
def session():
    return build_long_session()[0]


# --------------------------------------------------------------------------
# Sec 5 L1 / L2 / L4 frozen context
# --------------------------------------------------------------------------

def test_daily_context_matches_designed_geometry(session):
    ctx = build_daily_context(session, SESSION, BASELINE)
    # Sec 5 L1: P0 is the close of the minute ENDING 09:29, i.e. the bar starting
    # 09:28.  The bar starting 09:29 only closes at 09:30 and is not yet available.
    assert ctx.p0 == 19997.0
    assert (ctx.b_low, ctx.b_high) == (19970.0, 20060.0)   # Sec 5 L1 brackets
    assert ctx.bias is Direction.LONG                       # only B_low touched
    assert ctx.london == "bullish"                          # Sec 5 L2, agrees with bias
    assert (ctx.rh, ctx.rl) == (20020.0, 19881.0)           # Sec 5 L4 overnight range
    # Sec 5 L4: Long requires x < 0.40.
    assert ctx.normalized(19930.0) == pytest.approx(0.35252, abs=1e-4)
    assert ctx.location(19930.0) == "discount"
    assert ctx.location_ok(19930.0, Direction.LONG)
    assert not ctx.location_ok(19930.0, Direction.SHORT)


# --------------------------------------------------------------------------
# Full session, both declared cost scenarios
# --------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cost,expected_net,expected_rr",
    [(BASELINE, 177.50, 2.817), (STRESS, 175.00, 2.612)],
)
def test_single_qualifying_long_trade(session, cost, expected_net, expected_rr):
    r = run_session(session, SESSION, cost)
    assert not r.excluded and r.coverage_ratio == 1.0
    assert len(r.trades) == 1
    t = r.trades[0]

    assert t.direction == "long"
    assert t.offset == "3h"            # Sec 5 L3 ranking by abs(E-P) picks the 3h candle
    assert t.entry == 19930.0          # E is the candle high; limits fill at E, no improvement
    assert t.stop == 19900.0           # Sec 5 L6: E - 30
    assert t.target == 20020.0         # nearest unconsumed swing high above E
    assert t.swing_level == 19900.0    # Sec 5 L5 nearest unconsumed swing low below E
    assert t.exit_reason == "target"
    assert t.reward_risk == pytest.approx(expected_rr, abs=1e-3)
    assert t.net_pnl == pytest.approx(expected_net, abs=1e-6)
    # Sec 3: gross is fill-to-fill; fees are separate and never double-counted.
    assert t.gross_pnl == pytest.approx((t.exit - t.entry) * 2.0)
    assert t.fees == cost.round_trip_fees
    assert not t.ambiguous


def test_confirmation_is_not_before_0931(session):
    """Sec 5 L5: "Earliest first-window confirmation is 09:31"."""
    r = run_session(session, SESSION, BASELINE)
    entry_ny = r.trades[0].entry_ts.astimezone(ny_datetime(SESSION, time(0, 0)).tzinfo)
    assert entry_ny.time() >= time(9, 31)


def test_only_one_entry_order_per_day(session):
    """Sec 5 L7: "Only one entry order may be submitted per day.""" ""
    r = run_session(session, SESSION, BASELINE)
    assert len(r.trades) == 1
    assert all(c["window"] == 1 for c in r.candidates_logged)


def test_every_candidate_rejection_is_logged(session):
    """Sec 6: "For LB also save all candidates and ranking; hiding drawings must
    not erase rejected alternatives"."""
    r = run_session(session, SESSION, BASELINE)
    assert r.candidates_logged
    accepted = [c for c in r.candidates_logged if not c.get("rejected")]
    assert len(accepted) == 1
    assert accepted[0]["offset"] == "3h"


# --------------------------------------------------------------------------
# Sec 5 L5 expiry paths
# --------------------------------------------------------------------------

def _replace(store: MinuteStore, session_date: date, at: time,
             row: tuple[float, float, float, float]) -> MinuteStore:
    ts = ny_datetime(session_date, at).astimezone(UTC)
    kept = [b for b in store if b.ts != ts]
    return MinuteStore(kept + explicit(ny_datetime(session_date, at), [row]))


def test_swing_swept_before_activation_expires_the_window(session):
    """Sec 6: "LB selected swing swept before window activation | Expire that
    window; a later reclaim cannot qualify"."""
    # Drive the 09:29 minute strictly below the 19900 swing.
    tampered = _replace(session, SESSION, time(9, 29), (19997, 20002, 19890, 20000))
    r = run_session(tampered, SESSION, BASELINE)
    assert not r.trades
    assert "swept before activation" in r.no_trade_reason


def test_sweep_without_reclaim_expires_the_window(session):
    """Sec 6: "LB swing swept without same-candle reclaim | Selected setup
    expires for that window"."""
    # 09:31 still sweeps 19900 but closes below E, failing the reaction test.
    tampered = _replace(session, SESSION, time(9, 31), (19955, 19958, 19890, 19905))
    r = run_session(tampered, SESSION, BASELINE)
    assert not r.trades
    assert "failed reaction test" in r.no_trade_reason


def test_later_reclaim_cannot_resurrect_an_expired_setup(session):
    """Sec 5 L5: "A later reclaim cannot resurrect it"."""
    tampered = _replace(session, SESSION, time(9, 31), (19955, 19958, 19890, 19905))
    # 09:33 would be a textbook sweep-and-reclaim, but the one opportunity is spent.
    tampered = _replace(tampered, SESSION, time(9, 33), (19905, 19960, 19885, 19950))
    r = run_session(tampered, SESSION, BASELINE)
    assert not r.trades


# --------------------------------------------------------------------------
# Sec 2 data integrity
# --------------------------------------------------------------------------

def test_missing_minute_while_order_active_invalidates_the_run(session):
    """Sec 6: "Missing minute while an order or position is active | Run invalid
    until history is repaired; no invented P&L".

    Sec 2 requires the gap to be caught twice over.  A gap inside the execution
    window is PREDETECTED by the Sec 2 inventory and becomes a data-quality
    exclusion (see the next test).  RunInvalid is the second guard, for a gap
    that reaches the order loop without having been inventoried, and it must
    never be satisfied by bridging the gap or inventing an exit.
    """
    from lb_open.engine import manage_order, run_window

    ctx = build_daily_context(session, SESSION, BASELINE)
    setup, submitted, reason = run_window(
        session, ctx, SESSION, 1, BASELINE, BASELINE.starting_equity, []
    )
    assert setup is not None and submitted is not None

    drop = ny_datetime(SESSION, time(9, 40)).astimezone(UTC)
    tampered = MinuteStore([b for b in session if b.ts != drop])
    expiry = ny_datetime(SESSION, time(9, 45))
    with pytest.raises(RunInvalid):
        manage_order(tampered, setup, submitted, expiry, SESSION, BASELINE)


def test_incomplete_execution_coverage_is_an_exclusion_not_a_zero(session):
    """Sec 2: "Predetected incomplete sessions are reported as data-quality
    exclusions ... they are not silently included as zero-trade days"."""
    drop = ny_datetime(SESSION, time(11, 0)).astimezone(UTC)
    tampered = MinuteStore([b for b in session if b.ts != drop])
    r = run_session(tampered, SESSION, BASELINE)
    assert r.excluded
    assert r.coverage_ratio < 1.0
    assert not r.trades
    assert "incomplete execution coverage" in r.no_trade_reason


def test_limit_that_never_trades_through_does_not_fill(session):
    """Sec 3: "Require trading one tick through the limit"; Sec 6: "Limit reaction
    candle traded through entry earlier | No retrospective entry in that candle"."""
    # Hold every post-confirmation minute at or above E, so the limit never fills.
    tampered = session
    for m in range(32, 45):
        tampered = _replace(tampered, SESSION, time(9, m), (19945, 19960, 19930, 19950))
    r = run_session(tampered, SESSION, BASELINE)
    assert not r.trades
    assert "expired unfilled" in r.no_trade_reason
