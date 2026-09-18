# LB-OPEN run, M2026 window, 2026-09-18

First run in which sessions were actually **scored** rather than excluded.
**It produced zero trades.** That is a result about data starvation, not about
the strategy — see the breakdown below.

| Field | Value |
|---|---|
| Source | Yahoo Finance `NQ=F`, continuous front-month |
| Contract identity | **continuous, not dated** — disqualifying under §2 |
| Contract window | M2026 only (2026-08-21 → 2026-09-12), no roll spanned |
| Range | 2026-08-21 → 2026-09-12 |
| Eligible sessions | 15 |
| Scored sessions | **11** |
| Data-quality exclusions | 4 |
| **Trades** | **0** |
| LB candidates ever evaluated | **0** |
| Pre-snapshot context coverage floor | **0.98 (declared departure)** |
| Scenarios | baseline and stress |

## Why zero trades

| Cause | Sessions | Whose fault |
|---|---:|---|
| No eligible historical daily candle at all (0 of 13) | 4 | data ceiling |
| B_low or B_high absent — too few lookback candles to bracket P0 | 6 | data ceiling |
| **Both brackets touched in `[00:00,09:29)` → §L1 no trade** | **1** | **the rule** |

**Ten of the eleven are data starvation.** The vendor caps 1-minute history at
~30 days while §5 L1 needs 91, so only 0–3 of the 13 required lookback candles
ever existed, and with so few candidate prices P0 frequently falls outside them
entirely. Only one session (2026-09-11) reached a genuine rule decision, and it
was the L1 double-touch — the filter §3 of `docs/FINDINGS.md` identifies as the
dominant one.

No LB candidate was ever constructed, so L3/L4/L5/L6 were never exercised on
real data at all. This run says nothing whatsoever about LB-OPEN's edge.

## Declared departure

The pre-snapshot context windows were accepted at ≥98% minute coverage rather
than 100%. §2 names the intervals that must be minute-complete — the LB
historical intervals, the execution window, and snapshot-forward — and these are
not among them; those remain strict. Under the 100% reading every session is
excluded by a recurring ~10-minute vendor gap at midnight, which is a vendor
artifact rather than a market halt. §1 requires the change be recorded, which is
what this section is.

## Reproduce

```bash
python3 data/fetch.py --weeks 4 --out data/nq_minutes.csv
python3 -m lb_open.cli --csv data/nq_minutes.csv \
    --start 2026-08-21 --end 2026-09-12 --context-coverage 0.98 \
    --source "Yahoo NQ=F continuous, M2026 window"
```

The vendor serves a rolling ~30-day window, so a later fetch covers later dates.
The JSON beside this file is the record.
