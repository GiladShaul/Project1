"""Data acquisition.

Two paths, with very different evidentiary status:

1. `fetch_yahoo_minutes` - free continuous front-month data (NQ=F), capped by the
   vendor at ~30 calendar days of one-minute bars.  Sec 2 permits this ONLY as a
   labelled screen: "Do not splice price levels across contract expiries or use
   retrospectively back-adjusted continuous prices for absolute historical
   levels. ... A continuous-chart run may be an exploratory screen only, labeled
   as such."  LB-OPEN's L1 brackets ARE absolute historical levels drawn from up
   to 13 weeks back, so a continuous series is materially wrong for them.  Every
   artifact produced from this path carries EXPLORATORY_LABEL.

2. `load_tradingview_export` - dated, unadjusted contract exports produced by the
   user from TradingView's chart-data export.  This is the only path that can
   produce acceptance evidence under Sec 7.
"""

from __future__ import annotations

import json
import sys
import time as _time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterable, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from lb_open.core import UTC, Bar
from lb_open.data import MinuteStore, load_csv

EXPLORATORY_LABEL = (
    "EXPLORATORY SCREEN ONLY - continuous front-month series, not dated unadjusted "
    "contracts. Sec 2 forbids using continuous prices for absolute historical levels, "
    "which is exactly what LB-OPEN's L1 brackets are. No acceptance evidence."
)

_YAHOO = "https://query2.finance.yahoo.com/v8/finance/chart/{sym}?interval=1m&period1={p1}&period2={p2}"
_UA = {"User-Agent": "Mozilla/5.0"}


def fetch_yahoo_minutes(symbol: str = "NQ=F", weeks: int = 4,
                        end: Optional[date] = None) -> tuple[MinuteStore, dict]:
    """Chain weekly requests backwards.  The vendor rejects windows older than ~30d."""
    end_dt = datetime.combine(end or date.today(), datetime.min.time(), tzinfo=UTC)
    bars: list[Bar] = []
    meta: dict = {"symbol": symbol, "windows": [], "label": EXPLORATORY_LABEL}
    sym = urllib.parse.quote(symbol, safe="")

    for w in range(weeks):
        p2 = int((end_dt - timedelta(days=7 * w)).timestamp())
        p1 = p2 - 7 * 86400
        url = _YAHOO.format(sym=sym, p1=p1, p2=p2)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=_UA), timeout=30) as fh:
                payload = json.load(fh)
        except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError) as e:
            meta["windows"].append({"weeks_back": w, "error": str(e)})
            continue

        chart = payload.get("chart") or {}
        if chart.get("error"):
            meta["windows"].append({"weeks_back": w, "error": chart["error"].get("code")})
            continue
        result = (chart.get("result") or [None])[0]
        if not result:
            continue
        stamps = result.get("timestamp") or []
        q = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        o, h, l, c = q.get("open", []), q.get("high", []), q.get("low", []), q.get("close", [])
        v = q.get("volume", [])
        added = 0
        for i, ts in enumerate(stamps):
            try:
                oo, hh, ll, cc = o[i], h[i], l[i], c[i]
            except IndexError:
                continue
            # Sec 2: "An exchange-closed interval is unavailable, not a flat candle."
            # A null OHLC row is an ABSENT minute; never synthesise a flat bar.
            if None in (oo, hh, ll, cc):
                continue
            bars.append(Bar(
                ts=datetime.fromtimestamp(int(ts), tz=UTC),
                open=float(oo), high=float(hh), low=float(ll), close=float(cc),
                volume=float(v[i]) if i < len(v) and v[i] is not None else 0.0,
            ))
            added += 1
        meta["windows"].append({"weeks_back": w, "bars": added})
        _time.sleep(1.0)

    store = MinuteStore(bars)
    meta["total_bars"] = len(store)
    if len(store):
        meta["first"] = store.first_ts.isoformat()
        meta["last"] = store.last_ts.isoformat()
    return store, meta


def load_tradingview_export(path: str | Path, tz: str = "America/New_York") -> MinuteStore:
    """Load a TradingView chart-data export.

    Sec 7: "Export synchronized dates and contracts for all required symbols;
    separate screenshots are insufficient."
    Sec 8.5: "Record source IDs from the actual calculation, not marketing labels
    or CSV filenames."  Provenance is the operator's responsibility; this only parses.

    TradingView exports a `time` column.  Recent builds emit ISO-8601 with an
    offset (unambiguous); older builds emit exchange-local wall clock with no
    offset, which `tz` resolves.
    """
    return load_csv(str(path), tz=tz)


def write_csv(store: MinuteStore, path: str | Path) -> None:
    import csv

    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["time", "open", "high", "low", "close", "volume"])
        for b in store:
            w.writerow([b.ts.isoformat(), b.open, b.high, b.low, b.close, b.volume])


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Fetch exploratory minute data")
    ap.add_argument("--symbol", default="NQ=F")
    ap.add_argument("--weeks", type=int, default=4)
    ap.add_argument("--out", default="data/nq_minutes.csv")
    a = ap.parse_args()

    store, meta = fetch_yahoo_minutes(a.symbol, a.weeks)
    write_csv(store, a.out)
    print(json.dumps(meta, indent=2))
    print(f"\n!! {EXPLORATORY_LABEL}")
