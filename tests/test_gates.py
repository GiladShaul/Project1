"""Sec 7 evidence gates and the Sec 7 bootstrap.

Sec 8.4 lesson 7: "A favorable subset is not validation.  The small MSFT
exploratory gain, NVDA final-stock gain and two-trade profitable futures window
cannot justify choosing a symbol, quarter, direction or entry time after
observing results."  The gates must encode that, so a flattering thin sample can
never read as a pass.
"""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lb_open.report import (
    bootstrap_lower_bound,
    evaluate_gates,
    split_sessions,
    summarize,
)


def _trade(d: date, net: float, direction: str = "long", reason: str = "target") -> dict:
    return {"date": d, "direction": direction, "entry": 100.0, "exit": 100.0 + net,
            "exit_reason": reason, "net_pnl": net, "gross_pnl": net + 2.5,
            "fees": 2.5, "ambiguous": False}


# --------------------------------------------------------------------------
# Sec 7 split
# --------------------------------------------------------------------------

def test_split_is_floor_floor_remainder():
    """Sec 7: "oldest 50% development, next 25% validation, newest 25% final
    holdout; use floor for the first two counts and the remainder for holdout"."""
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(200)]
    dev, val, hold = split_sessions(days)
    assert (len(dev), len(val), len(hold)) == (100, 50, 50)
    # 19 sessions: floor(9.5)=9, floor(4.75)=4, remainder 6.
    dev, val, hold = split_sessions([date(2025, 1, 1) + timedelta(days=i) for i in range(19)])
    assert (len(dev), len(val), len(hold)) == (9, 4, 6)
    assert dev + val + hold == [date(2025, 1, 1) + timedelta(days=i) for i in range(19)]


def test_split_is_chronological_not_reordered():
    days = [date(2025, 1, 1) + timedelta(days=i) for i in range(40)]
    dev, val, hold = split_sessions(list(reversed(days)))
    assert dev[0] < dev[-1] < val[0] < val[-1] < hold[0] < hold[-1]


# --------------------------------------------------------------------------
# Sec 8.4 lesson 7: a flattering thin sample is never a pass
# --------------------------------------------------------------------------

def test_perfect_tiny_sample_is_inconclusive_never_pass():
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(8)]
    trades = [_trade(d, 177.50) for d in days]          # 100% win rate
    daily = [{"date": d, "net": 177.50} for d in days]
    gates = evaluate_gates(trades, daily, summarize(trades, daily), None)
    verdicts = {k: str(v) for k, v in gates.items()}
    assert not any("PASS" in v for v in verdicts.values()), verdicts
    assert "INCONCLUSIVE" in verdicts["overall"]


def test_exploratory_flag_downgrades_everything():
    """Sec 2: "A continuous-chart run may be an exploratory screen only, labeled
    as such"."""
    days = [date(2024, 1, 1) + timedelta(days=i) for i in range(600)]
    trades = [_trade(d, 50.0) for d in days]
    daily = [{"date": d, "net": 50.0} for d in days]
    gates = evaluate_gates(trades, daily, summarize(trades, daily), None, exploratory=True)
    assert not any("PASS" in str(v) for v in gates.values())


def test_zero_trade_days_are_retained_in_the_daily_series():
    """Sec 7: the bootstrap runs "over all eligible daily results, including
    zero-trade days"."""
    days = [date(2026, 1, 1) + timedelta(days=i) for i in range(60)]
    daily = [{"date": d, "net": 0.0} for d in days]
    daily[0]["net"] = 100.0
    r = bootstrap_lower_bound([d["net"] for d in daily], 5)
    assert r["n_sessions"] == 60


# --------------------------------------------------------------------------
# Sec 7 bootstrap, specified to the letter
# --------------------------------------------------------------------------

def _independent(daily, L, seed=20260917, R=10000):
    """Re-derived from the Sec 7 text alone, as a check on the module."""
    x = np.asarray(daily, float)
    n = len(x)
    nblocks = -(-n // L)                                  # ceil(n/L)
    rng = np.random.default_rng(seed)                     # seed reset per L
    starts = rng.integers(0, n - L + 1, size=(R, nblocks))
    idx = starts[:, :, None] + np.arange(L)[None, None, :]
    means = x[idx.reshape(R, -1)[:, :n]].mean(axis=1)
    return float(np.sort(means)[499])                     # element 500, one-based


@pytest.mark.parametrize("L", [5, 10])
def test_bootstrap_matches_an_independent_derivation(L):
    daily = list(np.random.default_rng(7).normal(3.0, 40.0, 200))
    assert bootstrap_lower_bound(daily, L)["lower_bound"] == _independent(daily, L)


def test_bootstrap_seed_is_reset_between_block_lengths():
    """Sec 7: "Run separately for L=5 and L=10, resetting the seed"."""
    daily = list(np.random.default_rng(11).normal(0.0, 10.0, 120))
    a = bootstrap_lower_bound(daily, 5)
    b = bootstrap_lower_bound(daily, 5)
    assert a["lower_bound"] == b["lower_bound"]            # deterministic
    assert a["first_resample_start_indices"] == b["first_resample_start_indices"]


def test_bootstrap_uses_the_order_statistic_not_interpolation():
    """Sec 7: "take element 500 in one-based order ... with no interpolation"."""
    daily = list(np.random.default_rng(3).normal(1.0, 25.0, 150))
    r = bootstrap_lower_bound(daily, 5)
    assert r["element_one_based"] == 500
    assert r["interpolation"] == "none"
    assert r["seed"] == 20260917
    assert r["resamples"] == 10000


def test_bootstrap_records_provenance_for_reproduction():
    """Sec 7: "record the random-number generator/library version" and "Publish
    the resample-start indices for exact reproduction"."""
    daily = list(np.random.default_rng(5).normal(1.0, 10.0, 80))
    r = bootstrap_lower_bound(daily, 10)
    assert r["bit_generator"] == "PCG64"
    assert r["numpy_version"] == np.__version__
    assert len(r["resample_start_indices_sha256"]) == 64
    assert r["first_resample_start_indices"]
