"""Sprint 47 — Cron expression parser and next-fire-time calculator.

Supports standard 5-field cron syntax::

    ┌─ minute     (0-59)
    │ ┌─ hour      (0-23)
    │ │ ┌─ day-of-month (1-31)
    │ │ │ ┌─ month       (1-12)
    │ │ │ │ ┌─ day-of-week (0-6, Sunday=0)
    │ │ │ │ │
    * * * * *

Supported syntax per field:
    *        any value
    n        exact value
    n,m,...  list
    n-m      range
    */n      step (from 0 or min)
    n-m/n    range + step
"""

from __future__ import annotations

from datetime import datetime, timezone


def _parse_field(expr: str, lo: int, hi: int) -> set[int]:
    """Return the set of integers that the cron field expression matches."""
    result: set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            step = int(step_s)
        if part == "*":
            r_lo, r_hi = lo, hi
        elif "-" in part:
            a, b = part.split("-", 1)
            r_lo, r_hi = int(a), int(b)
        else:
            v = int(part)
            result.add(v)
            continue
        for v in range(r_lo, r_hi + 1, step):
            result.add(v)
    return result


class CronExpression:
    """Parsed 5-field cron expression.

    Usage::

        expr = CronExpression("0 9 * * 1-5")
        nxt  = expr.next_after(time.time())   # epoch seconds
    """

    __slots__ = ("raw", "_minutes", "_hours", "_doms", "_months", "_dows")

    def __init__(self, expr: str) -> None:
        self.raw = expr
        parts = expr.split()
        if len(parts) != 5:
            raise ValueError(f"Cron expression must have 5 fields, got: {expr!r}")
        m, h, dom, month, dow = parts
        self._minutes = _parse_field(m, 0, 59)
        self._hours   = _parse_field(h, 0, 23)
        self._doms    = _parse_field(dom, 1, 31)
        self._months  = _parse_field(month, 1, 12)
        self._dows    = _parse_field(dow, 0, 6)

    def matches(self, dt: datetime) -> bool:
        """True if *dt* falls on a scheduled tick."""
        return (
            dt.minute  in self._minutes
            and dt.hour   in self._hours
            and dt.day    in self._doms
            and dt.month  in self._months
            and dt.weekday() in {(d - 1) % 7 for d in self._dows}
        )

    def next_after(self, ts: float) -> float:
        """Return the next epoch-second after *ts* that matches this expression.

        Advances minute-by-minute (max 527,040 iterations — 1 year).
        """
        # start at the next whole minute after ts
        t = int(ts) - (int(ts) % 60) + 60
        limit = t + 366 * 24 * 3600  # safety cap
        while t < limit:
            dt = datetime.fromtimestamp(t, tz=timezone.utc)
            if (
                dt.minute  in self._minutes
                and dt.hour   in self._hours
                and dt.day    in self._doms
                and dt.month  in self._months
                and dt.weekday() in {(d - 1) % 7 for d in self._dows}
            ):
                return float(t)
            t += 60
        raise RuntimeError(f"No matching tick found within a year for: {self.raw!r}")
