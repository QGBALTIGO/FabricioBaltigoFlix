from __future__ import annotations

import time
from collections import (
    defaultdict,
    deque,
)


class SlidingWindowLimiter:
    def __init__(
        self,
        limit: int,
        window_seconds: int = 60,
    ):
        self.limit = max(
            1,
            limit,
        )
        self.window = max(
            1,
            window_seconds,
        )
        self.hits: dict[
            str,
            deque[float],
        ] = defaultdict(deque)
        self._last_cleanup = (
            time.monotonic()
        )

    def _cleanup(
        self,
        now: float,
    ) -> None:
        if (
            now - self._last_cleanup
            < self.window
        ):
            return

        cutoff = (
            now - self.window
        )

        for key in list(
            self.hits.keys()
        ):
            q = self.hits.get(
                key
            )
            if not q:
                self.hits.pop(
                    key,
                    None,
                )
                continue

            while (
                q
                and q[0] < cutoff
            ):
                q.popleft()

            if not q:
                self.hits.pop(
                    key,
                    None,
                )

        self._last_cleanup = now

    def allow(
        self,
        key: str,
    ) -> bool:
        now = time.monotonic()
        self._cleanup(now)

        q = self.hits[key]

        while (
            q
            and now - q[0]
            > self.window
        ):
            q.popleft()

        if len(q) >= self.limit:
            return False

        q.append(now)
        return True
