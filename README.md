# LB-OPEN — independent implementation and test harness

An executable implementation of **LB-OPEN**, the secondary candidate specified in
section 5 of [`docs/Strategy_Rulebook_v1.md`](docs/Strategy_Rulebook_v1.md).

## Why this candidate

The rulebook's own evidence ledger (§8.1) records LB-OPEN as the one component of
the research programme that has never been tested:

> **LB-OPEN, PC/FVG filters and microquarter alternatives** — No completed
> implementation/performance study — *Untested; neither approved nor rejected by
> the Q90 results.*

The primary candidate, Q90-PSP, **has** been tested and **failed**:

| Study | Sessions | Trades | Winners | Net | Profit factor |
|---|---:|---:|---:|---:|---:|
| Futures baseline, 2023-09→2026-08 | 744 | 66 | 9 (13.6%) | −$1,792.00 | 0.365 |
| Futures stress, same dates | 744 | 66 | 9 | −$2,103.50 | 0.321 |
| Stock adaptation, 2024-09→2026-08 | — | 420 | 106 | −$1,034.40 | 0.610 |

Both baselines were **negative before commissions** at their modeled fill prices
(§8.4 lesson 6) — futures fill-price P&L −$1,627 against only $165 of commissions.
That is not a cost problem, and this repository does not attempt to rehabilitate
Q90-PSP. §8.6 stands: *"retain these results as rejected research versions and do
not deploy them live."*

LB-OPEN is implemented here because it is genuinely untested, not because Q90's
failure recommends it. §8.6 is explicit: *"No automatic selection of LB-OPEN is
justified by Q90's failure."*

## What this repository does and does not establish

**Does:** provide a causal, auditable implementation whose behaviour on the §6
acceptance cases is verified by an executable test suite.

**Does not:** establish an edge, a profit, or readiness of any kind. No live
trading, broker connection, or order-alert integration exists here, and none
should be built from it without the evidence §7 demands.

## Data — the binding constraint

The engine needs synchronized one-minute MNQ OHLC on **dated, unadjusted**
contracts (§2). Two paths exist, with very different status:

| Path | Coverage | Status under §7 |
|---|---|---|
| `data/fetch.py` (Yahoo `NQ=F`) | ~30 days, continuous front-month | **Exploratory screen only.** §2 forbids continuous prices for absolute historical levels — which is exactly what LB-OPEN's L1 brackets are. |
| `load_tradingview_export` | whatever you export | The only path that can produce §7 acceptance evidence. |

TradingView has no API for strategy backtests or history export; §8.5 records
that the chart-data export was performed manually and worked. That remains the
supported route: export dated MNQ minute bars yourself and point the CLI at the
CSV.

The free path is capped by the vendor at roughly 30 calendar days of one-minute
bars (chained weekly requests older than that return HTTP 422). Against §7's
floor of **125 eligible holdout sessions, 100 closed trades and six calendar
months**, that ceiling makes the free path structurally incapable of producing
acceptance evidence — it exercises the code, nothing more.

## Usage

```bash
# Exploratory screen on free continuous data (labelled as such in the report)
python3 data/fetch.py --weeks 4 --out data/nq_minutes.csv
python3 -m lb_open.cli --csv data/nq_minutes.csv \
    --start 2026-08-21 --end 2026-09-17 --source "Yahoo NQ=F continuous"

# Real run on a dated contract you exported from TradingView
python3 -m lb_open.cli --csv exports/MNQZ2025_1m.csv --tz America/New_York \
    --start 2025-09-15 --end 2025-12-01 --dated \
    --source "CME_MINI:MNQZ2025, TradingView chart export"

pytest tests/ -q
```

## Module map

| File | Rulebook section |
|---|---|
| `lb_open/core.py` | §2 clock and tick grid; §3 cost scenarios |
| `lb_open/calendar.py` | §2 session eligibility; §8.5 calendar exclusions |
| `lb_open/data.py` | §2 availability, completeness, no forward-fill |
| `lb_open/swings.py` | §5 L5 swing structure and consumption |
| `lb_open/context.py` | §5 L1 daily bias, L2 London filter, L4 range proxy |
| `lb_open/lookback.py` | §5 L3 historical LB candles, zones, ranking |
| `lb_open/targets.py` | §5 L6 structural target and reward/risk gate |
| `lb_open/execution.py` | §3 bar-based execution and adverse ordering |
| `lb_open/engine.py` | §5 L7 window and order state machine |
| `lb_open/report.py` | §7 evidence gates and moving-block bootstrap |
| `lb_open/study.py` | §6 audit record; registered experiment runner |

## Design commitments

Three rules shape every module, because §8.4 identifies them as where the earlier
work went wrong:

1. **Causality.** Every accessor takes an explicit `as_of`. A bar starting at
   09:04 becomes visible at 09:05, never before. A three-candle swing at `j` is
   unavailable until `j+1` closes. §8.4 lesson 3: *"Moving drawings and a
   quarter's eventual extreme are not an historical signal ledger."*
2. **Absent data is absent.** A missing minute is never a flat candle, never
   forward-filled, and never a silently substituted nearby date. Missing
   *optional* candidates drop; missing *mandatory* context vetoes the setup;
   a minute missing while an order or position is live invalidates the run.
3. **Adverse ordering where sequence is unknown.** If a stop and a target are
   both reachable inside one minute, the stop is taken and the trade is flagged
   ambiguous. A target touched in the entry minute is never credited as profit.

## Reproducibility

The §7 bootstrap is implemented to the letter: moving blocks of length 5 and 10,
10,000 resamples each, seed 20260917 **reset between block lengths**, and the
lower bound read as **element 500 in one-based order with no interpolation** —
not `np.percentile`, which interpolates. The generator name and NumPy version are
recorded in the report alongside the resample indices.
