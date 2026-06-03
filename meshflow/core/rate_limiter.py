"""Per-agent rate limiting with sliding-window RPM/TPM counters.

Provides a lightweight ``RateLimiter`` that throttles LLM calls to stay
within requests-per-minute (RPM) and tokens-per-minute (TPM) budgets.

Usage::

    from meshflow.core.rate_limiter import RateLimiter

    limiter = RateLimiter(rpm=60, tpm=100_000)

    # In an async agent step:
    await limiter.acquire(tokens=500)  # blocks if limits are exceeded
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class RateLimiter:
    """Sliding-window rate limiter for LLM call throttling.

    Parameters
    ----------
    rpm:
        Maximum requests per minute. ``None`` = unlimited.
    tpm:
        Maximum tokens per minute. ``None`` = unlimited.
    window_s:
        Sliding window duration in seconds (default 60).
    """

    rpm: int | None = None
    tpm: int | None = None
    window_s: float = 60.0

    # Internal tracking — not user-configurable
    _request_timestamps: deque[float] = field(default_factory=deque, repr=False)
    _token_log: deque[tuple[float, int]] = field(default_factory=deque, repr=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    async def acquire(self, tokens: int = 0) -> None:
        """Wait until the call is within rate limits, then record it.

        Parameters
        ----------
        tokens:
            Number of tokens this call will consume. Used for TPM tracking.
            Pass 0 if unknown (only RPM will be enforced).

        This method is designed to be called *before* each LLM call.
        If the limits are exceeded, it sleeps until the oldest entry in
        the sliding window expires.
        """
        async with self._lock:
            while True:
                now = time.monotonic()
                self._prune(now)

                # Check RPM
                if self.rpm is not None and len(self._request_timestamps) >= self.rpm:
                    oldest = self._request_timestamps[0]
                    sleep_for = self.window_s - (now - oldest)
                    if sleep_for > 0:
                        await asyncio.sleep(sleep_for)
                        continue

                # Check TPM
                if self.tpm is not None and tokens > 0:
                    current_tokens = sum(t for _, t in self._token_log)
                    if current_tokens + tokens > self.tpm:
                        oldest_ts = self._token_log[0][0] if self._token_log else now
                        sleep_for = self.window_s - (now - oldest_ts)
                        if sleep_for > 0:
                            await asyncio.sleep(sleep_for)
                            continue

                # Record this call
                self._request_timestamps.append(now)
                if tokens > 0:
                    self._token_log.append((now, tokens))
                break

    def _prune(self, now: float) -> None:
        """Remove entries older than the sliding window."""
        cutoff = now - self.window_s
        while self._request_timestamps and self._request_timestamps[0] < cutoff:
            self._request_timestamps.popleft()
        while self._token_log and self._token_log[0][0] < cutoff:
            self._token_log.popleft()

    @property
    def requests_in_window(self) -> int:
        """Number of requests currently in the sliding window."""
        self._prune(time.monotonic())
        return len(self._request_timestamps)

    @property
    def tokens_in_window(self) -> int:
        """Number of tokens consumed in the current sliding window."""
        self._prune(time.monotonic())
        return sum(t for _, t in self._token_log)
