"""Tests for app.core.save_queue (v1.7.17).

Covers:
- Per-upload Lock serializes saves for same upload_id
- Different upload_ids run concurrently
- Global Semaphore caps concurrent bakes
- Stats reporting
- Exception propagation releases lock + slot
"""
from __future__ import annotations

import asyncio
import time

import pytest

from app.core.save_queue import SaveQueue, _default_max_concurrent


pytestmark = pytest.mark.anyio  # all tests in file are async


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def test_default_concurrency_positive():
    n = _default_max_concurrent()
    assert n > 0
    assert n <= 32  # sanity — never absurdly large


async def test_same_upload_id_serializes():
    """Two concurrent saves for the same upload_id must run sequentially."""
    q = SaveQueue(max_concurrent=4)
    in_flight = 0
    max_seen = 0
    lock = asyncio.Lock()

    async def bake():
        nonlocal in_flight, max_seen
        async with lock:
            in_flight += 1
            max_seen = max(max_seen, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        return "ok"

    await asyncio.gather(*[q.run("u1", bake) for _ in range(5)])
    assert max_seen == 1, f"same upload_id should serialize, saw {max_seen} concurrent"


async def test_different_upload_ids_run_concurrently():
    q = SaveQueue(max_concurrent=8)
    in_flight = 0
    max_seen = 0
    lock = asyncio.Lock()

    async def bake():
        nonlocal in_flight, max_seen
        async with lock:
            in_flight += 1
            max_seen = max(max_seen, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        return "ok"

    await asyncio.gather(
        q.run("u1", bake), q.run("u2", bake), q.run("u3", bake),
        q.run("u4", bake), q.run("u5", bake),
    )
    assert max_seen >= 2, "different upload_ids should run in parallel"


async def test_global_semaphore_caps_concurrency():
    """Global semaphore caps total concurrent bakes across all uploads."""
    q = SaveQueue(max_concurrent=2)
    in_flight = 0
    max_seen = 0
    lock = asyncio.Lock()

    async def bake():
        nonlocal in_flight, max_seen
        async with lock:
            in_flight += 1
            max_seen = max(max_seen, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        return "ok"

    # 5 different uploads → would normally all run in parallel; semaphore
    # caps at 2.
    await asyncio.gather(*[q.run(f"u{i}", bake) for i in range(5)])
    assert max_seen <= 2, f"semaphore=2 should cap, saw {max_seen} concurrent"


async def test_exception_releases_lock_and_slot():
    q = SaveQueue(max_concurrent=2)

    async def boom():
        await asyncio.sleep(0.01)
        raise RuntimeError("planned")

    # First call raises
    with pytest.raises(RuntimeError, match="planned"):
        await q.run("u1", boom)

    async def ok():
        return "fine"

    # Second call (same upload_id) must succeed — lock released
    assert await q.run("u1", ok) == "fine"
    # Slots fully available
    assert q.stats["available_slots"] == 2


async def test_returns_bake_result():
    q = SaveQueue(max_concurrent=4)

    async def bake():
        return {"foo": 42}

    res = await q.run("u1", bake)
    assert res == {"foo": 42}


async def test_stats_shape():
    q = SaveQueue(max_concurrent=3)

    async def bake():
        return None

    await q.run("u1", bake)
    s = q.stats
    assert s["max_concurrent"] == 3
    assert s["active_uploads"] >= 1
    assert isinstance(s["available_slots"], int)
