# LB-OPEN: implementation findings

Status of this document: these are **findings about the specification and about
data availability**, produced while implementing §5. They are not a performance
result. No LB-OPEN performance result exists, and none is claimed.

---

## 1. The implementation is complete and behaves as specified

All of §5 L1–L7 is implemented, together with the §2 clock/data rules and the §3
execution convention. Correctness is demonstrated two ways:

**Acceptance cases (§6).** The cases from the §6 table that bind LB-OPEN are
executable tests. 26 tests pass, covering DST handling of the 09:30 anchor, the
stress-scenario constants, limit trade-through, unordered stop/target bars, the
entry-and-target-same-minute rule, both L5 expiry paths, and the §2 data guards.

**End-to-end fixture.** A synthetic session is constructed whose geometry gives
every gate a hand-derivable answer. The engine reproduces the hand-derivation
exactly in both cost scenarios:

| Quantity | Hand-derived | Engine |
|---|---|---|
| Winning LB candidate | 3h (nearest by \|E−P\|) | 3h |
| Entry E | 19930.00 | 19930.00 |
| Nearest unconsumed swing low below E | 19900.00 | 19900.00 |
| Nearest unconsumed swing high above E | 20020.00 | 20020.00 |
| Reward/risk, baseline | (90×2−2.50)/63 = 2.817 | 2.817 |
| Reward/risk, stress | (90×2−5.00)/67 = 2.612 | 2.612 |
| Net, baseline / stress | $177.50 / $175.00 | $177.50 / $175.00 |

**§7 bootstrap.** The moving-block bootstrap was re-implemented independently
from the rulebook text and compared against the module: the lower bounds agree
bit-for-bit for L=5 and L=10, and the resample start indices are identical.

Two places where the first draft of a test disagreed with the implementation,
and the implementation was right both times, are worth recording because they
are exactly the §8.4 lesson-3 failure mode:

- **P0 is the close of the minute *ending* 09:29**, i.e. the bar starting 09:28.
  The bar starting 09:29 closes at 09:30 and is not available at the snapshot.
- **A monotonic price ramp produces equal adjacent lows**, and §5 L5's *"Equal
  highs/lows do not form a swing"* then correctly yields no swing at all.

---

## 2. The registered experiment cannot be run on freely available data

Two independent blockers, either one sufficient.

**Warmup.** §5 L1 needs 13 same-weekday sessions going back 91 calendar days.
The free vendor path caps 1-minute bars at ~30 days (chained requests older than
that return HTTP 422). Across 17 screened sessions the engine found **0–3 of the
13 required lookback candles**, and 13 of 17 sessions failed for want of a
bracket level alone.

**Completeness.** Applied literally, §2 vetoes a session for a single absent
minute in mandatory context. Every one of the 19 screened sessions was missing a
contiguous ~10-minute block around midnight — a vendor artifact, not a halt — so
**19 of 19 sessions were excluded**, with execution coverage of 1.000 in every
case. The veto came from the 929-minute pre-snapshot context window, not from the
execution window.

The screen therefore produced **zero scored sessions and zero trades**. Every §7
gate reports INCONCLUSIVE, which is the correct reading of *"A lack of adequate
history is inconclusive, not a pass."*

---

## 3. The gate stack is far more selective than §7's evidence floor allows

This is the substantive finding, and it does not depend on the data problem
above. It is a property of the rules as written.

### Measured components

**§5 L1 bias gate.** Over 570 sessions with a *full* 13-candle lookback (5 years
of daily bars), the deduplicated candidate set is a median of 52 prices, and the
B_low…B_high bracket containing P0 is a median of **80.6 points** wide
(P0→B_low 33.4, P0→B_high 33.8). The touch window `[00:00,09:29)` sweeps a median
of **222 points** on the same instrument. The window is therefore ~2.7× wider
than the bracket it must land inside exactly once.

A Monte Carlo over the measured bracket distances, with the excursion width drawn
from the observed range distribution and split around P0:

| §5 L1 outcome | Estimated frequency |
|---|---:|
| both levels touched → no trade | ~62% |
| neither touched → no trade | ~7% |
| exactly one touched → bias set | **~31%** |

