# What LB-OPEN needs before it can be tested

This document exists because the data requirement, not the code, is what
currently blocks a verdict on LB-OPEN.

## The warmup requirement is the hard part

§5 L1 selects its bracket levels from *"completed full regular-session MNQ daily
candles in the preceding 13 calendar weeks with the same weekday as today"*.
Thirteen weeks is **91 calendar days**. So the first session that can produce a
signal needs 91 days of prior history already in the store, and §7 warns that
*"Warmup may use older observations but contributes no scored trades."*

Per scored session the engine reads:

| Interval | Purpose | Minutes |
|---|---|---|
| 13 × `[09:30,16:00)` on the same weekday, −7…−91 days | §5 L1 bracket candidates | 13 × 390 |
| previous day `[18:00,00:00)` | §5 L2 Asia high/low | 360 |
| today `[00:00,06:00)` | §5 L2 London high/low/close | 360 |
| previous day `18:00` → `09:29` | §5 L4 overnight range proxy | 929 |
| today `[00:00,09:29)` | §5 L1 touch test | 569 |
| the five LB offsets, 30 min each, per window | §5 L3 candidates | ≤ 300 |
| `09:29` → `12:00` inclusive | execution coverage (§2 inventory) | 152 |

## Minimum viable exports

| Goal | Span of 1-minute data | Roughly |
|---|---|---|
| Engine smoke test, no evidence value | 4 months | ~85 sessions, 1 contract |
| §7 holdout floor alone (125 sessions, 100 trades, 6 months) | 9 months | 91-day warmup + 6 months |
| §7 registered study as written (36 months, 50/25/25 split) | 39 months | ~13 dated contracts |

## Contract identity

§2 is strict and it matters more for LB-OPEN than it did for Q90-PSP:

> Use dated, unadjusted contracts. … Do not splice price levels across contract
> expiries or use retrospectively back-adjusted continuous prices for absolute
> historical levels.

LB-OPEN's B_low/B_high **are** absolute historical levels, drawn from up to 13
weeks back. A quarterly contract lives about 13 weeks. So the L1 lookback window
and the contract lifetime are nearly the same length, and any continuous or
back-adjusted series injects a splice discontinuity of tens to hundreds of points
directly into the levels the strategy trades against. A continuous-series run is
an exploratory screen and nothing more (§2).

§2 also requires that *"Historical features must come from the newly selected
contract's own available prior history. … Missing warmup means no signal."*
A freshly-rolled contract has thin early history, so the first weeks after each
roll will legitimately produce no trades.

## Export format

Any CSV with a timestamp column and open/high/low/close is accepted;
`lb_open.data.load_csv` sniffs the header. TradingView's chart export emits
`time,open,high,low,close,volume` — feed it directly:

```bash
python3 -m lb_open.cli --csv exports/MNQZ2025_1m.csv --tz America/New_York \
    --dated --source "CME_MINI:MNQZ2025, TradingView chart export 2026-09-18"
```

Pass `--dated` only when it really is a dated contract; without it the report is
stamped as an exploratory screen.

## A completeness problem you will hit

§2 says *"Never forward-fill a missing asset"* and *"missing mandatory context
vetoes the setup"*. Applied literally, a **single** absent minute anywhere in the
929-minute overnight window vetoes that session.

Real feeds have absent minutes. In the free `NQ=F` screen fetched for this
repository, every session was missing a contiguous ten-minute block around
midnight — a vendor artifact, not a market halt — and that alone vetoed the L4
range on 4 of 19 sessions. TradingView exports likewise omit minutes in which
nothing traded.

The engine defaults to the strict reading and reports every veto as a
data-quality exclusion with its coverage ratio, exactly as §2 requires. That is
the correct default. But be aware before you export: if your data has routine
minute gaps, you will get a high exclusion rate, and the right response is to
decide deliberately — either source gap-free data, or register a v1.1 that
states a coverage tolerance and its justification. §1 binds that choice:

> Change any operative rule only by creating a new version and recording the
> change before testing new unseen data.

Do not quietly loosen it inside a v1 run.
