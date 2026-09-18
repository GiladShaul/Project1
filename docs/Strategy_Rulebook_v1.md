# Quarter/SMT and LookBack — v1.0 rules and current research record

Prepared: 17 September 2026. Evidence updated: 18 September 2026. Platform used: TradingView.

**Status: Q90-PSP and its stock adaptation failed the completed native historical screens and are rejected for live deployment. Removing break-even from the stock variant also failed validation. No profitable replacement has been demonstrated. The full independent conservative replay remains incomplete; LB-OPEN remains separately specified, unimplemented and untested.**

This is the current strategy research record. Sections 1–6 retain the original v1.0 specifications, including the assumptions and exclusions. Section 7 retains the validation criteria and updates platform status; section 8 records the accumulated results, lessons and unresolved requirements. Recording a failed rule does not endorse deploying it.

The exact pre-test document is preserved in [Strategy_Rulebook_v1_Frozen_2026-09-17.md](Strategy_Rulebook_v1_Frozen_2026-09-17.md), SHA256 `689531478C7AC45688CF39DE00590F7933EC46C28C49B017033907E83977440C`. Historical references to that original hash refer to the archived snapshot. This update changes documentation, not the executable Pine or replay rules, and does not create a newly validated strategy version.

## 1. Decisions and scope

The initial traded instrument is **one Micro E-mini Nasdaq-100 futures contract (MNQ)**. The primary candidate, **Q90-PSP**, uses matched-expiry NQ and ES futures for signals. **LB-OPEN**, a separate secondary candidate, uses MNQ prices for both its signals and executions. Run and report them separately. Do not combine their signals or select the better-looking candidate after examining the final holdout.

Q90-PSP is designated the primary candidate before observing results because its rules require fewer assumptions. This is a research preference, not evidence that it is more profitable.

Labels used below:

- **Retained:** explicitly present in the user's documents.
- **Resolved:** a precise choice supplied here to remove an ambiguity.
- **Changed:** a deliberate departure from the original method.
- **Excluded:** not part of this version; it cannot be used as discretionary confirmation or an exception.

Every numerical choice added here is a proposed research parameter, not a measured optimum. Change any operative rule only by creating a new version and recording the change before testing new unseen data.

### Resolution register

| Issue in the source | Binding v1 decision | Classification |
|---|---|---|
| LookBack describes the US opening as 16:30 NY | Use 09:30 America/New_York; 16:30 is not retained as an ET trading anchor | Changed; assumes the intended event is the US cash open |
| Order at 09:29 depends on the 09:30 price and a later reaction | Freeze selection at 09:29; submit only after a completed reaction candle | Changed |
| Many asset classes, undefined point/micro units | MNQ only; one point means 1.00 Nasdaq index unit; execution contract is explicit | Resolved scope |
| Which asset/direction to trade after SMT | Trade MNQ; low-side SMT permits long, high-side SMT permits short | Resolved |
| Different timeframe combinations | Primary model uses adjacent 90-minute quarters and five-minute PSP confirmation | Changed: this is not the source's 5M/22.5-minute combination |
| PSP versus PC and FVG | PSP required; PC/FVG recorded if available but cannot authorize or block v1 entries | Changed scope |
| Quarter's ultimate highest/lowest candle | Use only the extreme observed by the confirmation timestamp | Resolved causally |
| SMT later disappears | A second asset's breach invalidates that side for the quarter; no reset after retreat | Resolved |
| Unlimited concurrent/repeated trades | One position; Q90 maximum two entries/day and one entry/current quarter; LB maximum one submitted entry/day | Changed risk policy |
| Break even/minimum loss | Q90 moves stop to actual entry price; explicitly gross break even before fees | Resolved |
| Fixed versus structural profit target | Q90 uses +90 points; LB uses the nearest qualifying structural target | Separate setups |
| Historical bias selection | Same weekday, prior 13 calendar weeks, completed daily OHLC boundaries | Resolved replacement for discretionary selection |
| Unspecified price-distribution indicator | Explicit overnight-range proxy for LB; not claimed to reproduce the missing indicator | Changed |
| Undefined continuation, ATH exceptions, alternate-day/week mappings | Excluded from v1 | Changed scope |
| TDO/AMO and other unavailable indicator outputs | Not required by either candidate; no guessed AMO values or screen-reading dependency | Excluded |
| News/earnings as discretionary 'Energy' | LB event is only the scheduled cash open; no discretionary news selection | Changed scope |
| 'Significant' sweep/reaction | Strict level penetration and a closed one-minute reclaim/rejection, defined below | Resolved |
| Microquarter cuts a five-minute candle | Microquarter mode is inactive; any later version needs exact boundary data and its own test | Explicit scope |

The supplied indicators do not implement this rulebook. Their conflicting clocks, rolling drawings, and 24-hour LookBack date calculation must not be used as authoritative signal history.

## 2. Shared clock, contracts and data

### Clock and sessions

Use timezone-aware `America/New_York`. Store timestamps in UTC and derive NY local dates/times. Never hard-code an Israel/NY offset. The regular US stock-market session starts at 09:30 ET; futures have their own trading schedule [1].

All intervals are half-open `[start, end)`. A tick at a boundary belongs to the new interval. A completed candle ending at a boundary belongs to the interval containing its observations; its close becomes available at that ending timestamp. Orders cannot be filled retrospectively within the candle that authorized them.

The research cycle hierarchy retains:

| Level | Parent interval | Four quarters |
|---|---|---|
| Weekly display | Sunday 18:00 through Thursday 18:00 | Four 24-hour trading-day blocks, Monday through Thursday |
| Daily display | Previous calendar day 18:00 through current day 18:00 | 18:00–00:00, 00:00–06:00, 06:00–12:00, 12:00–18:00 |
| Q90 operative | Each six-hour parent | Four consecutive 90-minute blocks |
| Micro display only | Each 90-minute parent | Four 22m30s blocks |

Asia and London in this document are **model labels** for 18:00–00:00 and 00:00–06:00 NY. They are not assertions about actual overseas exchange openings. Friday is eligible for the operative intraday setups; exclusion from the four-day weekly display is not a Friday trading veto.

Trade only on full regular US cash-market sessions. Skip cash-market holidays and shortened sessions using the published calendar. Do not remove a day retrospectively because it was volatile. Other scheduled announcements are not a signal or exclusion in v1; their impact remains in results and cost stress tests.

### Contract identity

Use dated, unadjusted contracts. For Q90, NQ, ES and MNQ must have the same quarterly expiry. Use CME's published customary roll date for each expiry [3]. The research switch is at 18:00 NY on the Sunday preceding that Monday roll date; all three symbols switch together. Log each actual dated ticker. Do not infer the active contract separately from each feed's volume.

