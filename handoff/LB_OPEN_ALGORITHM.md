# LB-OPEN — implementation specification

**Purpose of this document.** A self-contained, unambiguous statement of the
LB-OPEN trading algorithm, sufficient to implement it on TradingView (Pine
`strategy()`) or any other platform without reading the source rulebook.

**Provenance.** LB-OPEN is specified in §5 of `Strategy_Rulebook_v1.md`. This
document restates those rules with every ambiguity resolved, and cites the source
clause for each. Where this document and the rulebook differ, the rulebook wins —
but every known difference is listed in §10 below.

**Status.** The algorithm has a verified reference implementation (Python, 41
passing tests) but **no performance evidence whatsoever**. Read §11 before
spending anyone's time on it.

---

## 1. Scope

| | |
|---|---|
| Instrument traded | **MNQ** (Micro E-mini Nasdaq-100), exactly 1 contract |
| Signal source | The same MNQ series — no second instrument |
| Point value | **$2.00** per index point |
| Tick | **0.25** points = **$0.50** |
| Bar resolution | **1 minute**, regular trade OHLC (not Heikin Ashi / Renko) |
| Timezone | **America/New_York** throughout, DST-aware |
| Contract | **Dated, unadjusted** (e.g. `MNQZ2025`). Never continuous or back-adjusted |

**Contract rule.** L1 (below) draws price levels from up to 13 weeks of history.
A quarterly contract lives about 13 weeks. Therefore **one dated contract per
backtest run**; never let a run span a roll, or L1's levels are spliced across
expiries and are meaningless. Roll boundary: the research switch is 18:00 NY on
the Sunday preceding the Monday of CME's customary roll week (customary roll =
the Thursday 8 days before the third-Friday expiry).

**Sessions.** Trade only full regular US cash sessions. Skip weekends, NYSE
holidays, and **early-close days entirely**. Never drop a day after the fact for
being volatile.

---

## 2. Clock, intervals, causality

These three rules are where most backtests of this strategy will go wrong.

1. **All intervals are half-open `[start, end)`.** A tick at a boundary belongs
   to the new interval.
2. **A bar's close becomes available at the bar's END timestamp.** A 1-minute bar
   starting 09:28 closes at 09:29 and is usable from 09:29. The bar starting
   09:29 is **not** usable at 09:29.
3. **A decision at time `t` may use only observations available at or before
   `t`.** Never use an interval's eventual extreme where its as-of-now extreme is
   required. A 3-bar swing centred on bar `j` is unconfirmed until bar `j+1`
   closes.

**Missing data.** A missing minute is missing — never forward-fill, never
substitute a nearby date, never synthesise a flat bar for a closed interval.
- Missing *optional candidate* → drop that candidate.
- Missing *mandatory context* → veto the setup.
- Missing minute while an order or position is live → **the run is invalid**;
  repair the history. Do not bridge, invent an exit, or delete just that trade.

**Completeness requirements** (exactly these, no more):
- Each *selected* LB historical 30-minute interval: **100%** of its minutes.
- Execution window `09:29` → `12:00` inclusive: **100%**.
- From each snapshot forward through its decisions: **100%**.
- Pre-snapshot context windows (L1 touch, L2 Asia/London, L4 overnight): must be
  *present*; a declared coverage floor below 100% is permitted but must be
  recorded with the results.

---

## 3. Cost and risk model

| Setting | Baseline | Stress (required second run) |
|---|---|---|
| Fee per contract per filled side | $1.25 | $2.50 |
| Market/stop slippage | 1 adverse tick | 4 adverse ticks |
| Limit verification (trade-through) | 1 tick | 2 ticks |
| Initial stop | 30.00 points | 30.00 points |
| Starting equity | $50,000 | $50,000 |

Derived, and both **must** reproduce exactly or your port is wrong:

```
round_trip_fees = 2 × per_side_fee
planned_loss    = 30 × $2 + round_trip_fees + slippage_ticks × $0.50

baseline: planned_loss = $63.00   min target distance = 64.25 points
stress  : planned_loss = $67.00   min target distance = 69.50 points
```

**Slippage is in the fill price; fees are deducted separately. Never both.**

Other limits: one position, no pyramiding/averaging/reversal. **One entry order
per day, maximum.** Stop new entries once realised daily P&L ≤ −$120. Per-entry
risk gate: `planned_loss ≤ 0.25% × current closed equity`. **Flat at 12:00 NY**,
unconditionally.

---

## 4. Daily timeline

