"""Minute-bar storage with the availability and completeness rules of Sec 2.

Sec 2: "A decision at time `t` can access only observations with availability
time at or before `t`."  Every accessor here takes an explicit `as_of` and
refuses to return a bar whose close was not yet published.
"""

from __future__ import annotations

import csv
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional, Sequence

from .core import NY, UTC, Bar, OHLC, aggregate, to_ny


class MissingData(Exception):
    """Raised when mandatory context is absent.

    Sec 2: "If required data are unavailable, log the reason and skip that
    signal/window."  Sec 2: "Missing candidates can be removed; missing
    mandatory context vetoes the setup."
    """


@dataclass(frozen=True, slots=True)
class Coverage:
    expected: int
    present: int

    @property
    def complete(self) -> bool:
        return self.expected > 0 and self.present == self.expected

    @property
    def ratio(self) -> float:
        return self.present / self.expected if self.expected else 0.0


class MinuteStore:
    """An immutable, time-indexed collection of completed minute bars."""

    __slots__ = ("_bars", "_starts")

    def __init__(self, bars: Iterable[Bar]):
        ordered = sorted(bars, key=lambda b: b.ts)
        deduped: list[Bar] = []
        for b in ordered:
            if deduped and deduped[-1].ts == b.ts:
                continue  # Sec 2: one record per completed interval.
            deduped.append(b)
        self._bars: tuple[Bar, ...] = tuple(deduped)
        self._starts: tuple[datetime, ...] = tuple(b.ts for b in self._bars)

    def __len__(self) -> int:
        return len(self._bars)

    def __iter__(self):
        return iter(self._bars)

    @property
    def bars(self) -> tuple[Bar, ...]:
        return self._bars

    @property
    def first_ts(self) -> Optional[datetime]:
        return self._starts[0] if self._bars else None

    @property
    def last_ts(self) -> Optional[datetime]:
        return self._starts[-1] if self._bars else None

    def range(self, start: datetime, end: datetime) -> tuple[Bar, ...]:
        """Bars whose START lies in the half-open interval [start, end).

        Sec 2: "All intervals are half-open [start, end).  A tick at a boundary
        belongs to the new interval."
        """
        lo = bisect_left(self._starts, start)
        hi = bisect_left(self._starts, end)
        return self._bars[lo:hi]

    def available(self, start: datetime, end: datetime, as_of: datetime) -> tuple[Bar, ...]:
        """Bars in [start, end) whose close was published at or before `as_of`.

        Sec 2: "A decision at time `t` can access only observations with
        availability time at or before `t`."
        """
        window = self.range(start, end)
        return tuple(b for b in window if b.available_at <= as_of)

    def last_close_at(self, as_of: datetime) -> Optional[Bar]:
        """The last completed minute whose close is available at `as_of`.

        Sec 5 L3: "Let `P` be the last completed MNQ minute close at `S`."
        """
        idx = bisect_right(self._starts, as_of - timedelta(minutes=1))
        if idx == 0:
            return None
        bar = self._bars[idx - 1]
        return bar if bar.available_at <= as_of else None

    def bar_starting(self, ts: datetime) -> Optional[Bar]:
        idx = bisect_left(self._starts, ts)
        if idx < len(self._bars) and self._starts[idx] == ts:
            return self._bars[idx]
        return None

    # ---- completeness -------------------------------------------------

    def coverage(self, start: datetime, end: datetime) -> Coverage:
        """Minute coverage over the half-open interval [start, end)."""
        expected = int((end - start).total_seconds() // 60)
        return Coverage(expected=expected, present=len(self.range(start, end)))

    def require_complete(self, start: datetime, end: datetime, what: str) -> tuple[Bar, ...]:
        """Return the interval's bars, or raise if any minute is absent.

        Sec 2: "Require complete minute records for ... the entire completed
        reference quarter, and each selected LB historical interval."  Sec 2:
        "Never forward-fill a missing asset."
        """
        cov = self.coverage(start, end)
        if not cov.complete:
            raise MissingData(
                f"{what}: {cov.present}/{cov.expected} minutes in "
                f"[{to_ny(start):%Y-%m-%d %H:%M}, {to_ny(end):%H:%M}) NY"
            )
        return self.range(start, end)

    def aggregate_complete(self, start: datetime, end: datetime, what: str) -> OHLC:
        """Aggregate an interval that must be fully present (Sec 2)."""
        return aggregate(self.require_complete(start, end, what))

    def aggregate_if_complete(self, start: datetime, end: datetime) -> Optional[OHLC]:
        """Aggregate, or None when incomplete.

        Sec 5 L3: "If a local start is ambiguous/nonexistent during a clock
        change, or the required trading interval is unavailable, remove that
        candidate."  Candidate removal, not a veto.
        """
        cov = self.coverage(start, end)
        if not cov.complete:
            return None
        return aggregate(self.range(start, end))


def load_csv(path: str, tz: str = "UTC", ts_field: str = "time") -> MinuteStore:
    """Load minute bars from a CSV.

    Accepts TradingView chart exports (ISO-8601 `time` column) and generic
    epoch-second files.  Sec 8.5: "Record source IDs from the actual
    calculation, not marketing labels or CSV filenames."  The caller is
    responsible for provenance; this function only parses.
    """
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(tz)
    bars: list[Bar] = []
    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        fields = {f.lower().strip(): f for f in (reader.fieldnames or [])}

        def pick(*names: str) -> str:
            for n in names:
                if n in fields:
                    return fields[n]
            raise KeyError(f"none of {names} in {reader.fieldnames}")

        f_ts = fields.get(ts_field.lower()) or pick("time", "datetime", "date", "timestamp")
        f_o, f_h = pick("open", "o"), pick("high", "h")
        f_l, f_c = pick("low", "l"), pick("close", "c")
        f_v = fields.get("volume") or fields.get("v")

        for row in reader:
            raw = (row[f_ts] or "").strip()
            if not raw:
                continue
            if raw.isdigit() or (raw.startswith("-") and raw[1:].isdigit()):
                ts = datetime.fromtimestamp(int(raw), tz=UTC)
            else:
                parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
                ts = parsed if parsed.tzinfo else parsed.replace(tzinfo=zone)
            try:
                bars.append(
                    Bar(
                        ts=ts.astimezone(UTC),
                        open=float(row[f_o]),
                        high=float(row[f_h]),
                        low=float(row[f_l]),
                        close=float(row[f_c]),
                        volume=float(row[f_v]) if f_v and row.get(f_v) else 0.0,
                    )
                )
            except (TypeError, ValueError):
                continue  # blank/NaN rows in exports are absent minutes, not zeros.
    return MinuteStore(bars)