Reset quarter and order state on a contract switch. Historical features must come from the newly selected contract's own available prior history. Do not splice price levels across contract expiries or use retrospectively back-adjusted continuous prices for absolute historical levels. Missing warmup means no signal. A continuous-chart run may be an exploratory screen only, labeled as such.

### Data and availability

- Research baseline: synchronized one-minute trade OHLC for MNQ; also NQ and ES for Q90. All three records must represent the same completed interval.
- Aggregate five-minute candles from complete minute intervals on NY clock multiples of five minutes. Aggregate Q90 extrema from complete minute intervals at exact cycle boundaries.
- Require complete minute records for the elapsed prefix of the current operative quarter, the entire completed reference quarter, and each selected LB historical interval. Completeness of the current quarter never requires waiting for its future end. Never forward-fill a missing asset to manufacture SMT. If required data are unavailable, log the reason and skip that signal/window.
- An exchange-closed interval is unavailable, not a flat candle. Do not substitute Friday for an unavailable Sunday LB candle. Missing candidates can be removed; missing mandatory context vetoes the setup.
- Historical daily candles for LB are regular-session `[09:30,16:00)` MNQ OHLC, with only completed full sessions eligible.
- A decision at time `t` can access only observations with availability time at or before `t`. A three-candle swing at candle `j` is confirmed only after `j+1` closes.
- Prices and orders must conform to the instrument's tick grid. MNQ has $2 per index point and a 0.25-point tick, or $0.50 per tick [2].

Inventory execution coverage before inspecting trade outcomes. Every scored session requires complete MNQ execution records from 09:29 through the 12:00 flatten execution. Predetected incomplete sessions are reported as data-quality exclusions, with dates and coverage ratios; they are not silently included as zero-trade days. All remaining eligible no-trade days are included. An unexpected missing execution interval while an order or position is active invalidates that run until the history is repaired: do not bridge the gap, invent an exit, or remove only the affected losing/winning trade. A documented exchange halt is handled as a halt and a possible gap at resumption, not as a fictitious stream of flat candles.

LB additionally requires continuous complete records from each snapshot through its decisions. A missing interval before order submission expires that window, because it may conceal the first sweep or a target touch. Missing data after submission follow the active-order rule above.

## 3. Shared execution and risk policy

These limits define the research portfolio; they are not a recommendation for the user's account size.

| Setting | v1 value |
|---|---|
| Simulation starting equity | $50,000, USD |
| Position size | Exactly one MNQ contract; never increase after losses |
| Concurrent positions | One; no pyramiding, hedged overlap, reversal, or queued stale signals |
| Initial stop | 30.00 index points = 120 ticks = $60 gross planned risk |
| Commission assumption | $1.25 per contract per filled side, including an assumed all-in fee allowance |
| Market/stop slippage | One adverse tick per fill in baseline |
| Limit verification | Require trading one tick through the limit; fill at the limit, no favorable improvement assumed |
| Per-entry risk gate | Planned initial loss plus assumed fees/stop slippage must be at most 0.25% of current closed equity |
| Daily entry gate | Stop new entries after net realized daily P&L reaches -$120 or after the setup's entry cap |
| Mandatory flat time | 12:00 NY; cancel entries and close any remaining position at the first executable price |

The fee/slippage figures are declared test assumptions, not the user's actual broker charges. Replace them with documented costs before any readiness claim. Do not charge the same modeled slippage twice: it is included in fill prices, while fees are deducted separately. Subtract identifiable incremental platform/data operating expenses separately in the economic report; without those inputs, all-in profitability remains unknown.

The daily loss gate blocks further entries; it does not promise a maximum daily loss. A currently open position can lose more through slippage/gaps. One contract's planned initial loss at these assumptions is $60 + $2.50 fees + $0.50 stop slippage = **$63**. The entry slippage changes the fill price; bracket distances are measured from that actual fill.

All cost-dependent gates use the active scenario: `round_trip_fees = 2 * per_side_fee`; `planned_loss = 60 + round_trip_fees + stop_slippage_ticks*0.50`. Recompute these values in a stress run rather than retaining baseline constants. The stated stress scenario has $5 round-trip fees and $2 modeled stop slippage, so planned loss is $67.

All entries receive an initial protective stop and target upon execution. If a protective order cannot be established, the operational policy is to flatten and suspend new entries. After a disconnect or restart, reconcile position/order state before issuing anything new. A software implementation must log acknowledgments and reject duplicate event IDs.

### Bar-based execution convention

This rulebook deliberately uses a reproducible one-minute management baseline. It does not claim to reproduce immediate tick-by-tick discretionary management.

1. A closed candle authorizes an order only after its close. Historical market entry is the next minute's open plus adverse slippage. Never fill on the signal candle's close or its earlier high/low.
2. Entry acceptance requires the fill timestamp to remain in the setup's entry window. If a gap/delay moves execution outside the window, cancel/skip; do not invent the missing open.
3. Resting stop/target orders may fill during a minute. For a long stop gapped through, use the worse of stop and minute-open price, then adverse slippage; reverse for shorts. A limit cannot fill worse than its limit.
4. If available intrabar data cannot establish the sequence of entry and exits, use the adverse feasible sequence and flag the trade. If both stop and target can follow entry, assume the stop first. If an entry limit and target are reached in one minute but target-after-entry cannot be established, do not credit a same-minute target exit: retain the position unless a feasible stop execution closes it. A target touched before an entry is not profit from that entry.
5. Q90's +45 trigger is detected using the completed minute's favorable extreme, if the position survives that minute. Its stop amendment becomes effective only in the next minute. It cannot change an earlier fill. Stop/target executions take precedence over a later amendment.
6. More granular verified data may resolve an ambiguity, but the same declared data/fill policy must be applied to all comparable trades. Bar Magnifier helps with some ordering; it does not establish actual queue position or guarantee complete intrabar history [4, 6].

## 4. Primary candidate: Q90-PSP

### Q1. Eligibility and reference periods

Signal prices: NQ and ES. Execution prices: MNQ. All dated contracts share expiry.

Entry fills must occur in `[09:30,11:00)` NY. Confirmation closes can occur at 09:30, provided the completed five-minute candle meets every condition. A confirmation at 11:00 is too late.

Use the NY-AM parent `[06:00,12:00)`, divided into `[06:00,07:30)`, `[07:30,09:00)`, `[09:00,10:30)`, `[10:30,12:00)`. Compare only the current block with its immediately preceding block in this same parent. The first block has no eligible comparison.