```
09:29   snapshot 1  — freeze daily context (L1, L2, L4) and window-1 selection
09:30   window 1 opens                          ─┐
09:44   last bar that can confirm window 1       │ entry window 1 = [09:30, 09:45)
09:45   window 1 pending orders expire          ─┘
09:44   snapshot 2  — window-2 selection only (context stays frozen)
09:45   window 2 opens                          ─┐
09:59   last bar that can confirm window 2       │ entry window 2 = [09:45, 10:00)
10:00   window 2 pending orders expire          ─┘
12:00   mandatory flatten
```

**Window 2 runs only if NO window-1 order was submitted** — whether that order
filled, expired, was cancelled or rejected. A submitted order consumes the day.

---

## 5. L1 — frozen daily context and bias

At **09:29** (i.e. once the minute ending 09:29 has closed):

1. `P0` = close of the minute **ending** 09:29 (the bar starting 09:28).
2. Collect the **13 same-weekday** prior sessions at −7, −14, …, −91 calendar
   days. Each contributes its **regular-session `[09:30,16:00)` daily OHLC**, and
   only if that session was a full eligible session with complete data. Drop any
   that fail. **Need ≥ 1 surviving candle**, else no trade.
3. Pool all `{open, high, low, close}` from surviving candles; deduplicate on the
   tick grid. Then:
   - `B_low`  = **closest pooled price strictly below** `P0`
   - `B_high` = **closest pooled price strictly above** `P0`
   - If either is absent → **no trade**.
4. Scan today `[00:00, 09:29)`. A level is **touched** if any minute satisfies
   `low ≤ level ≤ high` — this is the one **inclusive** test in the algorithm.
   - only `B_low` touched → **bias LONG**
   - only `B_high` touched → **bias SHORT**
   - **both or neither → no trade**

Freeze `P0`, `B_low`, `B_high` and the bias for the whole day.

> Looking backwards from a 09:29 snapshot is causal — every input already exists.

---

## 6. L2 — London filter

- **Asia** = *previous calendar day* `[18:00, 00:00)` → high `AH`, low `AL`
- **London** = today `[00:00, 06:00)` → high `LH`, low `LL`, last close `LC`

```
bullish = (LL < AL and LH < AH and LC > AL)  or  (LH > AH and LC > AH)
bearish = (LH > AH and LL > AL and LC < AH)  or  (LL < AL and LC < AL)
```

All comparisons **strict** — equality never satisfies a take/break. Neither or
both → no trade. **The classification must agree with the L1 bias**, else no
trade. Freeze at snapshot 1.

*(These two are mutually exclusive by construction; "both" is unreachable.)*

---

## 7. L4 — overnight range location gate

At snapshot 1 freeze `RH` / `RL` = high / low over *previous day* `18:00` →
`09:29`. Require `RH > RL`.

For a candidate entry `E`:  `x = (E − RL) / (RH − RL)`

| Region | Condition |
|---|---|
| Extreme Discount | `x ≤ 0.20` |
| Discount | `0.20 < x < 0.40` |
| Dead Zone | `0.40 ≤ x ≤ 0.60` |
| Premium | `0.60 < x < 0.80` |
| Extreme Premium | `x ≥ 0.80` |

**Long requires `x < 0.40`. Short requires `x > 0.60`.** Values outside `[0,1]`
stay in the corresponding extreme. Frozen for both windows.

---

## 8. L3 / L5 / L6 — per-window selection

Run at snapshot 1 (anchor 09:30) and, if window 2 is still live, at snapshot 2
(anchor 09:45). **Only current price and LB selection refresh; bias, London and
the range proxy stay frozen.**

### 8.1 Build LB candidates (L3)

Let `P` = close of the last completed minute at the snapshot.

For each offset in **`3h, 6h, 12h, 24h, 1w`**, the historical candle starts at
the anchor minus that offset **in local NY wall-clock terms**:

- `3h/6h/12h`: subtract hours from the wall clock.
- `24h`: **same wall-clock time, previous calendar day.**
- `1w`: **same wall-clock time, 7 calendar days earlier.**

> Do this as wall-clock date/time arithmetic, **not** modulo-1440 minute
> arithmetic. If the resulting local time is ambiguous or non-existent (DST
> change), **drop that candidate** — never substitute a nearby date.

The candle is `[start, start + 30 min)` aggregated from minute bars. It must be
**entirely complete by the snapshot**; otherwise drop it. (E.g. a Monday 09:30
anchor's `24h` candidate starts Sunday 09:30 — exchange closed, no minutes, so it
drops. It does **not** fall back to Friday.)

Then per candidate:

