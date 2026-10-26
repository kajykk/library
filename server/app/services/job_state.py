"""后台任务状态基座（B0-06）。

B1 质量审计、B2 目录扫描/导入、B5 语义索引都需要「单飞、进度、错误」状态。
此处提供通用实现，不依赖 FastAPI/SQLAlchemy，方便单测。
"""

import threading
from datetime import datetime, timezone


class JobState:
    """线程安全的后台任务状态管理器。

    用法：
        job = JobState("quality_audit")
        if job.start(total=100):
            try:
                for i in range(100):
                    job.update(done=i + 1)
                job.finish()
            except Exception as exc:
                job.fail(str(exc))
    """

    def __init__(self, name: str) -> None:
        self._lock = threading.Lock()
        self._name = name
        self._running = False
        self._total = 0
        self._done = 0
        self._error: str | None = None
        self._started_at: str | None = None
        self._finished_at: str | None = None

    def start(self, total: int = 0) -> bool:
        """尝试启动任务。已在运行时返回 False，不覆盖现有进度。"""
        with self._lock:
            if self._running:
                return False
            self._running = True
            self._total = total
            self._done = 0
            self._error = None
            self._started_at = datetime.now(timezone.utc).isoformat()
            self._finished_at = None
            return True

    def update(self, **fields) -> None:
        """合并更新状态字段（done/total 等）。仅在 running 时生效。"""
        with self._lock:
            if not self._running:
                return
            if 'done' in fields:
                self._done = int(fields['done'])
            if 'total' in fields:
                self._total = int(fields['total'])

    def finish(self) -> None:
        """标记任务完成。仅在 running 时生效。"""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._finished_at = datetime.now(timezone.utc).isoformat()

    def fail(self, error: str) -> None:
        """标记任务失败。仅在 running 时生效。"""
        with self._lock:
            if not self._running:
                return
            self._running = False
            self._error = error
            self._finished_at = datetime.now(timezone.utc).isoformat()

    def snapshot(self) -> dict:
        """返回当前状态快照（线程安全）。"""
        with self._lock:
            return {
                'name': self._name,
                'running': self._running,
                'total': self._total,
                'done': self._done,
                'error': self._error,
                'started_at': self._started_at,
                'finished_at': self._finished_at,
            }