For each reference asset `i`, freeze preceding-block high `H_i` and low `L_i`. Maintain current-block cumulative high `h_i(t)` and low `l_i(t)` using records available by `t`. Do not compare NQ's numerical price with ES's numerical price; each compares to its own reference.

### Q2. SMT

At each synchronized completed minute:

- High breach flags: `h_NQ > H_NQ` and `h_ES > H_ES`.
- Low breach flags: `l_NQ < L_NQ` and `l_ES < L_ES`.
- High SMT is active if exactly one high-breach flag is true. It permits **short MNQ**.
- Low SMT is active if exactly one low-breach flag is true. It permits **long MNQ**.
- Equality is not a breach. A strict breach on the valid tick grid requires no additional numerical threshold.
- Once both assets breach on one side, that side is invalid for the rest of the block. Returning inside the old range does not reset a cumulative breach.
- If both high and low SMT are active at the same evaluation, veto new entries for the remainder of that block. Existing positions retain their exits.

It is irrelevant which reference asset breaches first for selecting the traded symbol: MNQ remains the sole execution instrument. This is a chosen reversal hypothesis, not a conclusion implied by the word SMT.

### Q3. PSP confirmation

At a synchronized, completed five-minute NQ/ES candle wholly contained in the current block:

**Long:** low SMT remains active; NQ `close > open`; ES `close < open`; the NQ candle's low equals the current-block low known at this close.

**Short:** high SMT remains active; NQ `close < open`; ES `close > open`; the NQ candle's high equals the current-block high known at this close.

Both candles must be closed. Reject a doji in either asset. Ties with an earlier observed block extreme qualify; equality is explicit and cannot use the eventual block extreme. SMT may first arise inside this same five-minute candle, but must remain valid at its close. No additional subjective 'significance' criterion exists.

Take the first qualifying PSP when flat and risk gates pass. Submit one market entry for the first executable MNQ price after confirmation. A skipped signal while already in a position is logged and never queued. A submitted/rejected entry consumes that block's opportunity; do not repeatedly resubmit a persistent PSP.

### Q4. Entry-boundary handling

The confirmation candle ending exactly 10:30 may still authorize a trade from `[09:00,10:30)` if the data available at that close satisfy Q2/Q3. It is a closed historical setup, evaluated before clearing that block's state. Its order is valid only for the immediately following executable minute at that boundary, subject to the global entry window; no later catch-up is allowed. Label it with the old block's event ID. The new block then starts with empty cumulative state.

Other unconfirmed SMT states expire at their block boundary. There is no indefinite 'PSP remains relevant until the move starts' rule.

### Q5. Management

Let actual MNQ fill be `E` and direction be `d`, with long `d=+1` and short `d=-1`:

- Initial stop: `E - d*30`.
- Target limit: `E + d*90`.
- On first surviving completed minute reaching a favorable distance of 45 points, amend stop to `E` effective next minute. Amend once; never loosen it.
- Entry-price stop is gross break even. After fees and adverse stop execution it will usually be a net loss.
- No partial exits, trailing stop, averaging down, opposite-signal exit, or re-entry within the same block.
- Maximum two filled entries per day, subject to the daily gate. Flat at 12:00.

The 1:3 initial price ratio is not an expected return. Before fees and with only full wins/losses it implies a 25% arithmetic break-even win rate; time exits and entry-price exits change that distribution. Judge actual net outcomes, not the ratio.

### Q6. PC, FVG and the original microcycle

These definitions resolve terminology but are **inactive** in v1 trading decisions. Do not test multiple versions and silently report only the winner.

- PC: same active SMT and opposite-color/directional requirements as PSP, but NQ's candle does not match the observed block extreme. It cannot substitute for PSP in v1.
- Bullish FVG formed on completed five-minute candle `k`: `low[k] > high[k-2]`; interval `[high[k-2],low[k]]`. Bearish: `high[k] < low[k-2]`; interval `[high[k],low[k-2]]`. Zero-width gaps are absent.
- Only candles wholly within the same six-hour parent can form a diagnostic FVG. Formation is known only after candle `k` closes. Diagnostic corresponding gaps on NQ/ES require the same three candle timestamps and direction; otherwise there is no matched pair. If several exist, select the most recently formed unexpired pair.
- A bullish gap is fully filled only by a later observation at/below its lower edge; bearish only at/above its upper edge. Near-edge touch is insufficient. Retain each fill flag; do not un-fill a gap after a retreat. A diagnostic SMT Fill is exactly one member filled; invalidate it when both fill or at parent end.
- The source's 5M SMT model uses 22m30s quarters, unlike Q90. A future source-faithful experiment would apply Q2 within adjacent microquarters of one 90-minute parent and require a native five-minute confirmation candle wholly inside the current microquarter. Exact extrema require appropriately aligned 30-second or finer data. Five-minute candles cannot be assigned wholesale across a half-minute boundary.

## 5. Secondary candidate: LB-OPEN

This is a fully specified replacement for the source's discretionary choices. Its OHLC levels, range proxy and reaction rule are hypotheses. It is not claimed to reproduce the unavailable proprietary indicators or the trader's judgment.

### L1. Frozen daily context

At **09:29 NY**, after the minute ending 09:29 closes, freeze the daily context. Let `P0` be that MNQ close. Required context is complete before this snapshot. First entry window is `[09:30,09:45)`; optional second is `[09:45,10:00)`.

Use completed full regular-session MNQ daily candles in the preceding 13 calendar weeks with the same weekday as today and the currently traded dated contract. Need at least one eligible historical candle; otherwise no trade. From every eligible candle take `{open, high, low, close}`, deduplicate exact tick prices, and select:

- `B_low`: closest candidate strictly below `P0`.
- `B_high`: closest candidate strictly above `P0`.

Missing either level means no trade. Observe `[00:00,09:29)` today. A touch means a recorded minute satisfies `low <= level <= high`.

- Only `B_low` touched: daily bias Long.
- Only `B_high` touched: daily bias Short.
- Both or neither touched: no trade for the day.

Freeze levels and direction for both windows. The nearer of the two levels is reported as 'active'; it does not override this directional rule. Selecting a level at 09:29 and examining earlier observations is causal because all inputs already exist. Alternate weekdays, month-week mappings, fifth-week interpretations and ATH overrides are excluded.

### L2. London filter

Asia is previous day `[18:00,00:00)` NY, with high `AH` and low `AL`. London is today `[00:00,06:00)`, with high `LH`, low `LL` and last close `LC`. These fixed model windows agree with the source's six-hour hierarchy.

