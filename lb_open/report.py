"""Sec 7 evidence gates and the Sec 6 reporting fields.

This module is pure statistics over a list of closed trades and a list of daily
session results.  It imports nothing from the engine on purpose: the engine
produces evidence, this module judges it, and judgement must not be able to
reach back into the machinery that made the numbers.

Trades and daily results may be dataclasses or plain dicts; every field access
goes through `_get`, which accepts either.

Two Sec 7 sentences shape everything here:

* "A lack of adequate history is **inconclusive**, not a pass."  Every gate
  therefore reports PASS / FAIL / INCONCLUSIVE, and a numerically favourable
  result on a sample below the Sec 7 floor is reported INCONCLUSIVE - never
  PASS.  See `_verdict`.
* "Do not describe a nominal entry-price exit as a net win."  `classify_exit`
  buckets an exit at the nominal entry price separately from target/stop/time
  exits, and such a trade is never counted as a winner.
"""

from __future__ import annotations

import hashlib
import math
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np

__all__ = [
    "GateResult",
    "PASS",
    "FAIL",
    "INCONCLUSIVE",
    "split_sessions",
    "classify_exit",
    "profit_factor",
    "closed_equity_drawdown",
    "summarize",
    "bootstrap_lower_bound",
    "bootstrap_lower_bounds",
    "evaluate_gates",
]

# ---------------------------------------------------------------------------
# Sec 7 preregistered constants.  These are hurdles declared before results are
# observed; Sec 7: "These are preregistered research hurdles, not guarantees or
# universal statistical standards."
# ---------------------------------------------------------------------------

BOOTSTRAP_SEED = 20260917          # Sec 7: "Use seed 20260917".
BOOTSTRAP_RESAMPLES = 10_000       # Sec 7: "For each of 10,000 resamples".
BOOTSTRAP_ELEMENT_ONE_BASED = 500  # Sec 7: "take element 500 in one-based order".
BOOTSTRAP_BLOCK_LENGTHS = (5, 10)  # Sec 7: "Run separately for L=5 and L=10".

MIN_HOLDOUT_TRADES = 100           # Sec 7: "At least 100 closed holdout trades".
MIN_HOLDOUT_SESSIONS = 125         # Sec 7: "at least 125 eligible holdout sessions".
MIN_HOLDOUT_MONTHS = 6             # Sec 7: "and at least six calendar months".
MIN_HOLDOUT_PROFIT_FACTOR = 1.20   # Sec 7: "holdout net profit factor at least 1.20".
MAX_CLOSED_DRAWDOWN = 1_200.0      # Sec 7: 20 initial gross risk units at one MNQ.
REGISTERED_SPAN_MONTHS = 36        # Sec 7: "at least 36 consecutive calendar months".
FORWARD_SESSIONS = 60              # Sec 7 forward paper observation floor.
FORWARD_TRADES = 50

# Sec 3: the position may be opened from 09:30 and must be flat at 12:00 NY.
# Exposure is reported against that 150-minute per-session window.
SESSION_EXPOSURE_MINUTES = 150

# Publishing 10,000 x ceil(n/L) indices inline is only reasonable for a tiny n;
# otherwise the report carries a sha256 digest plus the first resample's row.
MAX_INLINE_START_INDICES = 10_000

PASS = "PASS"
FAIL = "FAIL"
INCONCLUSIVE = "INCONCLUSIVE"

# Sec 5 L6: "No partial exits, trailing, breakeven amendment, or target
# movement.  Exit on stop, frozen target, or mandatory 12:00 flatten."  The
# classifier stays general (other candidates do amend stops), but LB-OPEN can
# only produce the first three buckets.
_EXIT_ALIASES = {
    "target": "target",
    "limit": "target",
    "stop": "stop",
    "initial_stop": "stop",
    "noon_flat": "noon_flat",
    "flat": "noon_flat",
    "flatten": "noon_flat",
    "time": "noon_flat",
    "time_exit": "noon_flat",
    "breakeven": "entry_price",
    "break_even": "entry_price",
    "be_stop": "entry_price",
    "entry_price": "entry_price",
}

_MISSING = object()


# ---------------------------------------------------------------------------
# field access: dataclasses or dicts, either way
# ---------------------------------------------------------------------------


def _get(obj: Any, *names: str, default: Any = None) -> Any:
    """First present attribute/key among `names`, else `default`."""
    if isinstance(obj, Mapping):
        for n in names:
            if n in obj:
                return obj[n]
        return default
    for n in names:
        v = getattr(obj, n, _MISSING)
        if v is not _MISSING:
            return v
    return default


