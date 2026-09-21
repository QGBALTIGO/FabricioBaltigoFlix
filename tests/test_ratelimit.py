from unittest.mock import patch

from app.ratelimit import SlidingWindowLimiter


def test_rate_limiter_cleans_old_keys():
    limiter = SlidingWindowLimiter(
        limit=2,
        window_seconds=60,
    )

    with patch(
        "app.ratelimit.time.monotonic",
        return_value=100.0,
    ):
        assert limiter.allow(
            "old-ip"
        )

    with patch(
        "app.ratelimit.time.monotonic",
        return_value=170.0,
    ):
        assert limiter.allow(
            "new-ip"
        )

    assert "old-ip" not in limiter.hits
    assert "new-ip" in limiter.hits