- Bullish = `(LL < AL and LH < AH and LC > AL)` OR `(LH > AH and LC > AH)`.
- Bearish = `(LH > AH and LL > AL and LC < AH)` OR `(LL < AL and LC < AL)`.
- Neither or both = no trade. Require the classification to agree with daily bias.

These formulas supply the source's missing reference for 'closes above/below'. Equality does not satisfy a strict take/break condition. Freeze the result at the first snapshot.

### L3. Historical LookBack candles and entry zones

For first window, event anchor `A=09:30` and selection snapshot `S=09:29`. For second window, `A=09:45`, `S=09:44`; only current price and LB selection are refreshed. Daily bias, London context and range proxy remain frozen.

For each anchor construct a historical 30-minute candle beginning at the local NY timestamp obtained by subtracting 3 hours, 6 hours, 12 hours, one calendar day, or seven calendar days. The interval is `[start,start+30 minutes)`, aggregated from minute data. It must be entirely completed by `S`.

Use local wall-clock date/time subtraction, then timezone conversion; do not implement day/week offsets with modulo-1440 clock arithmetic. If a local start is ambiguous/nonexistent during a clock change, or the required trading interval is unavailable, remove that candidate. Do not silently substitute a nearby date. Second-window starts move 15 minutes later and are explicitly rolling 30-minute candles, not mislabeled exchange-aligned candles.

Let `P` be the last completed MNQ minute close at `S`:

- Long zone: `[max(open,close), high]`, proposed limit entry `E=high`, require `E < P`.
- Short zone: `[low,min(open,close)]`, limit entry `E=low`, require `E > P`.
- Zero-width wick zones are ineligible.
- Rank by `abs(E-P)`, then shorter offset in the order 3h, 6h, 12h, 24h, 1w.
- Apply the location, liquidity and target gates below in that order; select the first candidate satisfying all of them. If none qualifies, that window has no setup. Freeze the chosen zone, entry, swing and target for the window.

### L4. Explicit price-location proxy

At the first snapshot, freeze overnight high `RH` and low `RL` from previous day 18:00 through 09:29. Require a positive range. For a candidate entry `E`, define `x=(E-RL)/(RH-RL)`:

| Region | Normalized entry price |
|---|---|
| Extreme Discount | `x <= 0.20` |
| Discount | `0.20 < x < 0.40` |
| Dead Zone | `0.40 <= x <= 0.60` |
| Premium | `0.60 < x < 0.80` |
| Extreme Premium | `x >= 0.80` |

Long requires `x < 0.40`; Short requires `x > 0.60`. Values outside `[0,1]` remain in the corresponding extreme. This feature is named **overnight_range_proxy**. It is an explicit replacement hypothesis, not a reconstruction of the missing indicator. Continuation exceptions and ATH-specific changes are disabled.

### L5. Swing liquidity and sweep/reaction

Use one-minute candles. A swing high is strictly higher than both immediate neighbors; a swing low is strictly lower than both. Confirm only after the right neighbor closes. Equal highs/lows do not form a swing.

At each window's snapshot, search confirmed swings formed since previous day 18:00. A swing is unconsumed if no observation after its confirmation has strictly crossed its level. A cross on the confirmation candle itself also marks it consumed if present. For each candidate `E`:

- Long: choose nearest unconsumed confirmed swing low strictly below `E`.
- Short: choose nearest unconsumed confirmed swing high strictly above `E`.
- Equal-priced candidates use the most recently confirmed swing. Missing swing rejects this LB candidate.

Once selected, a completed one-minute candle wholly inside the entry window must satisfy all conditions at once:

**Long:** its range intersects the frozen LB zone; its low is strictly below the frozen swing low; its close is strictly above both that swing and `E`.

**Short:** its range intersects the zone; its high is strictly above the frozen swing high; its close is strictly below both that swing and `E`.

Monitor every completed minute after snapshot, including the snapshot-to-window gap. If the selected swing is swept during 09:29–09:30 for window one, or 09:44–09:45 for window two, that window's setup expires before activation. Otherwise the first candle inside the window that strictly sweeps the selected swing is the one opportunity: if it fails the reaction test, this window's setup expires. A later reclaim cannot resurrect it. No extra adjective such as 'strong', 'significant' or 'quality' adds a hidden test. Strict penetration on the valid tick grid is the complete sweep threshold.

After a qualifying candle closes, submit a passive retest limit at the frozen `E`. Earliest first-window confirmation is 09:31. A candle closing at 09:45 cannot authorize a first-window entry. Do not submit at 09:29, chase the market, reprice the order, or retrospectively claim a fill during the confirmation candle.

### L6. Structural target and stop

At selection snapshot `S`, construct target candidates from:

1. Unconsumed confirmed swing highs for Long or swing lows for Short, using the same swing history as L5.
2. High and low boundaries of the other available historical LB candles for this window.

Deduplicate tick prices, retain only prices strictly in the profitable direction from `E`, and choose the **nearest**. Do not jump over a nearer target to advertise a better reward/risk ratio. Freeze that target `T` for the window. TDO/AMO/quarter targets are excluded in this independent baseline.

With point value `$2`, require `(abs(T-E)*2 - round_trip_fees) / planned_loss >= 2.0`, using the active cost scenario from section 3. This becomes `(abs(T-E)*2 - 2.50)/63 >= 2.0` in baseline, requiring at least 64.25 points on the tick grid; stress requires at least 69.50 points. This is a conservative proposed minimum planned net reward/risk, not a measured optimum. If the nearest target fails, reject that LB candidate and evaluate the next ranked LB candidate before freezing a setup. No target means rejection. A target already touched between snapshot and order submission expires the selected setup for that window.

Limit fills are modeled at `E` without improvement. Set protective stop at `E-30` for Long or `E+30` for Short. No partial exits, trailing, breakeven amendment, or target movement. Exit on stop, frozen target, or mandatory 12:00 flatten. The stop and holding cutoff are explicit rules even though the original LookBack management is incomplete.

### L7. Window and order state

- A failed daily bias/London/data/risk precheck ends the day.
- A first-window setup that expires or never confirms without submitting an order may be followed by the second window. Its selection was frozen from data available at 09:44; do not use 09:45 price to choose it.
- If **any** first-window entry order was submitted, the second window is disabled, whether that order fills, remains pending, is canceled, or is rejected.
- First-window pending orders expire at 09:45. Second-window orders expire at 10:00. No fill at or after expiry is eligible in simulation; a real cancellation/fill race would require reconciliation.
- A first-window order submitted during `[09:44,09:45)` overrides any provisional second-window selection.
- Only one entry order may be submitted per day. Any filled position is managed to its own exit; entry-window end does not force-close it.
- Five-minute priority buckets are logged as descriptors, not presumed statistical advantages. All eligible confirmations follow identical rules.