Note the direction of this effect: **more lookback history makes L1 stricter, not
looser.** Thirteen candles give up to 52 candidate prices, which packs the
brackets tighter around P0 and makes a double touch more likely. Supplying the
full history the rule asks for reduces the trade count.

**§5 L2 London filter.** Measured on 19 sessions: bullish 32%, bearish 37%,
neither 21%, insufficient data 11%. Roughly 68% of sessions are decisive, and
agreement with an already-fixed daily bias is about half of those, so **~34%** of
L1 survivors clear L2.

**§5 L6 reward/risk gate.** Across 126 candidate evaluations with the
completeness veto bypassed, the nearest target sits a median of 42.75 points from
E, giving a median reward/risk of 1.32 against a required 2.00. **30 of 103
scored evaluations (~29%) clear the gate**; 73 are rejected by it.

An earlier, cruder version of this measurement looked only at swing targets from
the 09:29 price and suggested the gate was almost never satisfiable. That was
wrong: including the other LB candles' boundaries, which §L6 also admits, supplies
targets much farther out. The gate is restrictive, not prohibitive.

### What this compounds to

L1 (~31%) × L2 (~34%) ≈ **~11% of sessions** reach candidate selection at all.
L4's location gate, L5's requirement that the first sweeping candle also close
beyond E inside a 15-minute window, and L6's ~29% pass rate all apply after that.
A trade rate of roughly **2–3% of sessions** is the realistic expectation.

Set that against §7:

> At least 100 closed holdout trades, at least 125 eligible holdout sessions and
> at least six calendar months.

A 36-month study is ~750 eligible sessions, so the newest-25% holdout is ~186
sessions. Reaching 100 closed trades there requires a **~54% trade rate**. At
2–3%, ~186 holdout sessions yield roughly **4–6 trades**.

**The registered LB-OPEN experiment cannot reach its own evidence floor at the
planned study length — short by a factor of roughly 20.** Producing 100 holdout
trades at this selectivity would need on the order of 3,000+ holdout sessions,
i.e. a study spanning decades.

### Confidence and caveats

The direction of this result is solid; the precise numbers are not.

- Bracket geometry uses Yahoo **full-session** daily bars, whereas §2 requires
  regular-session `[09:30,16:00)` candles. Regular-session candles are narrower,
  which shifts the candidate price density somewhat. The 13-week price drift
  dominates the spread, so the effect is second-order, but it is real.
- The touch-window range distribution rests on **19 sessions**. It is an anchor,
  not an estimate with a confidence interval.
- The L2 factor assumes bias/London independence, which is not established.
- `NQ=F` is a continuous front-month series. §2 forbids it for absolute
  historical levels, which is what L1's brackets are.

None of these caveats plausibly closes a 20× gap.

---

## 4. What follows

Under §7, *"If the primary candidate fails the evidence gates, the conclusion is
rejection or insufficient evidence — not automatic parameter optimization until a
winning chart appears."* The same standard applies here, so the honest reading is:

**LB-OPEN v1 is not rejected on performance — it is unfalsifiable at the planned
study length.** No amount of data collection fixes that, because the constraint is
the gate stack's selectivity, not the sample.

Three responses are available, and choosing between them is a research decision,
not an implementation one:

1. **Register a v1.1** that relaxes one named gate, with the change and its
   justification recorded *before* testing, per §1: *"Change any operative rule
   only by creating a new version and recording the change before testing new
   unseen data."* The L1 double-touch resolution is the highest-leverage
   candidate, since it alone removes ~62% of sessions.
2. **Lower the evidence floor deliberately**, accepting that a 20-trade study
   cannot support the §7 bootstrap gates, and say so explicitly rather than
   quietly.
3. **Drop LB-OPEN.** §8.6 already warns that *"No automatic selection of LB-OPEN
   is justified by Q90's failure."* Nothing found here argues for it either.

What should **not** happen is exporting 39 months of dated contract data first.
That work is only worth doing after the selectivity question is settled, because
the current specification cannot produce a conclusive result from it.