| | Long | Short |
|---|---|---|
| Zone | `[max(open,close), high]` | `[low, min(open,close)]` |
| Entry `E` | `high` | `low` |
| Requirement | `E < P` | `E > P` |

Drop zero-width zones. **Rank by `abs(E − P)` ascending; ties broken by shorter
offset in the order `3h, 6h, 12h, 24h, 1w`.**

### 8.2 Gate each candidate, in this order

Take the **first** candidate passing all three. If none passes, the window has no
setup.

**Gate 1 — location (§7).** `x < 0.40` for Long, `x > 0.60` for Short.

**Gate 2 — liquidity (L5).** Among swings confirmed since *previous day 18:00*
and still **unconsumed** at the snapshot:
- Long → nearest **unconsumed swing low strictly below `E`**
- Short → nearest **unconsumed swing high strictly above `E`**
- Equal-priced candidates: use the **most recently confirmed**.
- None → reject this candidate.

> **Swing definition.** On 1-minute bars, a swing high is a bar strictly higher
> than **both immediate neighbours**; swing low, strictly lower. Equal
> highs/lows form no swing. "Immediate" means genuinely adjacent minutes — if a
> minute is missing, the bars either side are **not** neighbours and form no
> swing. A swing centred on bar `j` is confirmed only once bar `j+1` closes.
> **Consumed** = some bar from the confirming bar `j+1` onward has strictly
> crossed the level (high > level for a swing high; low < level for a swing low).
> Consumption is permanent — returning inside does not un-consume it.

**Gate 3 — structural target and reward/risk (L6).** Build the target candidate
set from:
1. Unconsumed confirmed **swing highs** (Long) / **swing lows** (Short), same
   swing history as Gate 2; and
2. The **high and low boundaries of the other *available* LB candles for this
   window**.

> "Available" means *the interval was complete* — **not** "entry-eligible". A
> candle that failed the `E < P` rule or had a zero-width zone still contributes
> its boundaries. This matters: for a Long, every entry-eligible candle has
> `E = high < P`, so restricting this set to them discards all overhead
> boundaries and pushes the target farther out — the exact "jump" the next rule
> forbids.

Deduplicate on the tick grid, keep only prices **strictly in the profitable
direction** from `E`, and take **the nearest**.

> **Do not jump over a nearer target to obtain a better ratio.** Only the nearest
> is ever gated.

Gate: `(abs(T − E) × 2 − round_trip_fees) / planned_loss ≥ 2.0`
→ **≥ 64.25 points baseline, ≥ 69.50 points stress.**

If the nearest target fails, **reject this LB candidate** and try the next ranked
one. No target → reject.

If a level is off the tick grid, snap it **toward `E`** before use. (Rounding to
*nearest* can only ever turn a failing gate into a passing one.)

Freeze zone, `E`, swing and `T` for the window.

---

## 9. L5 trigger, L6 bracket, L7 order state

### 9.1 Trigger

- If the selected swing is swept during `09:29–09:30` (window 1) or
  `09:44–09:45` (window 2), **the setup expires before activation**.
- Otherwise, **the first completed minute inside the entry window that strictly
  sweeps the selected swing is the one and only opportunity.** If it fails the
  reaction test, the window's setup expires. **A later reclaim cannot revive it.**

**Reaction test** — all conditions on that same bar simultaneously:

| | Long | Short |
|---|---|---|
| Range intersects the frozen LB zone | ✔ | ✔ |
| Sweep | `low < swing_low` | `high > swing_high` |
| Reclaim | `close > swing_low` **and** `close > E` | `close < swing_high` **and** `close < E` |

Strict penetration on the tick grid is the entire sweep threshold — no
"significance", "quality" or "strength" criterion exists.

### 9.2 Order

On the qualifying bar's **close**, submit a **passive limit at `E`**.
- Earliest window-1 submission: 09:31. A bar closing at 09:45 cannot authorise a
  window-1 entry.
- **Never** submit at the snapshot, chase, reprice, or claim a fill inside the
  confirmation bar itself.
- If `T` was already touched between the snapshot and submission, the setup
  expires.

**Fill rule.** The limit fills only if price trades **through** it by the
verification amount (1 tick baseline, 2 stress): Long fills when
`low ≤ E − through`; Short when `high ≥ E + through`. **Fill at `E` exactly — no
price improvement.**

Pending orders expire at 09:45 (window 1) / 10:00 (window 2). No fill at or after
expiry counts.

### 9.3 Bracket and exits

`stop = E ∓ 30.00` (Long minus, Short plus). `target = T`.