## 6. Audit record and minimum acceptance examples

Record every eligible session and every candidate rejection, not only successful trades. Required fields: version/hash, strategy, NY date, dated symbols, decision/availability times, reference-window endpoints, reference highs/lows, cumulative breach flags, confirmation OHLC, feature snapshot, order/fill/cancel timestamps, prices, initial/amended stop, target, quantity, commissions, net P&L, exclusion reason, ambiguity flag, and portfolio equity/drawdown. For LB also save all candidates and ranking; hiding drawings must not erase rejected alternatives.

Before any performance report, an implementation must pass these examples. They are acceptance cases, **not executed test results**:

| Case | Required behavior |
|---|---|
| NY 09:30 in winter versus summer | Both target the same NY event with different UTC offsets |
| LB 24h at today's 09:30 | Select yesterday's dated interval; never today's 09:30 candle |
| Missing Sunday 24h candidate on Monday | Candidate unavailable; do not substitute Friday |
| Reference low 100, current low 100 | No breach |
| NQ low breaches, ES does not, NQ green/ES red PSP closes | Long candidate only after close and only if other gates pass |
| ES subsequently also breaches that low | Low SMT invalid for remainder of current quarter |
| Both high/low SMT active | Quarter veto; do not choose the favorable direction later |
| Confirmation is eventual extreme but was not extreme when observed | No retroactive qualification |
| One reference feed missing | No SMT evaluation using carried-forward data |
| Swing middle candle appears extreme | Unavailable until next candle closes |
| Limit reaction candle traded through entry earlier | No retrospective entry in that candle |
| +45 and initial stop touched with unknown ordering | No retroactive break-even save; adverse feasible outcome |
| Stop and target touched in an unordered bar | Do not assume target first |
| Entry limit and target touched, order unknown, no stop | Do not credit target profit; carry the position forward |
| LB first-window order still pending at 09:45 | Cancel it; second window disabled |
| LB swing swept without same-candle reclaim | Selected setup expires for that window |
| LB selected swing swept before window activation | Expire that window; a later reclaim cannot qualify |
| Missing minute while an order or position is active | Run invalid until history is repaired; no invented P&L |
| Stress cost scenario | Planned stop loss $67; LB minimum target distance 69.50 points |
| Repeat signal/event after restart | Reconcile; never duplicate an already submitted order |
| Contract switch | No comparison with old-contract absolute levels |

## 7. TradingView validation protocol

### Platform configuration

Implement each candidate as a separate Pine `strategy()`, not an `indicator()` [4]. The user-supplied drawing indicators do not execute this rulebook. Q90-PSP now has compiled Pine implementations and an independent replay implementation. The stock adaptation has separate Pine files because its benchmark, clock, sizing and stop distances differ. LB-OPEN has not been implemented. Section 8 identifies the files and measured evidence.

Use standard one-minute candles and explicit dated execution/reference symbols. Aggregate the five-minute signal logic causally from those records. Do not use Heikin Ashi, Renko or synthetic-price fills. Set one-contract sizing, pyramiding off, USD $50,000 initial equity, $1.25 cash commission per contract per side, one-tick market/stop slippage, and one-tick limit verification. Avoid same-close market execution. Keep signal decisions on closed bars; do not rely on tick-recalculation results that cannot be reconstructed historically.

Use Bar Magnifier if available and record its actual coverage. It cannot replace data-quality checks or the rulebook's adverse-ordering requirement. Native broker-emulator results that do not implement the required ordering are preliminary screens, not acceptance evidence [4, 6].

TradingView can export chart/indicator data and strategy results, but chart exports cover data actually loaded [5]. During the sessions, the user's trial enabled the stock and futures Deep Backtesting studies and trade exports. Those successful runs do not establish complete synchronized raw-price exports or future subscription entitlements. Export synchronized dates and contracts for all required symbols; separate screenshots are insufficient. No account credentials belong in this document or any project file.

### Predeclared experiments and data split

1. Register Q90-PSP as primary and LB-OPEN as secondary before observing strategy outcomes. Run each under identical shared cost/risk rules. No combined deployment in v1.
2. Target at least 36 consecutive calendar months of suitable data ending with the most recent complete month before registration. If unavailable, report the actual shorter span; do not claim the planned validation was performed.
3. Split whole eligible sessions chronologically: oldest 50% development, next 25% validation, newest 25% final holdout; use floor for the first two counts and the remainder for holdout. Record exact dates before accessing result summaries. Warmup may use older observations but contributes no scored trades.
4. Keep final holdout hidden while correcting implementation errors and choosing rules on development/validation. Any period already used to select this setup is not genuinely untouched. If that applies, reserve new future sessions instead.
5. After opening final holdout, do not tune and reuse it as an untouched test. A changed rule requires a new version and new unseen evidence. Failed tests remain in the experiment ledger.
6. Retain all eligible zero-trade days and all observed volatility conditions. Record exclusion reasons mechanically. No post-result removal of losing days, contract months or trade directions.

### Proposed evidence gates

These are preregistered research hurdles, not guarantees or universal statistical standards. A lack of adequate history is **inconclusive**, not a pass.

- At least 100 closed holdout trades, at least 125 eligible holdout sessions and at least six calendar months. The floor alone does not establish independent statistical evidence.
- Positive net results after declared trading costs in validation and holdout separately, with holdout net profit factor at least 1.20. Also report the result after identifiable incremental fixed operating expenses; missing expenses prevent an all-in profit claim.
- A lower one-sided 95% confidence bound for average net daily P&L above zero. Use a moving-block percentile bootstrap over all eligible daily results, including zero-trade days. For `n` sessions and block length `L`, form all `n-L+1` overlapping contiguous blocks. For each of 10,000 resamples, sample block-start indices uniformly with replacement, concatenate blocks and truncate to `n`, then compute that resample's daily mean. Use seed 20260917 and record the random-number generator/library version. Sort the 10,000 means and take element 500 in one-based order (the empirical fifth-percentile lower order statistic, with no interpolation) as the lower bound. Run separately for `L=5` and `L=10`, resetting the seed, and require both bounds above zero. Publish the resample-start indices for exact reproduction. This accommodates some daily dependence; it does not eliminate model-selection bias.
- Positive holdout net results under a declared stress run: double per-side fees to $2.50, use four adverse ticks for market/stop fills, and require two ticks of trade-through for limit fills. Rerun selection gates, fills, states and trades under these assumptions; do not merely subtract a flat amount from an unchanged list. Cost-dependent risk and LB reward/risk gates use the stress costs.
- Maximum peak-to-trough closed-equity drawdown no greater than 20 initial gross risk units ($1,200 at one MNQ). Also report marked-to-market drawdown, worst day and consecutive losses; acceptable future drawdown is not bounded by this historical threshold.
- Forward paper observation for at least 60 eligible sessions and 50 closed trades, whichever takes longer, with positive net modeled results and no unexplained signal/order discrepancies. These observations must occur prospectively; replay is not prospective paper trading.

