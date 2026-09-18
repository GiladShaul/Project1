# LB-OPEN handoff package

Everything needed to implement and run LB-OPEN on TradingView, plus the means to
verify the implementation is correct before anyone trusts a number from it.

## Contents

| File | What it is |
|---|---|
| `LB_OPEN_ALGORITHM.md` | **The specification.** Self-contained and authoritative. Read this first. |
| `acceptance_vectors.json` | Expected values from the reference implementation |
| `acceptance_bars.csv` | 6,211 one-minute bars the vectors were computed from |
| `pine/LB_OPEN.pine` | Pine v6 strategy — **never compiled, see below** |

The working reference implementation lives in `../lb_open/` (Python, 41 passing
tests). It is the tie-breaker for any disagreement about behaviour.

## Brief for whoever runs this

**1. The Pine file has never been compiled.** It was written without access to a
Pine compiler or to TradingView. Expect syntax errors. Its value is the
architecture — particularly the `max_bars_back` workaround, which is the single
biggest time sink in porting this — not the syntax. If it fights you, discard it
and implement from `LB_OPEN_ALGORITHM.md`, which is the real deliverable.

**2. Verify before trusting.** Run `acceptance_bars.csv` through the
implementation for session **2026-09-15** and assert `acceptance_vectors.json`.
Two values catch the classic mistakes:

- `P0` must be **19997.00**, not 20000.00. Getting 20000 means reading the bar
  starting 09:29, which does not close until 09:30 — a look-ahead leak that will
  silently flatter every result.
- The pre-market ramp contains **equal adjacent lows**, which must produce **no
  swing**. Detecting one there means the swing test is not strict.

A full correct run yields: entry 19930.00, stop 19900.00, target 20020.00, in at
09:32, out on target at 10:25, **+$177.50 baseline / +$175.00 stress**.

That fixture is **synthetic** — hand-built to exercise every rule. It proves the
implementation is right. It says nothing about profitability.

**3. Data requirements are strict and non-negotiable.**
- Dated, unadjusted contract (`MNQZ2025`, not `MNQ1!` and not continuous).
- **One contract per run** — never span a roll. L1 draws price levels from 13
  weeks back and a quarterly contract lives about 13 weeks, so a spanning run
  splices levels across expiries and produces nonsense.
- 1-minute bars, and **91 days of warm-up before the first scoreable session**,
  because L1 needs 13 same-weekday regular-session daily candles.
- Deep Backtesting must be enabled explicitly.

**4. TradingView's broker emulator is optimistic here.** It does not implement
the adverse-ordering rule of §9.4 — where a bar could hit both stop and target,
it will not reliably take the stop. Bar Magnifier helps but guarantees nothing
about intrabar sequence. **Treat any native TradingView result as a preliminary
screen, not as evidence.** The Python reference implements the conservative
ordering and can re-score the same trades if you export them.

## Expect very few trades — this is the important part

Before anyone budgets time: the gate stack is extremely selective, and that is a
property of the rules rather than of any particular dataset.

| Gate | Admits |
|---|---|
| L1 bias — exactly one bracket touched | **~31% of sessions** |
| L2 London agreement | ~34% of those |
| L6 reward/risk — ≥64.25 pts to the *nearest* target | ~23% of candidate evaluations |

Measured over 570 sessions with a full 13-candle lookback: the `B_low…B_high`
bracket around `P0` is a **median 80.6 points** wide, while the `[00:00,09:29)`
touch window sweeps a **median 222 points**. So both levels are usually touched,
which L1 resolves as *no trade*.

Compounded, expect a trade on roughly **2–3% of sessions**.

The registered evidence standard asks for **≥100 closed trades in a 25% holdout**.
A 36-month study is ~750 sessions → ~186 holdout sessions → that needs a **~54%**
trade rate. At 2–3% you would get **4–6 trades**.

**The experiment as specified is short of its own evidence floor by roughly 20×,
and supplying more lookback history makes L1 *stricter*, not looser** (13 candles
give up to 52 candidate prices, packing the brackets tighter around `P0`).

So a faithful implementation run over three years will most likely return a
handful of trades and no conclusion. That is not an implementation failure — it
is what these rules do. If the goal is a verdict rather than an implementation,
the L1 double-touch resolution needs deciding first, since that single rule
removes ~62% of all days.

## What has and has not been established

**Established:** the algorithm is fully specified and has a verified
implementation; the §6 acceptance cases pass; the moving-block bootstrap matches
an independent derivation bit-for-bit; the engine runs end-to-end on real market
data.

**Not established:** anything at all about profitability. The only P&L figures
anywhere in this work come from the synthetic fixture above. On real data the
engine has taken **zero trades**, in every run, for want of the 91-day warm-up
that free data cannot supply.

Full detail: `../docs/FINDINGS.md`.
