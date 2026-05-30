from __future__ import annotations

import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Optional

from ..config import settings

JobStatus = Literal["pending", "running", "done", "error", "cancelled"]


@dataclass
class Job:
    id: str
    tool_id: str
    status: JobStatus = "pending"
    progress: float = 0.0
    message: str = ""
    result_path: Optional[Path] = None
    result_filename: Optional[str] = None
    error: Optional[str] = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    meta: dict[str, Any] = field(default_factory=dict)
    cancelled: bool = False
    # Lazily tagged on the first authenticated status poll (the creator's
    # browser polls within ~800ms; the unguessable job_id isn't known to anyone
    # else yet). Used to ACL "存至工作區 by job_id" so a leaked id can't let
    # another user copy someone else's result into their workspace.
    owner_id: Optional[int] = None

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "tool_id": self.tool_id,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "error": self.error,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "has_result": self.result_path is not None and self.result_path.exists(),
            "result_filename": self.result_filename,
            "meta": self.meta or {},
        }


class JobManager:
    def __init__(self, workers: int = 2) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.RLock()
        self._executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")

    def submit(
        self,
        tool_id: str,
        fn: Callable[["Job"], None],
        meta: Optional[dict] = None,
    ) -> Job:
        job = Job(id=uuid.uuid4().hex, tool_id=tool_id, meta=meta or {})
        with self._lock:
            self._jobs[job.id] = job

        def _run() -> None:
            if job.cancelled:
                job.status = "cancelled"
                job.updated_at = time.time()
                return
            job.status = "running"
            job.updated_at = time.time()
            try:
                fn(job)
                if job.cancelled:
                    job.status = "cancelled"
                elif job.status != "error":
                    job.status = "done"
                    job.progress = 1.0
            except Exception as e:  # noqa: BLE001
                if job.cancelled:
                    job.status = "cancelled"
                else:
                    job.status = "error"
                    job.error = str(e)
            finally:
                job.updated_at = time.time()

        self._executor.submit(_run)
        return job

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> bool:
        """標記 job 取消。執行緒會在下一個 checkpoint 中止並丟棄結果。
        回 True 表示成功標記（job 存在且尚未結束）。"""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status in ("done", "error", "cancelled"):
                return False
            job.cancelled = True
            job.status = "cancelled"
            job.message = "已停止"
            job.updated_at = time.time()
            return True

    def cleanup_expired(self) -> int:
        cutoff = time.time() - settings.job_ttl_seconds
        removed = 0
        with self._lock:
            for jid in list(self._jobs.keys()):
                j = self._jobs[jid]
                if j.updated_at < cutoff:
                    if j.result_path and j.result_path.exists():
                        try:
                            j.result_path.unlink()
                        except OSError:
                            pass
                    del self._jobs[jid]
                    removed += 1
        return removed


job_manager = JobManager()