Report net expectancy, profit factor, win/loss/entry-price/time-exit frequencies, average realized win/loss, gross/net P&L, both drawdowns, exposure, number of trades/sessions, no-trade reasons, ambiguous-fill counts, and results by month and direction. Do not describe a nominal entry-price exit as a net win.

As diagnostics only, compare Q90 with (a) its opposite-color/extreme confirmation without the SMT gate, and (b) a no-breakeven variant. These help assess what adds value; their outcomes cannot replace the registered primary candidate on the same holdout. LB's added filters must earn their place in a later separately registered experiment if the baseline fails.

If the primary candidate fails the evidence gates, the conclusion is rejection or insufficient evidence—not automatic parameter optimization until a winning chart appears. LB results remain separately labeled. Positive historical evidence still cannot guarantee future profit.

## 8. Accumulated evidence, lessons and research decision

### 8.1 Evidence status and experiment sequence

Results below are historical simulations, not live returns. Source identity, costs, quantity and coverage belong to each experiment; stock and futures dollar totals are not directly comparable or additive. Older implementation reports are dated snapshots. Their earlier statements that history, exports or a stress run were unavailable are superseded by the completed studies recorded here.

| Experiment | Verified observation | Interpretation |
|---|---|---|
| Initial dated-futures screen, 2026-09-14–17 | Four observed complete sessions, 72 rejected decisions, zero orders | Confirmed compilation and recorded decision/rejection processing; no trade sample from which to assess profitability |
| Initial AAPL/MSFT/NVDA screen, 2026-09-01–16 | Eleven complete sessions per stock, eleven trades total, two winners, arithmetic net −$15.60 | Exploratory only; MSFT's three-trade gain did not establish an edge |
| Stock Deep baseline, 2024-09-17–2026-08-31 | 420 trades, 106 winners, arithmetic net −$1,034.40, pooled net profit factor 0.610 | Rejected for live use |
| Registered stock no-break-even comparison | Same 122 validation entries; net −$368.20 versus baseline −$340.00 | Rejected before the stock final period was opened |
| Original futures Deep baseline, 2023-09-01–2026-08-31 | 744 eligible sessions, 66 trades, nine winners, net −$1,792.00, net profit factor 0.365 | Rejected for live use |
| Original futures Deep stress, same dates | Same 744 sessions and 66 entries; net −$2,103.50, net profit factor 0.321 | Cost robustness failed |
| LB-OPEN, PC/FVG filters and microquarter alternatives | No completed implementation/performance study | Untested; neither approved nor rejected by the Q90 results |

The eleven exploratory stock trades are separate from the 420-trade historical baseline. The 122 no-break-even observations are retests of existing validation entries, not additional independent trades. The initial September 2026 screens were excluded from the historical studies' final windows.

### 8.2 Stock study: results and rejected change

This was a distinct adaptation: AAPL, MSFT or NVDA versus QQQ, regular-session one-minute data, completed five-minute PSP confirmation, 90-minute blocks, entry fills in 11:00–14:00 NY (first possible 11:05), ten shares, a daily-reset five-minute ATR14 stop, 3R target, gross break-even amendment at +1.5R and 15:30 flattening. Costs were $1 per order per side, one adverse tick and one-tick limit verification. These rules do not replace the MNQ rules in sections 2–4.

| Frozen stage | Dates | Baseline trades | Arithmetic net | Decision |
|---|---|---:|---:|---|
| Development | 2024-09-17–2025-09-16 | 183 | −$261.60 | No demonstrated broad edge |
| Validation | 2025-09-17–2026-03-16 | 122 | −$340.00 | Baseline negative; registered alternative worse |
| Final evaluation | 2026-03-17–2026-08-31 | 115 | −$432.80 | Frozen baseline failed |

Final results were AAPL −$165.70 over 50 trades, MSFT −$272.20 over 39, and NVDA +$5.10 over 26. NVDA's small positive subset does not justify selecting it after viewing final results. Each symbol and stage began independently at $50,000; sums do not simulate shared capital, and native drawdowns must not be added.

Only one management change was tested: remove the break-even amendment, leaving the remaining rules unchanged. It had to produce positive aggregate validation net, improve at least two stocks and exceed pooled profit factor 1. It failed all three. AAPL improved $5.20, MSFT worsened $24.60 and NVDA worsened $8.80; aggregate deterioration was $28.20. All 122 entries matched in time, direction, entry price and initial risk. Of 17 changed exits, twelve became initial stops, four became timed exits and one became a target. This is evidence against that particular change on that validation sample, not a universal claim that break-even is optimal.

Across the baseline, 248 initial stops lost $2,508.50; there were 69 targets, 64 gross break-even exits and 39 timed exits. Commissions totaled $840, while fill-price P&L before commissions was still −$194.40. Longs lost $385.50 and shorts $648.90. This is not evidence that fees alone or one direction explains the failure.

Expected full sessions per stock were 247 development, 122 validation and 116 final. Three stock-days had 360 of 361 execution bars and were excluded: AAPL 2025-09-05, MSFT 2025-12-15 and MSFT 2026-07-15. All had zero entries, independently checked in the exports. Eligible no-trade sessions were retained; reference-minute gaps were zero in the actual deep audits.

Actual audit identities were `BATS:AAPL`, `BATS:MSFT`, `BATS:NVDA` and `BATS:QQQ`, with the chart labeled “NASDAQ by Cboe One.” NASDAQ in an export filename does not prove consolidated Nasdaq pricing. Fixed ten-share sizing does not equalize stock risk. Borrow availability/costs, variable spreads, partial fills and real broker execution were not modeled. No independent conservative stock replay or prospective paper test was completed.

Evidence: [historical stock report](History_Test_Report.md), [registered stock plan](History_Test_Plan.md), [development analysis](History_Development_Trade_Analysis.md), [paired validation analysis](History_Validation_Trade_Analysis.md), [final-period analysis](History_Holdout_Trade_Analysis.md), and twelve preserved CSVs in `history_exports`.

### 8.3 Original futures study: outcomes and coverage

