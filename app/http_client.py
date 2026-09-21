from __future__ import annotations

import asyncio

import httpx


_client: httpx.AsyncClient | None = None
_lock = asyncio.Lock()


async def get_provider_http_client() -> httpx.AsyncClient:
    global _client

    if (
        _client is not None
        and not _client.is_closed
    ):
        return _client

    async with _lock:
        if (
            _client is None
            or _client.is_closed
        ):
            _client = httpx.AsyncClient(
                timeout=None,
                limits=httpx.Limits(
                    max_connections=200,
                    max_keepalive_connections=80,
                    keepalive_expiry=30.0,
                ),
                follow_redirects=True,
            )

    return _client


async def close_provider_http_client() -> None:
    global _client

    if (
        _client is not None
        and not _client.is_closed
    ):
        await _client.aclose()

    _client = None
