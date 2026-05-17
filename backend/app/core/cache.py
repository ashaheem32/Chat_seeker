"""
Async Redis cache helper.

Thin wrapper around `redis.asyncio` that:
    - Creates a single shared client per process (lazy on first call).
    - Exposes JSON get/set with a TTL — the only access pattern we use today.
    - Fails open. If Redis is unreachable, the helper logs once and returns
      None / no-ops. Callers can therefore wrap reads in `cached or compute()`
      and never crash because the cache is down.

Why fail-open:
    Stats endpoints can run uncached for a 50k-message chat in <2s. Losing
    Redis is a degradation, not an outage. We don't want a redis.ConnectionError
    bubbling up to a 500.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


class _Cache:
    """Internal helper. Use the module-level `cache` singleton."""

    def __init__(self) -> None:
        self._client: Any = None
        # We don't want to spam the logs every time Redis is down — flip
        # this true after the first failure and only log resumes.
        self._unavailable: bool = False

    async def _get_client(self) -> Any | None:
        if self._unavailable:
            return None
        if self._client is not None:
            return self._client
        try:
            from redis.asyncio import from_url

            self._client = from_url(
                settings.REDIS_URL,
                encoding="utf-8",
                decode_responses=True,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            # Cheap ping so we surface unreachable Redis on first use rather
            # than mid-request.
            await self._client.ping()
        except Exception as e:  # pragma: no cover — env-dependent
            logger.warning("Redis unavailable (%s); cache disabled", e)
            self._client = None
            self._unavailable = True
            return None
        return self._client

    # ---- Public surface --------------------------------------------------
    async def get_json(self, key: str) -> Any | None:
        """Fetch a JSON-encoded value. Returns None for cache miss / down."""
        client = await self._get_client()
        if client is None:
            return None
        try:
            raw = await client.get(key)
        except Exception as e:
            logger.warning("Redis GET %s failed: %s", key, e)
            return None
        if raw is None:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Stale / corrupt entry — drop it and treat as miss.
            await self.delete(key)
            return None

    async def set_json(
        self, key: str, value: Any, ttl_seconds: int | None = None
    ) -> None:
        """Store a JSON-encoded value with an optional TTL. No-op on Redis-down."""
        client = await self._get_client()
        if client is None:
            return
        try:
            payload = json.dumps(value, default=str)
            if ttl_seconds:
                await client.set(key, payload, ex=ttl_seconds)
            else:
                await client.set(key, payload)
        except Exception as e:
            logger.warning("Redis SET %s failed: %s", key, e)

    async def delete(self, key: str) -> None:
        client = await self._get_client()
        if client is None:
            return
        try:
            await client.delete(key)
        except Exception as e:
            logger.warning("Redis DEL %s failed: %s", key, e)

    async def delete_pattern(self, pattern: str) -> None:
        """Best-effort DEL for keys matching `pattern`. Used for invalidation
        when an upload is reanalyzed (e.g. `stats:overview:<uid>:*`)."""
        client = await self._get_client()
        if client is None:
            return
        try:
            keys = []
            async for k in client.scan_iter(match=pattern, count=200):
                keys.append(k)
            if keys:
                await client.delete(*keys)
        except Exception as e:
            logger.warning("Redis SCAN/DEL %s failed: %s", pattern, e)

    async def mark_stage_complete(self, upload_id: str, stage: str) -> int:
        """Coordination primitive for the parallel NLP / embedding stages.

        Records that `stage` finished for `upload_id` and returns the number
        of distinct stages now complete. Callers compare against the
        expected total (2 for nlp+embedding) to decide when to flip
        ChatUpload.status to `done`.

        Fail-open: returns -1 if Redis is unreachable, in which case the
        caller should treat as "no coordination available" and proceed to
        mark done — losing coordination is preferable to leaving an upload
        stuck in an intermediate state forever.
        """
        client = await self._get_client()
        if client is None:
            return -1
        key = f"upload:{upload_id}:done_stages"
        try:
            await client.sadd(key, stage)
            await client.expire(key, 24 * 3600)
            return int(await client.scard(key))
        except Exception as e:
            logger.warning("Redis SADD/SCARD %s failed: %s", key, e)
            return -1

    async def close(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            except Exception:
                pass
            self._client = None


# Module-level singleton. FastAPI's lifespan can call cache.close() on shutdown.
cache = _Cache()