The rules in sections 2–4 were tested on thirteen consecutive dated MNQ windows, with matching NQ/ES expiries and actual delayed-feed IDs using `CME_MINI_DL`. Baseline costs remained $1.25 per side, one slippage tick and one limit-verification tick. The registered stress used $2.50 per side, four slippage ticks and two limit-verification ticks. Entry, exit and selection rules were not tuned.

| Frozen segment | Dates | Eligible sessions | Trades per scenario | Baseline net | Stress net |
|---|---|---:|---:|---:|---:|
| Development | 2023-09-01–2025-03-04 | 372 | 33 | −$887.00 | −$1,069.50 |
| Validation | 2025-03-05–2025-12-01 | 186 | 17 | −$618.00 | −$686.00 |
| Final evaluation | 2025-12-02–2026-08-31 | 186 | 16 | −$287.00 | −$348.00 |
| Total | Full registered study | 744 | 66 | **−$1,792.00** | **−$2,103.50** |

The final futures segment is a cross-instrument robustness evaluation, not a fresh untouched market holdout: overlapping regimes had already been inspected in the stock study. Its 16 trades also fail the original 100-trade minimum. Positive validation/final performance, final profit factor at least 1.2 and positive stress performance all fail. Descriptive reconstructed closed-trade drawdowns of $1,824.50 baseline and $2,130.50 stress exceed the $1,200 research threshold. Conservative marked-to-market drawdown, exposure, ambiguous-fill counts, bootstrap confidence gates and prospective paper results remain uncertified.

Baseline net expectancy was −$27.15 per trade, stress −$31.87; both had nine winners (13.64%) and a longest losing streak of fourteen. Worst eligible sessions were −$66 and −$74 respectively. Baseline commissions were $165 and pre-commission fill-price P&L was −$1,627; stress commissions were $330 and pre-commission P&L was −$1,773.50. Fill prices already contain modeled slippage. The extra $311.50 loss in stress comprises $165 additional commissions and $146.50 deterioration in fill-price P&L; it was measured by rerunning the strategy.

Baseline exits were 44 initial stops (−$2,772), eleven gross break-even stops (−$33), five targets (+$887.50) and six noon exits (+$125.50). Seven trades entered and stopped out within the same one-minute timestamp. Longs lost $614 over 23 trades; shorts lost $1,178 over 43. One contract window, H2026, earned $174.50 on only two trades; eleven windows lost money and one had no trades.

All 66 entry timestamps and directions matched between scenarios. Four exit times/categories changed. For example, the 2023-12-15 short entered at 10:35 NY: baseline exited at noon for −$15.50, whereas stress hit its initial stop at 11:31 for −$67. Costs can change the path of a trade through shifted entry/bracket prices; subtracting a fee from an unchanged trade list would miss this.

Actual Deep Backtesting audits confirmed all 744 expected sessions per scenario, complete MNQ execution windows (152 records, 09:29–12:00 inclusive) and complete broad synchronized reference diagnostics (271 records, 07:30–12:00 inclusive). The last eligible entry confirmation uses the minute starting 10:54; the broad reference diagnostic therefore extends beyond the signal requirement. Each scenario had five missing reference minutes elsewhere in the wider morning; these did not invalidate the required windows. All 26 audits reconciled, no endpoint positions remained open, and all 24 preserved trade CSVs matched their downloads byte-for-byte. The zero-trade U2023 window was separately audited in each scenario.

Each native contract run independently began at $50,000. Reported pooled equity and drawdown reconstruct exported trades rather than executing the independent carried-equity portfolio. The specific equity-risk gate would not have bound: minimum reconstructed closed balances were $48,208 baseline and $47,896.50 stress, above the corresponding $25,200/$26,800 entry-admission floors. This resolves that reset concern only; native fill limitations remain.

Evidence: [futures study report](Futures_Test_Report.md), [registered plan](Futures_Test_Plan.md), [complete trade analysis](Futures_Native_Trade_Analysis.md), and `futures_native_exports` containing both metrics files, matched-trade comparisons, transcribed audit notes, source/settings provenance and the 24 CSVs. Audit notes are transcriptions, not verbatim UI strings.

### 8.4 Lessons that apply to the method

1. **A chart indicator is not a trading strategy.** Displayed opens, cycles, SMT and FVG labels do not define executable entries, invalidations, stops, fills or costs. The supplied TWO comment and Monday condition disagree; TSO comments and actual trigger times also disagree. Resolve clocks explicitly instead of assuming the label is authoritative. Exact clock checks also require bars at those timestamps; missing or coarser bars cannot silently become historical levels.
2. **Keep the original model and adaptations separate.** The stock version deliberately changed the benchmark, clock, sizing and stop measurement. Its failure does not by itself test the futures version; the later futures study supplies separate negative evidence. Neither study tests LB-OPEN or every discretionary interpretation of Quarter Theory.
3. **Only information available at the decision time may be used.** Keep observed quarter extremes, completed confirmations, persistent SMT invalidations, boundary event IDs and next-minute fills. Moving drawings and a quarter's eventual extreme are not an historical signal ledger. Fixing a LookBack date ambiguity does not establish a LookBack edge.
4. **“Break-even” is gross, not net.** A stock entry-price stop normally lost $2.10 in the tested baseline. A futures entry-price stop lost $3 baseline and $7 stress. The stock no-break-even comparison worsened validation; no futures no-break-even comparison was run.
5. **The combined rules failed; a replacement component has not been identified.** Initial stops dominate losses, but this does not prove entries alone are defective, stops should be widened or targets shortened. Exported MFE/MAE cover each actual holding period; they cannot establish what happened after the actual exit or prove an alternative rule's P&L.
6. **Costs matter without explaining everything.** Both historical baselines were negative before commissions at their modeled fill prices. Removing commissions in an accounting table is not a separately rerun frictionless strategy. Fixed platform/data costs would still need to be deducted before any all-in profitability claim.
7. **A favorable subset is not validation.** The small MSFT exploratory gain, NVDA final-stock gain and two-trade profitable futures window cannot justify choosing a symbol, quarter, direction or entry time after observing results. The same observed final period cannot be reused as untouched evidence.
8. **Software checks and profit checks answer different questions.** The final recorded verification passed 33 replay/adapter/study tests plus native-trade reconciliation. They support causal behavior, parsing, arithmetic and missing-data handling on their tested cases. They do not establish a market edge, complete LB acceptance coverage or identical native/conservative fills.
9. **Preserve known execution order before assuming ambiguity.** An independent review corrected the replay so a resting target already executable at the minute open fills before a later stop; opening stop gaps and noon flattening also have explicit priority. Where the opening price does not establish order, retain the conservative adverse-feasible convention. Break-even amendments cannot rescue a loss that occurred earlier in the same minute.
10. **More history improves evaluation, not profitability.** A four-session zero-trade run could not assess performance; the longer studies exposed persistent losses. A better subscription or more plotted indicators does not create a demonstrated edge. Sparsity remains material even over three calendar years.

