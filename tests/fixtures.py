"""Synthetic session builder for end-to-end engine tests.

Real market data cannot be committed, and the free vendor path cannot reach the
91-day warmup §5 L1 requires.  These fixtures construct a complete, fully
specified session whose geometry is chosen so that every §5 gate has a known
answer, which lets the engine's decisions be checked against the rulebook by
hand rather than against a black box.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lb_open.core import UTC, Bar, ny_datetime, round_to_tick
from lb_open.data import MinuteStore


def ramp(a: float, b: float, n: int) -> list[float]:
    """n prices moving linearly from a to b (inclusive)."""
    if n == 1:
        return [round_to_tick(b)]
    step = (b - a) / (n - 1)
    return [round_to_tick(a + step * i) for i in range(n)]


def bars_from_path(start_ny: datetime, path: list[float], spread: float = 2.0) -> list[Bar]:
    """Turn a per-minute price path into bars with a symmetric high/low band."""
    out: list[Bar] = []
    for i, p in enumerate(path):
        ts = (start_ny + timedelta(minutes=i)).astimezone(UTC)
        prev = path[i - 1] if i else p
        o, c = round_to_tick(prev), round_to_tick(p)
        out.append(Bar(
            ts=ts, open=o, close=c,
            high=round_to_tick(max(o, c) + spread),
            low=round_to_tick(min(o, c) - spread),
        ))
    return out


def explicit(start_ny: datetime, rows: list[tuple[float, float, float, float]]) -> list[Bar]:
    """Bars with hand-specified OHLC, one per consecutive minute."""
    return [
        Bar(ts=(start_ny + timedelta(minutes=i)).astimezone(UTC),
            open=round_to_tick(o), high=round_to_tick(h),
            low=round_to_tick(l), close=round_to_tick(c))
        for i, (o, h, l, c) in enumerate(rows)
    ]


def flat_session(d: date, o: float, h: float, l: float, c: float) -> list[Bar]:
    """A full regular session [09:30,16:00) whose aggregate OHLC is exactly (o,h,l,c).

    Used for the §5 L1 historical daily candles.  The high and low are placed in
    the first half hour so the §5 L3 '1w' candidate drawn from this session has
    known boundaries.
    """
    n = 390
    path = ramp(o, c, n)
    bars = bars_from_path(ny_datetime(d, time(9, 30)), path, spread=1.0)
    first = bars[0]
    bars[0] = Bar(ts=first.ts, open=round_to_tick(o), high=round_to_tick(h),
                  low=first.low, close=first.close)
    lowest = min(range(n), key=lambda i: bars[i].low)
    b = bars[lowest]
    bars[lowest] = Bar(ts=b.ts, open=b.open, high=b.high, low=round_to_tick(l), close=b.close)
    return bars


def build_long_session(session_date: date = date(2026, 9, 15)) -> tuple[MinuteStore, dict]:
    """A session engineered to produce exactly one qualifying LONG setup.

    Geometry, and the Sec 5 gate each element is aimed at:

      L1  13 prior same-weekday sessions give union prices {19800, 19970,
          20060, 20080}.  P0 = 20000.00, so B_low = 19970 and B_high = 20060.
          [00:00,09:29) reaches 20020 and 19900: B_low touched, B_high not,
          so the daily bias is LONG.
      L2  Asia [18:00,00:00) AH = 19935.  London [00:00,06:00) LH = 20020 > AH
          and LC = 20015 > AH, which is Bullish and agrees with the bias.
      L4  Overnight [18:00,09:29) RH = 20020, RL = 19875, range 145.
      L3  The -3h candle (06:30-07:00) has high 19930, so E = 19930 < P.
          x = (19930-19875)/145 = 0.379 < 0.40 and the location gate passes.
          The -6h and -12h candles are placed entirely below E so their
          boundaries cannot become a nearer target; the -1w candle sits above P
          and is rejected by the E < P rule; the -24h candidate falls on a
          Monday morning outside the generated data and drops for want of it.
      L5  A single spike bar at 07:46 puts an unconsumed swing low at 19900.
          Every later minute stays strictly above it, so it survives to the
          snapshot.  The pre-market rise is monotonic, so no swing high forms
          between E and E+64.25 to undercut the target.
      L6  The nearest unconsumed swing high above E is the London peak at
          20020, 90 points away: (90*2 - 2.50)/63 = 2.82 >= 2.0 in baseline and
          (180 - 5)/67 = 2.61 in stress, so both scenarios pass the gate.
    """
    prev = session_date - timedelta(days=1)
    bars: list[Bar] = []
    notes: dict = {}

    # --- L1: 13 preceding same-weekday regular sessions -------------------
    for weeks in range(13, 0, -1):
        d = session_date - timedelta(days=7 * weeks)
        bars += flat_session(d, o=20060, h=20080, l=19800, c=19970)
    notes["l1_union_prices"] = [19800.0, 19970.0, 20060.0, 20080.0]

    # --- Asia: previous day [18:00,00:00) NY, 360 minutes -----------------
    # 21:30-22:00 is the -12h candidate and is held below E on purpose.
    asia = (ramp(19920, 19930, 195)        # 18:00-21:15
            + ramp(19930, 19886, 15)       # 21:15-21:30 decline into the window
            + ramp(19886, 19888, 30)       # 21:30-22:00  <- the -12h candidate
            + ramp(19888, 19930, 120))     # 22:00-00:00
    asia_bars = bars_from_path(ny_datetime(prev, time(18, 0)), asia, spread=5.0)
    bars += asia_bars
    notes["asia"] = {"high": max(b.high for b in asia_bars),
                     "low": min(b.low for b in asia_bars)}

    # --- London: today [00:00,06:00) NY, 360 minutes ----------------------
    # Peak at 03:00, pull back below E across the -6h window, then recover.
    london = (ramp(19930, 20015, 180)      # 00:00-03:00 rise to the peak
              + ramp(20015, 19914, 30)     # 03:00-03:30 pull back
              + ramp(19914, 19918, 30)     # 03:30-04:00  <- the -6h candidate
              + ramp(19918, 20015, 120))   # 04:00-06:00 recover
    london_bars = bars_from_path(ny_datetime(session_date, time(0, 0)), london, spread=5.0)
    bars += london_bars
    notes["london"] = {"high": max(b.high for b in london_bars),
                       "low": min(b.low for b in london_bars),
                       "close": london_bars[-1].close}

    # --- Pre-market 06:00-09:29 (209 minutes) -----------------------------
    bars += bars_from_path(ny_datetime(session_date, time(6, 0)),
                           ramp(20015, 19928, 30), spread=2.0)      # 06:00-06:30
    # 06:30-07:00 is the -3h candidate: its aggregate high is exactly E.
    lb3h = [(19925, 19927, 19923, 19926) for _ in range(29)]
    lb3h.insert(14, (19926, 19930, 19924, 19928))                   # the 19930 high
    bars += explicit(ny_datetime(session_date, time(6, 30)), lb3h[:30])
    bars += bars_from_path(ny_datetime(session_date, time(7, 0)),
                           ramp(19926, 19912, 46), spread=2.0)      # 07:00-07:46
    # A single spike bar strictly below both neighbours: the Sec 5 L5 swing low.
    bars += explicit(ny_datetime(session_date, time(7, 46)),
                     [(19912, 19913, 19900, 19910)])
    # Monotonic rise to P0; no local maximum forms, so no nearer swing target.
    bars += bars_from_path(ny_datetime(session_date, time(7, 47)),
                           ramp(19911, 19997, 102), spread=1.0)     # 07:47-09:29

    # --- 09:29 snapshot minute -------------------------------------------
    bars += explicit(ny_datetime(session_date, time(9, 29)),
                     [(19997, 20002, 19996, 20000)])

    # --- Window 1 ---------------------------------------------------------
    bars += explicit(ny_datetime(session_date, time(9, 30)), [
        (20000, 20001, 19952, 19955),      # 09:30 no sweep of 19900
        (19955, 19958, 19890, 19945),      # 09:31 sweep + reclaim -> confirmation
        (19945, 19947, 19929.5, 19935),    # 09:32 two ticks through 19930 -> fill
    ])
    # 09:33 onward: rise to the target and hold flat to the noon flatten.
    bars += bars_from_path(ny_datetime(session_date, time(9, 33)),
                           ramp(19935, 20030, 60) + [20030.0] * 148, spread=2.0)

    notes["expected"] = {"bias": "long", "entry": 19930.0, "stop": 19900.0,
                         "swing_low": 19900.0, "target": 20020.0}
    return MinuteStore(bars), notes


if __name__ == "__main__":
    store, notes = build_long_session()
    print(f"bars: {len(store)}  {store.first_ts} -> {store.last_ts}")
    for k, v in notes.items():
        print(f"  {k}: {v}")
