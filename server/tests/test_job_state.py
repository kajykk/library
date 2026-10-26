"""JobState 单元测试（B0-06）"""

import sys
import pathlib

SERVER_ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVER_ROOT))

from app.services.job_state import JobState


class TestJobState:
    def test_first_start_returns_true(self):
        job = JobState("test")
        assert job.start(total=10) is True

    def test_second_start_returns_false(self):
        job = JobState("test")
        job.start(total=10)
        assert job.start(total=20) is False

    def test_update_merges_fields(self):
        job = JobState("test")
        job.start(total=10)
        job.update(done=5)
        job.update(total=15)
        snap = job.snapshot()
        assert snap["done"] == 5
        assert snap["total"] == 15

    def test_finish_sets_running_false_and_finished_at(self):
        job = JobState("test")
        job.start(total=10)
        job.finish()
        snap = job.snapshot()
        assert snap["running"] is False
        assert snap["finished_at"] is not None

    def test_fail_records_error(self):
        job = JobState("test")
        job.start(total=10)
        job.fail("something broke")
        snap = job.snapshot()
        assert snap["running"] is False
        assert snap["error"] == "something broke"
        assert snap["finished_at"] is not None

    def test_finish_when_not_running_is_noop(self):
        job = JobState("test")
        job.finish()  # 未启动，不应报错
        snap = job.snapshot()
        assert snap["running"] is False
        assert snap["finished_at"] is None

    def test_fail_when_not_running_is_noop(self):
        job = JobState("test")
        job.fail("err")  # 未启动，不应报错
        snap = job.snapshot()
        assert snap["error"] is None

    def test_update_when_not_running_is_noop(self):
        job = JobState("test")
        job.update(done=99)
        snap = job.snapshot()
        assert snap["done"] == 0

    def test_snapshot_independent_of_state(self):
        job = JobState("myjob")
        job.start(total=3)
        snap = job.snapshot()
        assert snap["name"] == "myjob"
        assert snap["running"] is True
        assert snap["started_at"] is not None
        assert snap["error"] is None

    def test_restart_after_finish(self):
        job = JobState("test")
        job.start(total=5)
        job.finish()
        # 完成后可以重新启动
        assert job.start(total=8) is True
        snap = job.snapshot()
        assert snap["running"] is True
        assert snap["total"] == 8
        assert snap["done"] == 0
