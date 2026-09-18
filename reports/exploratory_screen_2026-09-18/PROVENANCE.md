# Exploratory screen, 2026-09-18

Preserved because §6 requires the audit record of every eligible session and
every candidate rejection to be kept, not only successful trades.

**This run produced no trades and no scored sessions. It is not evidence about
LB-OPEN's profitability, and §2 forbids treating it as any.**

| Field | Value |
|---|---|
| Source | Yahoo Finance `NQ=F`, continuous front-month |
| Contract identity | **continuous, not dated/unadjusted** — disqualifying under §2 |
| Range requested | 2026-08-21 → 2026-09-17 |
| Minute bars | 25,662 |
| Eligible sessions | 19 |
| Scored sessions | **0** |
| Data-quality exclusions | **19** |
| Trades | 0 |
| Scenarios | baseline and stress, both run |

Every session was excluded on the same cause: the 929-minute pre-snapshot
context window `[prev 18:00, 09:29)` was missing a contiguous ~10-minute block
around midnight, a vendor artifact rather than a market halt. Execution coverage
over `[09:29, 12:00]` was 1.000 on all 19 sessions.

Every §7 gate reads INCONCLUSIVE. §7: *"A lack of adequate history is
inconclusive, not a pass."*

Regenerate with:

```bash
python3 data/fetch.py --weeks 4 --out data/nq_minutes.csv
python3 -m lb_open.cli --csv data/nq_minutes.csv \
    --start 2026-08-21 --end 2026-09-17 \
    --source "Yahoo Finance NQ=F continuous front-month (exploratory)"
```

The vendor serves a rolling ~30-day window, so a later run covers later dates
and will not reproduce these exact rows. The preserved JSON here is the record.
