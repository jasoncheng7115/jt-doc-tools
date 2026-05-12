"""Per-upload save queue + global concurrency cap (v1.7.17).

Two layered protections for backends that bake PDFs (or other CPU-bound work)
on every save:

1. **Per-upload Lock** — saves for the SAME `upload_id` run serially. A user
   who fires off 10 quick `/save` calls (e.g., dragging a text box) won't
   queue 10 concurrent bakes; they line up cleanly.

2. **Global Semaphore** — caps total concurrent bakes across ALL uploads.
   Prevents 50 users hitting `/save` at once from saturating CPU. Excess
   saves wait for a slot.

Usage::

    from app.core.save_queue import save_queue

    async def my_bake(model):
        ...  # CPU-bound work
        return result

    @router.post("/save")
    async def save(request):
        body = await request.json()
        upload_id = body["upload_id"]
        return await save_queue.run(upload_id, lambda: my_bake(body))

The bake_fn is async (or sync wrapped in `asyncio.to_thread`); awaited inside
the lock+semaphore.

NB: locks are kept per upload_id forever (small per-upload memory cost).
A cleanup task could prune unused locks, but for typical workloads (single
user editing one PDF for ~minutes then leaving) the leak is negligible.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Awaitable, Callable, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")


def _default_max_concurrent() -> int:
    """Default global concurrency cap. Env override: `JTDT_SAVE_CONCURRENCY`.
    Falls back to min(8, CPU * 2) so small VMs don't get crushed and beefy
    machines can use their cores."""
    env = os.environ.get("JTDT_SAVE_CONCURRENCY")
    if env:
        try:
            v = int(env)
            if v > 0:
                return v
        except ValueError:
            pass
    cpus = os.cpu_count() or 4
    return min(8, cpus * 2)


class SaveQueue:
    """Per-upload serialization + global concurrency cap.

    Thread-safety: built on `asyncio` primitives, intended for single-process
    async server (FastAPI / uvicorn). Multi-worker deployments would need a
    cross-process lock (Redis, file lock, etc) — out of scope here.
    """

    def __init__(self, max_concurrent: int | None = None):
        self._max = max_concurrent if max_concurrent is not None else _default_max_concurrent()
        # Lazily created — both per-upload locks and the global semaphore.
        # Lazy because: SaveQueue() is instantiated at import time, BEFORE
        # the asyncio event loop runs. Creating asyncio.Semaphore at import
        # time binds it to whatever loop happens to be current — usually a
        # different loop than the one serving requests → silent breakage.
        self._sem: asyncio.Semaphore | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_meta: asyncio.Lock | None = None

    def _ensure_sem(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self._max)
        return self._sem

    def _ensure_meta(self) -> asyncio.Lock:
        if self._locks_meta is None:
            self._locks_meta = asyncio.Lock()
        return self._locks_meta

    async def _get_lock(self, upload_id: str) -> asyncio.Lock:
        async with self._ensure_meta():
            lk = self._locks.get(upload_id)
            if lk is None:
                lk = asyncio.Lock()
                self._locks[upload_id] = lk
            return lk

    async def run(self, upload_id: str, bake_fn: Callable[[], Awaitable[T]]) -> T:
        """Run `bake_fn()` under per-upload lock + global semaphore.

        Args:
            upload_id: identifier used for per-upload serialization. Two
                concurrent calls with the same upload_id will run sequentially.
            bake_fn: zero-arg async callable that performs the work and
                returns its result. Caller bakes whatever it wants — we just
                serialize and cap.

        Returns:
            Whatever `bake_fn()` returned. Exceptions propagate (lock released).
        """
        lock = await self._get_lock(upload_id)
        sem = self._ensure_sem()
        # Per-upload first: prevents multiple concurrent bakes per user
        async with lock:
            # Then global: caps total concurrent bakes across all uploads
            async with sem:
                return await bake_fn()

    @property
    def stats(self) -> dict:
        """Snapshot of queue state — for /healthz / metrics. Cheap to call."""
        sem = self._sem
        return {
            "max_concurrent": self._max,
            "active_uploads": len(self._locks),
            # asyncio.Semaphore exposes _value (private but stable enough)
            "available_slots": getattr(sem, "_value", self._max) if sem else self._max,
        }


# Module-level singleton — import this everywhere
save_queue = SaveQueue()
