"""Study runner: registered experiment, splits, gates and the audit record.

Sec 7: "Register Q90-PSP as primary and LB-OPEN as secondary before observing
strategy outcomes."  LB-OPEN is the secondary candidate.  Sec 8.1 records it as
having "No completed implementation/performance study - Untested; neither
approved nor rejected by the Q90 results."

Sec 7: "If the primary candidate fails the evidence gates, the conclusion is
rejection or insufficient evidence - not automatic parameter optimization until a
winning chart appears."  The same standard binds this candidate.
"""

from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Iterable, Optional

from . import calendar as cal
from .core import BASELINE, STRESS, CostScenario
from .data import MinuteStore
from .engine import RunInvalid, SessionResult, Trade, run_study


def _json_default(o):
    if isinstance(o, (datetime, date)):
        return o.isoformat()
    if is_dataclass(o):
        return asdict(o)
    return str(o)


def audit_rows(results: list[SessionResult], cost: CostScenario,
               source: str) -> list[dict]:
    """Sec 6: "Record every eligible session and every candidate rejection, not
    only successful trades."  Sec 6 lists the required fields; Sec 6 adds "For LB
    also save all candidates and ranking; hiding drawings must not erase rejected
    alternatives."
    """
    rows: list[dict] = []
    for r in results:
        base = {
            "strategy": "LB-OPEN",
            "version": "v1.0",
            "scenario": cost.name,
            "source": source,
            "ny_date": r.date.isoformat(),
            "excluded": r.excluded,
            "coverage_ratio": round(r.coverage_ratio, 6),
            "no_trade_reason": r.no_trade_reason,
            "candidates": r.candidates_logged,
        }
        if r.context is not None:
            base |= {
                "p0": r.context.p0,
                "b_low": r.context.b_low,
                "b_high": r.context.b_high,
                "bias": r.context.bias.name.lower(),
                "london": r.context.london,
                "overnight_high": r.context.rh,
                "overnight_low": r.context.rl,
                "lookback_candles_used": r.context.candidates_used,
            }
        if not r.trades:
            rows.append(base | {"trade": None})
            continue
        for t in r.trades:
            rows.append(base | {"trade": {
                "window": t.window, "direction": t.direction,
                "entry": t.entry, "exit": t.exit,
                "entry_ts": t.entry_ts.isoformat(), "exit_ts": t.exit_ts.isoformat(),
                "exit_reason": t.exit_reason, "offset": t.offset,
                "stop": t.stop, "target": t.target, "swing": t.swing_level,
                "reward_risk": round(t.reward_risk, 4),
                "gross_pnl": t.gross_pnl, "fees": t.fees, "net_pnl": t.net_pnl,
                "ambiguous": t.ambiguous, "quantity": 1,
            }})
    return rows


def run_experiment(
    store: MinuteStore,
    start: date,
    end: date,
    scenarios: Iterable[CostScenario] = (BASELINE, STRESS),
    source: str = "unspecified",
    exploratory: bool = True,
) -> dict:
    """Run the registered experiment over every eligible session in the range."""
    from .report import split_sessions, summarize, bootstrap_lower_bound, evaluate_gates

    sessions = cal.eligible_sessions(start, end)

    # Sec 2: "Use dated, unadjusted contracts. ... Do not splice price levels
    # across contract expiries or use retrospectively back-adjusted continuous
    # prices for absolute historical levels."  LB-OPEN's L1 brackets ARE
    # absolute historical levels drawn from up to 13 weeks back, and a quarterly
    # contract lives about 13 weeks, so a range that crosses a research switch
    # cannot be one contract's own history.  Sec 2: "Reset quarter and order
    # state on a contract switch."
    windows = cal.contract_windows(start, end)
    spans_roll = len(windows) > 1

    out: dict = {
        "strategy": "LB-OPEN",
        "rulebook": "Strategy_Rulebook_v1.md",
        "source": source,
        "exploratory": exploratory,
        "range": [start.isoformat(), end.isoformat()],
        "eligible_sessions": len(sessions),
        "contract_windows": [
            {"contract": c, "from": a.isoformat(), "to": b.isoformat()} for c, a, b in windows
        ],
        "spans_contract_roll": spans_roll,
        "scenarios": {},
    }
    if spans_roll:
        out["contract_warning"] = (
            f"Range crosses {len(windows) - 1} Sec 2 research switch(es): "
            + " -> ".join(c for c, _a, _b in windows)
            + ". A single CSV covering this range cannot be one dated contract's own "
            "history, so L1's absolute bracket levels are spliced across expiries, "
            "which Sec 2 forbids. Run one contract per invocation."
        )
    if exploratory or spans_roll:
        out["exploratory"] = True
        out["evidentiary_status"] = (
            "EXPLORATORY SCREEN - not acceptance evidence under Sec 7. "
            "Sec 7: 'A lack of adequate history is inconclusive, not a pass.'"
            + (" Range spans a contract roll (Sec 2)." if spans_roll else "")
        )
        exploratory = True

    for cost in scenarios:
        try:
            results = run_study(store, sessions, cost)
        except RunInvalid as e:
            out["scenarios"][cost.name] = {"run_invalid": str(e)}
            continue

        scored = [r for r in results if not r.excluded]
        excluded = [
            {"date": r.date.isoformat(), "coverage": round(r.coverage_ratio, 4),
             "reason": r.no_trade_reason}
            for r in results if r.excluded
        ]
        trades = [t for r in scored for t in r.trades]
        daily = [{"date": r.date, "net": r.net_pnl} for r in scored]

        dev, val, hold = split_sessions([r.date for r in scored])
        summary = summarize(trades, daily)
        boot = {
            f"L{L}": bootstrap_lower_bound([d["net"] for d in daily], L)
            for L in (5, 10)
        }
        out["scenarios"][cost.name] = {
            "planned_loss": cost.planned_loss,
            "min_target_points": cost.min_target_points(),
            "scored_sessions": len(scored),
            "data_quality_exclusions": excluded,
            "summary": summary,
            "bootstrap": boot,
            "split": {"development": len(dev), "validation": len(val), "holdout": len(hold)},
            # Sec 7's stress gate judges the declared stress rerun; naming the
            # active scenario lets it read that run's own holdout instead of
            # reporting "not supplied" for a rerun that did happen.
            "gates": evaluate_gates(trades, daily, summary, boot,
                                    exploratory=exploratory,
                                    scenario_name=cost.name),
            "audit": audit_rows(results, cost, source),
        }
    return out


def write_report(report: dict, out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "lb_open_report.json"
    path.write_text(json.dumps(report, indent=2, default=_json_default))
    return path