**No break-even move, no trailing, no partials, no target movement.** Exits are:
target, stop, or the 12:00 flatten — nothing else. A filled position is managed
to its own exit; the entry window closing does not force it out.

### 9.4 Intrabar ordering (matters more than it looks)

Resolve each minute in this order:

1. **Stop already gapped through at the bar open** → fill at
   `worse(stop, open) ∓ slippage`. This is an established fact, not ambiguity.
2. **Target already executable at the bar open** → fill at `T`. (A resting target
   executable at the open fills before a later stop.)
3. **Both stop and target reachable within the bar, order unknown** → take the
   **stop**, and flag the trade ambiguous.
4. Otherwise whichever single level is reached.

In the **entry** minute specifically:
- entry + stop both reachable → **stopped**, flagged ambiguous.
- entry + target both reachable, ordering unestablished → **do not credit the
  target**; carry the position forward. A target touched before entry is not
  profit from that entry.

---

## 10. Known resolutions and departures

These are places where the source rulebook was ambiguous and this document
chooses. An implementer should know which lines are interpretation:

1. **Pre-snapshot context completeness.** §2 of the rulebook names the intervals
   that must be minute-complete; the L1 touch, L2 Asia/London and L4 overnight
   windows are *not* among them. This spec treats them as needing to be present
   with a declared coverage floor, recorded with results. The strictest reading
   (100%) is a valid alternative and excludes far more sessions.
2. **Target snapping direction.** Snapping toward `E` is this spec's choice; the
   rulebook only requires order prices to be on the tick grid.
3. **Target source = available, not entry-eligible candles** (§8.2 Gate 3). The
   rulebook says "available"; this is the literal reading.
4. **Swing levels compared as recorded**, not re-quantised.
5. Window-2 selection refreshes only price and LB candidates; the rulebook is
   explicit, but it is easy to re-derive bias/London by accident — don't.

---

## 11. Before anyone spends time on this — read

A reference implementation exists and is verified. **It has produced no evidence
of profitability, and there is good reason to expect it cannot produce a
conclusive result at all.**

Measured properties of the gate stack (details in `docs/FINDINGS.md`):

| Gate | Sessions admitted |
|---|---|
| L1 bias (exactly one bracket touched) | **~31%** |
| L2 London agreement | ~34% of those |
| L6 reward/risk (≥ 64.25 pts to the *nearest* target) | ~23% of candidate evaluations |

Over 570 sessions with a full 13-candle lookback, the `B_low…B_high` bracket
around `P0` is a **median 80.6 points** wide while the `[00:00,09:29)` window
sweeps a **median 222 points** — so both levels are usually touched, which L1
resolves as no-trade. Compounded, expect a trade on roughly **2–3% of sessions**.

The registered evidence standard wants **≥ 100 closed trades in a 25% holdout**.
A 36-month study is ~750 sessions → ~186 holdout sessions → that needs a **~54%**
trade rate. At 2–3% you get **4–6 trades**.

**So the experiment as specified is short of its own evidence floor by roughly
20×, and more lookback history makes L1 *stricter*, not looser.** This is a
property of the rules, not of the sample — no amount of data fixes it.

If the goal is a verdict rather than an implementation, the selectivity question
has to be settled first — most usefully by deciding what L1 should do when both
brackets are touched, since that single rule removes ~62% of all days.

---

## 12. Verifying a port

`acceptance_bars.csv` (6,211 one-minute bars) and `acceptance_vectors.json` are
in this folder. Feed the bars to your implementation for session **2026-09-15**
and assert the vectors. Key values:

| | |
|---|---|
| `P0` (close of minute **ending** 09:29) | **19997.00** |
| `B_low` / `B_high` | 19970.00 / 20060.00 |
| bias / London | long / bullish |
| `RH` / `RL` | 20020.00 / 19881.00 |
| chosen LB offset | `3h` |
| `E` / stop / `T` | 19930.00 / 19900.00 / 20020.00 |
| selected swing low | 19900.00 |
| reward/risk baseline / stress | 2.81746 / 2.61194 |
| entry → exit (NY) | 09:32 → 10:25, target |
| net baseline / stress | **+$177.50** / **+$175.00** |

Two traps this fixture deliberately catches:
- `P0` is **19997.00**, not 20000.00. If you get 20000 you are reading the bar
  starting 09:29, which does not close until 09:30 — a causality leak.
- The pre-market ramp contains **equal adjacent lows**, which must form **no
  swing**. If you detect one there, your swing test is not strict.

This fixture is synthetic and proves *correctness of implementation only*. It is
not market data and says nothing about profitability.