### 8.5 TradingView and data procedures learned

- Audit the actual Deep calculation. Chart markers, ordinary plots and Pine Logs can describe the loaded chart instead of the requested Deep range. The history copies expose terminal counters through intentional audit-only errors; capture them, disable audit mode, and rerun before exporting valid trade results.
- Record source IDs from the actual calculation, not marketing labels or CSV filenames. Use dated, unadjusted same-expiry futures and standard one-minute candles; preserve session settings and timezone conversions. Full holidays, early closes and the 2025-01-09 exceptional closure are explicit calendar exclusions.
- Missing execution data and missing reference data are different. Pre-inventory whole execution sessions; never manufacture zero returns for absent days. Missing mandatory reference context vetoes affected signals; a partial-reference day is not a fully scored zero-return day. No forward filling or removal of only inconvenient losing trades is allowed.
- A trade export proves trade accounting, not raw minute coverage. Chart-data export covers loaded bars. The earlier replay export contained 6,960 MNQ bars but only 59 synchronized NQ/ES tuples; the later native Deep audit did not repair that raw file. Full independent synchronized data acquisition remains incomplete.
- During the study, expired contracts opened by entering the full exchange-qualified dated symbol. Bounded contract date requests worked when an “Entire history” request returned an availability error. Such an error alone did not prove that a more expensive subscription or real-time feed was required.
- Futures settlement-as-close was disabled through the daily-chart setting before returning to one-minute testing. Record this modifier: it affected the available replay history in the session. This is an observed configuration finding, not a claim about future account entitlements.
- Scenario Inputs do not automatically set strategy Properties. Independently verify commission, slippage, limit verification, quantity, capital, margin and calculation timing. The inspected menu offered only zero/one-tick limit verification; the separate stress source declares two ticks directly. Do not change that selector to one while labeling the run as two. Resetting defaults requires rechecking the other inputs and Properties.
- Pair entry/exit export rows and count each trade's P&L and fees once. Preserve source files and hashes, reconcile totals to the report, retain audited zero-trade windows, and distinguish native intratrade drawdown from reconstructed closed-trade drawdown. Do not add drawdowns across independently funded instruments.

These are observations and procedures from the September 17 sessions. Interface options, subscription access and data entitlements may change; verify them at the next run instead of treating the old account state as current.

### 8.6 Files, unresolved requirements and decision

| Artifact | Role and current limitation |
|---|---|
| `Q90_PSP.pine` | Original compiled v1 implementation; frozen and unchanged; original calendar/roll scope is 2026 |
| `Q90_Futures_History.pine` | History companion with date/calendar/roll eligibility and actual-deep audit diagnostics; operative order logic preserved |
| `Q90_Futures_History_Stress.pine` | Same history logic; changed title, three declared costs and default scenario only |
| `Q90_Stocks_History.pine` / `Q90_Stocks_History_NoBE.pine` | Separate stock baseline and rejected registered comparison |
| `q90_replay.py` | Independent conservative Q90 execution core; synthetic checks passed; full historical market study not completed |
| `q90_chart_csv.py` / `q90_study.py` | Synchronized-export adapter and multi-expiry study runner, fixed calendar splits, carried equity, inventory and bootstrap machinery; implementation exists, full real-data study remains unexecuted |
| `analyze_history_trades.py` / `analyze_futures_native.py` | Reconcile exported native trades and produce descriptive results; do not reconstruct missing price paths |
| `futures_data` | Frozen calendars, matched-expiry rolls, official sources and source-change metadata |

Remaining requirements must stay visible: complete synchronized raw-price acquisition; independent baseline/stress replay with carried equity and adverse-order handling; complete marked-to-market/exposure/ambiguity reporting; qualifying unseen statistical evidence; actual cost/borrow assumptions where applicable; and prospective paper observation. Having code for a check does not mean the corresponding market-evidence gate passed. LB-OPEN requires its own implementation and tests before any performance claim.

**Decision: retain these results as rejected research versions and do not deploy them live.** There is no evidence-based rule update that can honestly be labeled profitable. Do not loosen the gates, keep only the profitable subgroup or repeatedly optimize this observed history. If further research is undertaken, register one concrete new hypothesis and its decision criteria before testing genuinely new evaluation data; keep prior failed results in the ledger. Disabling break-even in futures, dropping SMT, adding PC/FVG, changing targets or widening stops remain untested ideas, not approved improvements. No automatic selection of LB-OPEN is justified by Q90's failure.

No broker connection, live order or order-alert integration was created. The user handled sign-in and trial setup directly. Strategy files remain research artifacts; the tested executable sources and original pre-test rulebook are preserved for reproduction.

## 9. Source documents and external references

User sources, preserved without modification:

- Quarter Theory specification: `C:/Users/gilad/.codex/attachments/8c94a7ee-d1ee-41e1-b0e4-e8f27c6eb01d/pasted-text.txt`.
- LookBack specification: `C:/Users/gilad/.codex/attachments/131a7799-66f7-4330-b1c7-325d40bdc6e1/pasted-text.txt`.
- LookBack indicator: `C:/Users/gilad/.codex/attachments/540b5bac-e1c0-418e-9a3a-5aedcf5dc2c3/pasted-text.txt`.
- Cycle indicator: `C:/Users/gilad/.codex/attachments/120a0d28-2834-4702-a32c-828ce7cab291/pasted-text.txt`.
- TDO/TWO/TSO indicator: supplied inline in this conversation.

External references checked on 17 September 2026. They support platform, calendar and contract mechanics, **not the strategy's profitability**:

1. [NYSE trading hours](https://www.nyse.com/trade/trading-information).
2. [CME Micro E-mini Nasdaq-100 contract specification](https://www.cmegroup.com/markets/equities/nasdaq/micro-e-mini-nasdaq-100.html).
3. [CME equity-index futures roll dates](https://www.cmegroup.com/trading/equity-index/rolldates.html).
4. [TradingView Pine strategies: orders, costs and testing limitations](https://www.tradingview.com/pine-script-docs/concepts/strategies/).
5. [TradingView chart-data export](https://www.tradingview.com/support/solutions/43000537255-how-to-export-chart-data/).
6. [TradingView Bar Magnifier and historical coverage](https://www.tradingview.com/support/solutions/43000669285-what-is-bar-magnifier-backtesting-mode/).