def _num(v: Any, default: float = 0.0) -> float:
    if v is None:
        return default
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def _as_date(v: Any) -> Optional[date]:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _month_key(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def _m(x: float) -> float:
    """Round money for the published report (P&L lives on half-dollar ticks)."""
    return round(float(x) + 0.0, 2)


def _r(x: Optional[float], nd: int = 4) -> Optional[float]:
    return None if x is None else round(float(x) + 0.0, nd)


def _trade_date(t: Any) -> Optional[date]:
    return _as_date(_get(t, "date", "ny_date", "session_date"))


def _trade_net(t: Any) -> float:
    return _num(_get(t, "net_pnl", "net"))


def _trade_gross(t: Any) -> float:
    return _num(_get(t, "gross_pnl", "gross"))


def _daily_date(d: Any) -> Optional[date]:
    return _as_date(_get(d, "date", "ny_date", "session_date"))


def _daily_net(d: Any) -> float:
    """Net result of one eligible session.

    Sec 7: the bootstrap runs "over all eligible daily results, including
    zero-trade days", so a bare number is accepted as that day's net.
    """
    if isinstance(d, (int, float)) and not isinstance(d, bool):
        return float(d)
    return _num(_get(d, "net", "net_pnl", "net_result", "pnl"))


def _chronological(trades: Sequence[Any]) -> list[Any]:
    """Closed trades in execution order: session date, then exit time.

    The original order is the final tiebreak so that a caller supplying no
    timestamps still gets a stable, reproducible sequence.
    """

    def key(item: tuple[int, Any]) -> tuple[date, float, int]:
        i, t = item
        d = _trade_date(t) or date.min
        ts = _get(t, "exit_ts", "entry_ts")
        secs = ts.timestamp() if isinstance(ts, datetime) else 0.0
        return (d, secs, i)

    return [t for _, t in sorted(enumerate(trades), key=key)]


# ---------------------------------------------------------------------------
# Sec 7 chronological split
# ---------------------------------------------------------------------------


def split_sessions(dates: Iterable[Any]) -> tuple[list[date], list[date], list[date]]:
    """Sec 7: "Split whole eligible sessions chronologically: oldest 50%
    development, next 25% validation, newest 25% final holdout; use floor for
    the first two counts and the remainder for holdout."

    Whole sessions only - a session is never divided between segments.
    """
    ordered = sorted({d for d in (_as_date(x) for x in dates) if d is not None})
    n = len(ordered)
    n_dev = n // 2   # floor(0.50 * n)
    n_val = n // 4   # floor(0.25 * n)
    development = ordered[:n_dev]
    validation = ordered[n_dev:n_dev + n_val]
    holdout = ordered[n_dev + n_val:]  # Sec 7: the remainder, not another floor.
    return development, validation, holdout


# ---------------------------------------------------------------------------
# exit classification and basic statistics
# ---------------------------------------------------------------------------


def classify_exit(trade: Any) -> str:
    """Bucket one closed trade's exit.

    Sec 7: "Do not describe a nominal entry-price exit as a net win."  An exit
    filled at the nominal entry price is therefore its own bucket whatever the
    order that produced it was called; Sec 8.4 lesson 4: "'Break-even' is gross,
    not net."  LB-OPEN has no break-even amendment (Sec 5 L6), so for this
    candidate the buckets reduce to target / stop / noon_flat - the classifier
    stays general for the other candidates in the same ledger.
    """
    entry = _get(trade, "entry", "entry_price")
    exit_price = _get(trade, "exit", "exit_price")
    if entry is not None and exit_price is not None and float(entry) == float(exit_price):
        return "entry_price"
    raw = _get(trade, "exit_reason", "reason", default="")
    raw = str(raw or "").strip().lower()
    if not raw:
        return "unknown"
    return _EXIT_ALIASES.get(raw, raw)


def _outcome(trade: Any) -> str:
    """win / loss / flat / entry_price, on NET money after declared costs."""
    if classify_exit(trade) == "entry_price":
        # Sec 7: never a net win, even in the arithmetic corner where the
        # modelled costs happen to vanish.
        return "entry_price"
    net = _trade_net(trade)
    if net > 0:
        return "win"
    if net < 0:
        return "loss"
    return "flat"


def profit_factor(values: Sequence[float]) -> Optional[float]:
    """Gross wins / gross losses over the supplied P&L series.

    Returns None when there is no losing money in the sample: the ratio is then
    undefined (unbounded), which Sec 7 must not silently read as a large pass.
    """
    wins = sum(v for v in values if v > 0)
    losses = -sum(v for v in values if v < 0)
    if losses <= 0:
        return None
    return wins / losses


def closed_equity_drawdown(
    nets: Sequence[float], labels: Optional[Sequence[Any]] = None
) -> dict:
    """Maximum peak-to-trough drawdown of the CLOSED-equity curve.

    Sec 7 gates "maximum peak-to-trough closed-equity drawdown"; Sec 8.5:
    "distinguish native intratrade drawdown from reconstructed closed-trade
    drawdown".  Marked-to-market drawdown needs intratrade price paths and is
    not derivable here.  The curve starts at zero (i.e. at starting equity), so
    an immediate losing run counts from the initial balance.
    """
    equity = 0.0
    peak = 0.0
    peak_at: Any = None
    worst = 0.0
    worst_peak_at: Any = None
    worst_trough_at: Any = None
    for i, net in enumerate(nets):
        equity += float(net)
        label = labels[i] if labels is not None and i < len(labels) else i
        if equity > peak:
            peak = equity
            peak_at = label
        dd = peak - equity
        if dd > worst:
            worst = dd
            worst_peak_at = peak_at
            worst_trough_at = label
    return {
        "max_drawdown": _m(worst),
        "peak_at": _label(worst_peak_at),
        "trough_at": _label(worst_trough_at),
        "final_closed_equity_change": _m(equity),
        "observations": len(nets),
    }


def _label(v: Any) -> Any:
    if isinstance(v, (datetime, date)):
        return v.isoformat()
    return v


def _streaks(outcomes: Sequence[str]) -> tuple[int, int]:
    """Longest consecutive losing and winning runs (Sec 7: "consecutive losses")."""
    longest_loss = longest_win = run_loss = run_win = 0
    for o in outcomes:
        if o == "loss":
            run_loss += 1
            run_win = 0
        elif o == "win":
            run_win += 1
            run_loss = 0
        else:  # entry_price / flat breaks both runs
            run_loss = run_win = 0
        longest_loss = max(longest_loss, run_loss)
        longest_win = max(longest_win, run_win)
    return longest_loss, longest_win


def _bucket(trades: Sequence[Any]) -> dict:
    """The Sec 6 per-group figures used for by-month and by-direction tables."""
    nets = [_trade_net(t) for t in trades]
    outcomes = [_outcome(t) for t in trades]
    wins = [n for n, o in zip(nets, outcomes) if o == "win"]
    losses = [n for n, o in zip(nets, outcomes) if o == "loss"]
    pf = profit_factor(nets)
    return {
        "trades": len(trades),
        "winners": len(wins),
        "losers": len(losses),
        "entry_price_exits": sum(1 for o in outcomes if o == "entry_price"),
        "gross_pnl": _m(sum(_trade_gross(t) for t in trades)),
        "fees": _m(sum(_num(_get(t, "fees", "commission")) for t in trades)),
        "net_pnl": _m(sum(nets)),
        "profit_factor": _r(pf),
        "average_win": _m(sum(wins) / len(wins)) if wins else None,
        "average_loss": _m(sum(losses) / len(losses)) if losses else None,
    }


# ---------------------------------------------------------------------------
# Sec 6 / Sec 7 reporting
# ---------------------------------------------------------------------------


def summarize(
    trades: Sequence[Any],
    daily_results: Sequence[Any],
    *,
    data_quality_exclusions: Optional[Sequence[Any]] = None,
) -> dict:
    """Sec 7: "Report net expectancy, profit factor, win/loss/entry-price/time-
    exit frequencies, average realized win/loss, gross/net P&L, both drawdowns,
    exposure, number of trades/sessions, no-trade reasons, ambiguous-fill counts,
    and results by month and direction."

    `daily_results` must be every eligible scored session, including zero-trade
    days (Sec 7: "Retain all eligible zero-trade days").
    """
    ordered = _chronological(trades)
    nets = [_trade_net(t) for t in ordered]
    grosses = [_trade_gross(t) for t in ordered]
    fees = [_num(_get(t, "fees", "commission")) for t in ordered]
    outcomes = [_outcome(t) for t in ordered]
    categories = [classify_exit(t) for t in ordered]

    wins = [n for n, o in zip(nets, outcomes) if o == "win"]
    losses = [n for n, o in zip(nets, outcomes) if o == "loss"]
    entry_price = [n for n, o in zip(nets, outcomes) if o == "entry_price"]
    n_trades = len(ordered)

    # ---- sessions -------------------------------------------------------
    daily_dates = [_daily_date(d) for d in daily_results]
    daily_nets = [_daily_net(d) for d in daily_results]
    sessions = len(daily_results)
    traded_dates = {d for d in (_trade_date(t) for t in ordered) if d is not None}
    known_dates = [d for d in daily_dates if d is not None]
    months = sorted({_month_key(d) for d in known_dates})

    # Sec 6 requires every rejection reason, not only the successful trades.
    reasons = Counter()
    for d in daily_results:
        reason = _get(d, "no_trade_reason", "reason")
        if reason:
            reasons[str(reason)] += 1

    # ---- exit frequencies ----------------------------------------------
    cat_counts = Counter(categories)
    cat_net: dict[str, float] = {}
    for c, n in zip(categories, nets):
        cat_net[c] = cat_net.get(c, 0.0) + n
    raw_counts = Counter(
        str(_get(t, "exit_reason", "reason", default="") or "unknown").lower()
        for t in ordered
    )

    # ---- exposure -------------------------------------------------------
    holding = [
        (_get(t, "exit_ts"), _get(t, "entry_ts"))
        for t in ordered
    ]
    timed = [
        (x - e).total_seconds() / 60.0
        for x, e in holding
        if isinstance(x, datetime) and isinstance(e, datetime)
    ]
    if n_trades and len(timed) == n_trades:
        held = sum(timed)
        exposure = {
            "in_position_minutes": _r(held, 2),
            "average_holding_minutes": _r(held / n_trades, 2),
            "session_minutes": sessions * SESSION_EXPOSURE_MINUTES,
            # Sec 3: entries are possible from 09:30 and everything is flat at 12:00.
            "ratio": _r(held / (sessions * SESSION_EXPOSURE_MINUTES), 6)
            if sessions
            else None,
        }
    else:
        exposure = {
            "in_position_minutes": None,
            "average_holding_minutes": None,
            "session_minutes": sessions * SESSION_EXPOSURE_MINUTES,
            "ratio": None,
            "note": "entry/exit timestamps absent for some trades; exposure not computed",
        }

    # ---- drawdown -------------------------------------------------------
    dd_trades = closed_equity_drawdown(nets, [_trade_date(t) for t in ordered])
    day_order = sorted(range(sessions), key=lambda i: (daily_dates[i] or date.min, i))
    dd_daily = closed_equity_drawdown(
        [daily_nets[i] for i in day_order], [daily_dates[i] for i in day_order]
    )

    # ---- by month / by direction ---------------------------------------
    by_month: dict[str, dict] = {}
    for mk in months:
        group = [t for t in ordered if (_trade_date(t) or date.min).strftime("%Y-%m") == mk]
        entry = _bucket(group)
        entry["sessions"] = sum(1 for d in known_dates if _month_key(d) == mk)
        by_month[mk] = entry
    # Trades on a date outside the supplied daily series would silently vanish
    # from the monthly table; surface them instead of dropping them (Sec 6).
    for mk in sorted({(_trade_date(t) or date.min).strftime("%Y-%m") for t in ordered}):
        if mk not in by_month:
            group = [t for t in ordered if (_trade_date(t) or date.min).strftime("%Y-%m") == mk]
            by_month[mk] = _bucket(group) | {"sessions": 0}

    directions = sorted({str(_get(t, "direction", default="unknown")).lower() for t in ordered})
    by_direction = {
        d: _bucket([t for t in ordered if str(_get(t, "direction", default="unknown")).lower() == d])
        for d in directions
    }

    ambiguous = [t for t in ordered if bool(_get(t, "ambiguous", default=False))]
    longest_loss_streak, longest_win_streak = _streaks(outcomes)
    net_total = sum(nets)
    daily_total = sum(daily_nets)

    best_i = max(range(sessions), key=lambda i: daily_nets[i]) if sessions else None
    worst_i = min(range(sessions), key=lambda i: daily_nets[i]) if sessions else None

    return {
        # counts
        "trades": n_trades,
        "sessions": sessions,
        "sessions_with_trades": len(traded_dates),
        "zero_trade_sessions": sessions - len(traded_dates & set(known_dates)),
        "calendar_months": len(months),
        "first_session": known_dates[0].isoformat() if known_dates else None,
        "last_session": max(known_dates).isoformat() if known_dates else None,
        # outcome frequencies (Sec 7: entry-price exits are not net wins)
        "winners": len(wins),
        "losers": len(losses),
        "entry_price_exits": len(entry_price),
        "flat_trades": sum(1 for o in outcomes if o == "flat"),
        "win_rate": _r(len(wins) / n_trades) if n_trades else None,
        "loss_rate": _r(len(losses) / n_trades) if n_trades else None,
        "entry_price_exit_rate": _r(len(entry_price) / n_trades) if n_trades else None,
        # money
        "gross_pnl": _m(sum(grosses)),
        "fees": _m(sum(fees)),
        "net_pnl": _m(net_total),
        "daily_net_total": _m(daily_total),
        # Sec 8.5: "reconcile totals to the report".
        "reconciled": abs(net_total - daily_total) < 0.005,
        "net_expectancy": _m(net_total / n_trades) if n_trades else None,
        "gross_expectancy": _m(sum(grosses) / n_trades) if n_trades else None,
        "mean_daily_net": _r(daily_total / sessions, 6) if sessions else None,
        "profit_factor": _r(profit_factor(nets)),
        "gross_profit_factor": _r(profit_factor(grosses)),
        "average_win": _m(sum(wins) / len(wins)) if wins else None,
        "average_loss": _m(sum(losses) / len(losses)) if losses else None,
        "largest_win": _m(max(nets)) if nets else None,
        "largest_loss": _m(min(nets)) if nets else None,
        "entry_price_exit_net": _m(sum(entry_price)) if entry_price else None,
        # exits
        "exit_reasons": dict(sorted(cat_counts.items())),
        "exit_frequencies": {
            k: _r(v / n_trades) for k, v in sorted(cat_counts.items())
        } if n_trades else {},
        "exit_net_pnl": {k: _m(v) for k, v in sorted(cat_net.items())},
        "exit_reasons_raw": dict(sorted(raw_counts.items())),
        # risk
        "closed_equity_drawdown": dd_trades,
        "closed_equity_drawdown_by_session": dd_daily,
        "marked_to_market_drawdown": None,
        "longest_losing_streak": longest_loss_streak,
        "longest_winning_streak": longest_win_streak,
        "worst_day": {
            "date": _label(daily_dates[worst_i]),
            "net": _m(daily_nets[worst_i]),
        } if worst_i is not None else None,
        "best_day": {
            "date": _label(daily_dates[best_i]),
            "net": _m(daily_nets[best_i]),
        } if best_i is not None else None,
        "positive_days": sum(1 for v in daily_nets if v > 0),
        "negative_days": sum(1 for v in daily_nets if v < 0),
        "flat_days": sum(1 for v in daily_nets if v == 0),
        "exposure": exposure,
        # audit
        "ambiguous_fills": {
            "trades": len(ambiguous),
            "net_pnl": _m(sum(_trade_net(t) for t in ambiguous)),
            "rate": _r(len(ambiguous) / n_trades) if n_trades else None,
        },
        "no_trade_reasons": dict(reasons.most_common()),
        "data_quality_exclusions": len(data_quality_exclusions or []),
        "by_month": by_month,
        "by_direction": by_direction,
        "notes": [
            "Sec 7: a nominal entry-price exit is bucketed separately and is never "
            "counted as a net win.",
            "Sec 8.5: marked-to-market drawdown requires intratrade price paths and "
            "is not derivable from closed trades; it is reported as unavailable.",
            "Sec 3: exposure is measured against the 09:30-12:00 window "
            f"({SESSION_EXPOSURE_MINUTES} minutes per eligible session).",
        ],
    }


# ---------------------------------------------------------------------------
# Sec 7 moving-block percentile bootstrap
# ---------------------------------------------------------------------------


def bootstrap_lower_bound(
    daily_results: Sequence[Any],
    block_length: int,
    *,
    seed: int = BOOTSTRAP_SEED,
    resamples: int = BOOTSTRAP_RESAMPLES,
    element_one_based: int = BOOTSTRAP_ELEMENT_ONE_BASED,
) -> dict:
    """Sec 7's lower one-sided 95% bound for average net daily P&L, verbatim:

    "Use a moving-block percentile bootstrap over all eligible daily results,
    including zero-trade days.  For `n` sessions and block length `L`, form all
    `n-L+1` overlapping contiguous blocks.  For each of 10,000 resamples, sample
    block-start indices uniformly with replacement, concatenate blocks and
    truncate to `n`, then compute that resample's daily mean.  Use seed 20260917
    and record the random-number generator/library version.  Sort the 10,000
    means and take element 500 in one-based order (the empirical fifth-percentile
    lower order statistic, with no interpolation) as the lower bound.  Run
    separately for `L=5` and `L=10`, resetting the seed, and require both bounds
    above zero.  Publish the resample-start indices for exact reproduction."

    Implementation decisions, all recorded in the returned record:

    * The generator is `numpy.random.default_rng(20260917)` (PCG64), constructed
      fresh inside this function, so calling it for L=5 and then L=10 *is* the
      required seed reset.
    * Blocks per resample is `ceil(n / L)` - the fewest whole blocks that can
      cover `n` observations before truncation.
    * Start indices are drawn in one row-major `rng.integers(0, n-L+1,
      size=(resamples, ceil(n/L)))` call; that single call defines the stream.
    * Element 500 one-based is index 499 zero-based of the sorted means.
      `numpy.percentile` is deliberately NOT used: it interpolates, and Sec 7
      demands the order statistic "with no interpolation".
    """
    L = int(block_length)
    if L < 1:
        raise ValueError(f"block length must be positive, got {block_length!r}")
    if resamples < element_one_based:
        raise ValueError(
            f"element {element_one_based} does not exist among {resamples} resamples"
        )

    values = np.asarray([_daily_net(d) for d in daily_results], dtype=np.float64)
    n = int(values.size)

    record: dict[str, Any] = {
        "block_length": L,
        "n_sessions": n,
        "resamples": resamples,
        "seed": seed,
        "element_one_based": element_one_based,
        "interpolation": "none",
        # Sec 7: "record the random-number generator/library version".
        "rng": f"numpy.random.default_rng({seed})",
        "bit_generator": "PCG64",
        "numpy_version": np.__version__,
        "observed_daily_mean": _r(float(values.mean()), 6) if n else None,
    }

    if n == 0 or n < L:
        # Sec 7: "A lack of adequate history is inconclusive, not a pass."
        record |= {
            "lower_bound": None,
            "n_blocks": 0,
            "blocks_per_resample": 0,
            "inconclusive_reason": (
                f"{n} eligible daily results cannot form a block of length {L}"
            ),
        }
        return record

    n_blocks = n - L + 1                 # all overlapping contiguous blocks
    blocks_per_resample = math.ceil(n / L)  # concatenate, then truncate to n

    rng = np.random.default_rng(seed)  # Sec 7: seed reset for each block length.
    starts = rng.integers(0, n_blocks, size=(resamples, blocks_per_resample), dtype=np.int64)
    offsets = np.arange(L, dtype=np.int64)

    means = np.empty(resamples, dtype=np.float64)
    span = blocks_per_resample * L
    chunk = max(1, min(resamples, 2_000_000 // max(1, span)))
    for lo in range(0, resamples, chunk):
        hi = min(lo + chunk, resamples)
        # (rows, blocks, L) -> concatenated (rows, blocks*L) -> truncated to n.
        idx = starts[lo:hi, :, None] + offsets
        flat = idx.reshape(hi - lo, span)[:, :n]
        means[lo:hi] = values[flat].mean(axis=1)

    means.sort()
    lower = float(means[element_one_based - 1])  # one-based element 500.

    digest = hashlib.sha256(np.ascontiguousarray(starts, dtype="<i8").tobytes()).hexdigest()
    record |= {
        "n_blocks": n_blocks,
        "blocks_per_resample": blocks_per_resample,
        "lower_bound": _r(lower, 6),
        "resample_mean_min": _r(float(means[0]), 6),
        "resample_mean_max": _r(float(means[-1]), 6),
        "resample_mean_mean": _r(float(means.mean()), 6),
        # Sec 7: "Publish the resample-start indices for exact reproduction."
        "start_index_base": 0,
        "resample_start_indices_sha256": digest,
        "resample_start_indices_sha256_layout": (
            f"sha256 of the ({resamples} x {blocks_per_resample}) int64 "
            "little-endian row-major start-index array"
        ),
        "first_resample_start_indices": starts[0].tolist(),
        "resample_start_indices": (
            starts.tolist() if starts.size <= MAX_INLINE_START_INDICES else None
        ),
        "reproduce": (
            f"numpy=={np.__version__}; "
            f"np.random.default_rng({seed}).integers(0, {n_blocks}, "
            f"size=({resamples}, {blocks_per_resample}), dtype=np.int64)"
        ),
    }
    return record


def bootstrap_lower_bounds(
    daily_results: Sequence[Any],
    block_lengths: Sequence[int] = BOOTSTRAP_BLOCK_LENGTHS,
    **kwargs: Any,
) -> dict[str, dict]:
    """Run the bootstrap separately per block length, keyed "L5"/"L10".

    Sec 7: "Run separately for L=5 and L=10, resetting the seed".  Each call
    builds its own generator from the seed, so the reset is structural.
    """
    return {f"L{int(L)}": bootstrap_lower_bound(daily_results, int(L), **kwargs) for L in block_lengths}


# ---------------------------------------------------------------------------
# Sec 7 evidence gates
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class GateResult:
    """One preregistered hurdle's verdict.

    Sec 7: "A lack of adequate history is inconclusive, not a pass."  There is
    no two-valued status here on purpose.
    """

    name: str
    status: str
    detail: str
    observed: dict[str, Any] = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == PASS

    def __str__(self) -> str:
        return f"{self.status:<12} {self.detail}"


def _verdict(
    name: str,
    *,
    computable: bool,
    satisfied: bool,
    detail: str,
    observed: dict[str, Any],
    evidence_adequate: bool,
    thin_note: str,
) -> GateResult:
    """PASS only when the hurdle is met AND the sample can carry the claim.

    Sec 7: a favourable number on a thin or exploratory sample is INCONCLUSIVE.
    An unfavourable number is a FAIL whatever the sample size - failing on thin
    evidence is still not a pass.
    """
    if not computable:
        return GateResult(name, INCONCLUSIVE, detail, observed)
    if not satisfied:
        return GateResult(name, FAIL, detail, observed)
    if not evidence_adequate:
        return GateResult(name, INCONCLUSIVE, f"{detail} - {thin_note}", observed)
    return GateResult(name, PASS, detail, observed)


def _normalise_bootstraps(bootstraps: Any) -> dict[str, dict]:
    """Accept {"L5": ...}, {5: ...} or {"5": ...} indifferently."""
    out: dict[str, dict] = {}
    if isinstance(bootstraps, Mapping):
        for k, v in bootstraps.items():
            key = str(k)
            if not key.upper().startswith("L"):
                key = f"L{key}"
            out[key.upper()] = v
    return out


def evaluate_gates(
    trades: Sequence[Any],
    daily_results: Sequence[Any],
    summary: Optional[Mapping[str, Any]] = None,
    bootstraps: Any = None,
    *,
    exploratory: bool = False,
    scenario_name: Optional[str] = None,
    stress_trades: Optional[Sequence[Any]] = None,
    stress_daily_results: Optional[Sequence[Any]] = None,
    holdout_bootstrap: bool = True,
) -> dict[str, GateResult]:
    """Evaluate every Sec 7 "Proposed evidence gate" over one scenario's results.

    `trades`/`daily_results` are the whole registered study for one cost
    scenario; the validation and holdout segments are recut here with
    `split_sessions` so that a caller cannot hand in a favourable subset by
    accident.  `exploratory=True` (Sec 2: "A continuous-chart run may be an
    exploratory screen only, labeled as such") downgrades every otherwise
    passing gate to INCONCLUSIVE.
    """
    daily = list(daily_results)
    all_trades = _chronological(trades)
    dev, val, hold = split_sessions([_daily_date(d) for d in daily])
    val_set, hold_set = set(val), set(hold)

    daily_by_date: dict[date, float] = {}
    for d in daily:
        dd = _daily_date(d)
        if dd is not None:
            daily_by_date[dd] = daily_by_date.get(dd, 0.0) + _daily_net(d)

    val_daily = [daily_by_date.get(d, 0.0) for d in val]
    hold_daily = [daily_by_date.get(d, 0.0) for d in hold]
    val_trades = [t for t in all_trades if _trade_date(t) in val_set]
    hold_trades = [t for t in all_trades if _trade_date(t) in hold_set]
    hold_nets = [_trade_net(t) for t in hold_trades]

    val_net = sum(val_daily)
    hold_net = sum(hold_daily)
    hold_months = sorted({_month_key(d) for d in hold})
    all_months = sorted({_month_key(d) for d in daily_by_date})

    gates: dict[str, GateResult] = {}

    # --- Sec 7: sample-size floor ---------------------------------------
    enough_trades = len(hold_trades) >= MIN_HOLDOUT_TRADES
    enough_sessions = len(hold) >= MIN_HOLDOUT_SESSIONS
    enough_months = len(hold_months) >= MIN_HOLDOUT_MONTHS
    floor_met = enough_trades and enough_sessions and enough_months
    sample_observed = {
        "holdout_trades": len(hold_trades),
        "holdout_sessions": len(hold),
        "holdout_calendar_months": len(hold_months),
        "required": {
            "trades": MIN_HOLDOUT_TRADES,
            "sessions": MIN_HOLDOUT_SESSIONS,
            "months": MIN_HOLDOUT_MONTHS,
        },
        "exploratory": bool(exploratory),
    }
    gates["holdout_sample_floor"] = GateResult(
        "holdout_sample_floor",
        PASS if floor_met and not exploratory else INCONCLUSIVE,
        (
            f"{len(hold_trades)}/{MIN_HOLDOUT_TRADES} closed holdout trades, "
            f"{len(hold)}/{MIN_HOLDOUT_SESSIONS} holdout sessions, "
            f"{len(hold_months)}/{MIN_HOLDOUT_MONTHS} calendar months"
            + ("" if floor_met else " - Sec 7: inadequate history is inconclusive, not a pass")
            + (" - exploratory screen, not acceptance evidence" if exploratory else "")
        ),
        sample_observed,
    )

    # Sec 7: "The floor alone does not establish independent statistical
    # evidence" - but falling short of it forbids any PASS elsewhere.
    adequate = floor_met and not exploratory
    thin = (
        "sample below the Sec 7 floor, or an exploratory screen: "
        "inconclusive, not a pass"
    )

    # --- Sec 7: registered span (informational hurdle) -------------------
    gates["registered_history_span"] = GateResult(
        "registered_history_span",
        PASS if len(all_months) >= REGISTERED_SPAN_MONTHS else INCONCLUSIVE,
        (
            f"{len(all_months)}/{REGISTERED_SPAN_MONTHS} calendar months of eligible "
            "sessions"
            + (
                ""
                if len(all_months) >= REGISTERED_SPAN_MONTHS
                else " - Sec 7: report the actual shorter span; do not claim the "
                "planned validation was performed"
            )
        ),
        {"calendar_months": len(all_months), "required": REGISTERED_SPAN_MONTHS,
         "development_sessions": len(dev), "validation_sessions": len(val),
         "holdout_sessions": len(hold)},
    )

    # --- Sec 7: positive net in validation and holdout separately --------
    gates["validation_net_positive"] = _verdict(
        "validation_net_positive",
        computable=bool(val),
        satisfied=val_net > 0,
        detail=f"validation net ${val_net:,.2f} over {len(val)} sessions, "
               f"{len(val_trades)} trades",
        observed={"net_pnl": _m(val_net), "sessions": len(val), "trades": len(val_trades)},
        evidence_adequate=adequate,
        thin_note=thin,
    )
    gates["holdout_net_positive"] = _verdict(
        "holdout_net_positive",
        computable=bool(hold),
        satisfied=hold_net > 0,
        detail=f"holdout net ${hold_net:,.2f} over {len(hold)} sessions, "
               f"{len(hold_trades)} trades",
        observed={"net_pnl": _m(hold_net), "sessions": len(hold), "trades": len(hold_trades)},
        evidence_adequate=adequate,
        thin_note=thin,
    )

    hold_pf = profit_factor(hold_nets)
    if hold_pf is None:
        # No losing money in the sample: the ratio is unbounded, so the hurdle
        # is met only if there is winning money at all.
        pf_satisfied = sum(hold_nets) > 0
        pf_text = "undefined (no losing holdout trades)"
    else:
        pf_satisfied = hold_pf >= MIN_HOLDOUT_PROFIT_FACTOR
        pf_text = f"{hold_pf:.3f}"
    gates["holdout_profit_factor"] = _verdict(
        "holdout_profit_factor",
        computable=bool(hold_trades),
        satisfied=pf_satisfied,
        detail=f"holdout net profit factor {pf_text} (required >= "
               f"{MIN_HOLDOUT_PROFIT_FACTOR:.2f})",
        observed={"profit_factor": _r(hold_pf), "required": MIN_HOLDOUT_PROFIT_FACTOR,
                  "trades": len(hold_trades)},
        evidence_adequate=adequate,
        thin_note=thin,
    )

    # --- Sec 7: moving-block bootstrap lower bounds ----------------------
    boots = _normalise_bootstraps(bootstraps)
    if not boots:
        # Sec 7 runs the bootstrap "over all eligible daily results", i.e. the
        # full eligible series supplied here, zero-trade days included.
        boots = bootstrap_lower_bounds([_daily_net(d) for d in daily])
    bounds = {k: (boots.get(k) or {}).get("lower_bound") for k in ("L5", "L10")}
    boot_computable = all(v is not None for v in bounds.values())
    boot_ok = boot_computable and all(v > 0 for v in bounds.values())
    boot_observed: dict[str, Any] = {
        "lower_bounds": bounds,
        "seed": BOOTSTRAP_SEED,
        "resamples": BOOTSTRAP_RESAMPLES,
        "element_one_based": BOOTSTRAP_ELEMENT_ONE_BASED,
        "series": "all eligible daily results, including zero-trade days",
        "n_sessions": len(daily),
    }
    if holdout_bootstrap and len(hold_daily) >= max(BOOTSTRAP_BLOCK_LENGTHS):
        # Diagnostic only: Sec 7 words the bootstrap over *all* eligible daily
        # results, so the holdout-only bounds do not decide this gate.
        hb = bootstrap_lower_bounds(hold_daily)
        boot_observed["holdout_only_lower_bounds_diagnostic"] = {
            k: hb[k]["lower_bound"] for k in hb
        }
    gates["bootstrap_lower_bounds"] = _verdict(
        "bootstrap_lower_bounds",
        computable=boot_computable,
        satisfied=boot_ok,
        detail=(
            f"L=5 lower bound {bounds['L5']}, L=10 lower bound {bounds['L10']} "
            "(both required > 0)"
        ),
        observed=boot_observed,
        evidence_adequate=adequate,
        thin_note=thin,
    )

    # --- Sec 7: stress run -----------------------------------------------
    is_stress = str(scenario_name or "").lower() == "stress"
    if is_stress:
        s_trades, s_daily = all_trades, daily
    elif stress_daily_results is not None or stress_trades is not None:
        s_trades, s_daily = list(stress_trades or []), list(stress_daily_results or [])
    else:
        s_trades, s_daily = None, None
    if s_daily is None:
        gates["stress_holdout_net_positive"] = GateResult(
            "stress_holdout_net_positive",
            INCONCLUSIVE,
            "stress-run results were not supplied to this gate; Sec 7 requires a "
            "rerun under stress costs, not a flat deduction",
            {"supplied": False},
        )
    else:
        s_dates = [_daily_date(d) for d in s_daily]
        _, _, s_hold = split_sessions(s_dates)
        s_hold_set = set(s_hold)
        s_by_date: dict[date, float] = {}
        for d in s_daily:
            dd = _daily_date(d)
            if dd is not None:
                s_by_date[dd] = s_by_date.get(dd, 0.0) + _daily_net(d)
        s_net = sum(s_by_date.get(d, 0.0) for d in s_hold)
        s_hold_trades = [t for t in (s_trades or []) if _trade_date(t) in s_hold_set]
        gates["stress_holdout_net_positive"] = _verdict(
            "stress_holdout_net_positive",
            computable=bool(s_hold),
            satisfied=s_net > 0,
            detail=f"stress holdout net ${s_net:,.2f} over {len(s_hold)} sessions, "
                   f"{len(s_hold_trades)} trades",
            observed={"net_pnl": _m(s_net), "sessions": len(s_hold),
                      "trades": len(s_hold_trades), "self_scenario": is_stress},
            evidence_adequate=adequate,
            thin_note=thin,
        )

    # --- Sec 7: closed-equity drawdown ------------------------------------
    dd_all = closed_equity_drawdown(
        [_trade_net(t) for t in all_trades], [_trade_date(t) for t in all_trades]
    )
    dd_hold = closed_equity_drawdown(hold_nets, [_trade_date(t) for t in hold_trades])
    gates["max_closed_drawdown"] = _verdict(
        "max_closed_drawdown",
        computable=bool(all_trades),
        satisfied=dd_all["max_drawdown"] <= MAX_CLOSED_DRAWDOWN,
        detail=(
            f"max peak-to-trough closed-equity drawdown ${dd_all['max_drawdown']:,.2f} "
            f"(limit ${MAX_CLOSED_DRAWDOWN:,.2f} = 20 gross risk units at one MNQ)"
        ),
        observed={
            "max_drawdown": dd_all["max_drawdown"],
            "limit": MAX_CLOSED_DRAWDOWN,
            "peak_at": dd_all["peak_at"],
            "trough_at": dd_all["trough_at"],
            "holdout_max_drawdown": dd_hold["max_drawdown"],
            # Sec 7 also asks for marked-to-market drawdown; closed trades alone
            # cannot supply it (Sec 8.5 distinguishes the two).
            "marked_to_market_drawdown": None,
        },
        evidence_adequate=adequate,
        thin_note=thin,
    )

    # --- Sec 7: prospective forward paper observation ---------------------
    gates["forward_paper_observation"] = GateResult(
        "forward_paper_observation",
        INCONCLUSIVE,
        (
            f"requires >= {FORWARD_SESSIONS} eligible sessions and >= {FORWARD_TRADES} "
            "closed trades observed prospectively; Sec 7: \"replay is not prospective "
            "paper trading\""
        ),
        {"required_sessions": FORWARD_SESSIONS, "required_trades": FORWARD_TRADES,
         "observed": None},
    )

    # --- overall ----------------------------------------------------------
    statuses = [g.status for g in gates.values()]
    if FAIL in statuses:
        overall, note = FAIL, "at least one preregistered gate failed"
    elif INCONCLUSIVE in statuses:
        overall, note = INCONCLUSIVE, "no gate failed, but the evidence is not adequate"
    else:
        overall, note = PASS, "every preregistered gate passed"
    gates["overall"] = GateResult(
        "overall",
        overall,
        # Sec 7: "If the primary candidate fails the evidence gates, the
        # conclusion is rejection or insufficient evidence - not automatic
        # parameter optimization until a winning chart appears."
        f"{note}: "
        f"{statuses.count(PASS)} pass / {statuses.count(FAIL)} fail / "
        f"{statuses.count(INCONCLUSIVE)} inconclusive",
        {
            "pass": statuses.count(PASS),
            "fail": statuses.count(FAIL),
            "inconclusive": statuses.count(INCONCLUSIVE),
            "exploratory": bool(exploratory),
            "scenario": scenario_name,
        },
    )
    return gates
