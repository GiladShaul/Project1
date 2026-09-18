"""Command-line entry point for the LB-OPEN study.

    python3 -m lb_open.cli --csv data/nq_minutes.csv \
        --start 2026-08-21 --end 2026-09-17 --source "Yahoo NQ=F continuous"

Sec 7: results from a continuous series are an exploratory screen.  Pass
`--dated` only when the CSV really is a dated, unadjusted contract export, and
record the exchange-qualified symbol in `--source`.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lb_open.core import BASELINE, STRESS
from lb_open.data import load_csv
from lb_open.study import run_experiment, write_report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run the LB-OPEN registered experiment")
    ap.add_argument("--csv", required=True, help="one-minute MNQ OHLC CSV")
    ap.add_argument("--tz", default="UTC", help="timezone for naive timestamps in the CSV")
    ap.add_argument("--start", required=True, type=date.fromisoformat)
    ap.add_argument("--end", required=True, type=date.fromisoformat)
    ap.add_argument("--source", default="unspecified",
                    help="exchange-qualified symbol / provenance (Sec 8.5)")
    ap.add_argument("--dated", action="store_true",
                    help="the CSV is a dated unadjusted contract, not a continuous series")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--scenario", choices=["baseline", "stress", "both"], default="both")
    a = ap.parse_args(argv)

    store = load_csv(a.csv, tz=a.tz)
    if not len(store):
        print(f"no bars parsed from {a.csv}", file=sys.stderr)
        return 2

    scenarios = {"baseline": (BASELINE,), "stress": (STRESS,), "both": (BASELINE, STRESS)}[a.scenario]
    report = run_experiment(
        store, a.start, a.end, scenarios=scenarios,
        source=a.source, exploratory=not a.dated,
    )
    path = write_report(report, a.out)

    print(f"bars {len(store)}  {store.first_ts}  ->  {store.last_ts}")
    print(f"eligible sessions: {report['eligible_sessions']}")
    for name, sc in report["scenarios"].items():
        if "run_invalid" in sc:
            print(f"\n[{name}] RUN INVALID: {sc['run_invalid']}")
            continue
        s = sc["summary"]
        print(f"\n[{name}] scored sessions {sc['scored_sessions']}, "
              f"excluded {len(sc['data_quality_exclusions'])}")
        print(f"  trades {s['trades']}  winners {s['winners']}  "
              f"net ${s['net_pnl']:,.2f}  profit factor {s['profit_factor']}")
        print(f"  exits: {s['exit_reasons']}")
        for gate, verdict in sc["gates"].items():
            print(f"  gate {gate:<28} {verdict}")
    if report.get("exploratory"):
        print(f"\n!! {report['evidentiary_status']}")
    print(f"\nreport written to {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
