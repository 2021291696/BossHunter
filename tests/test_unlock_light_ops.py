"""轻操作与后台任务解耦（unlock-light-ops）的行为回归。

后台任务（如 AI 评分）运行期间：
- 岗位状态操作（移入回收站/恢复/标记已发/永久删除）不再被 active task 拦截；
- 评分循环逐岗重查软删状态，启动后才被删除的岗位不再进入评分处理。

浏览器类操作（开聊天页/准备回复）与 Agent API 的任务互斥保持不变。
"""

import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bosshunter.ai.scorer import score_jobs
from bosshunter.db import get_db, insert_job, soft_delete_jobs
from bosshunter.web import server


def _job(job_id: str) -> dict:
    return {
        "id": job_id,
        "title": "Engineer",
        "company": "Example",
        "salary": "10-20K",
        "city": "北京",
        "experience": "1-3 years",
        "jd": "Build product features",
        "hr_name": "HR",
        "hr_title": "Recruiter",
        "hr_active": "active",
        "company_size": "100-499",
        "company_industry": "Software",
        "url": f"https://example.com/jobs/{job_id}",
    }


class UnlockLightOpsApiTests(unittest.TestCase):
    def setUp(self):
        self.original_base_dir = server.BASE_DIR
        self._tmp = tempfile.TemporaryDirectory()
        server.set_base_dir(Path(self._tmp.name))

    def tearDown(self):
        server.set_base_dir(self.original_base_dir)
        self._tmp.cleanup()

    def _request(self, path: str, method: str = "POST", json_body: dict | None = None):
        path_info, _, query_string = path.partition("?")
        status_headers = {}

        def start_response(status, headers, exc_info=None):
            status_headers["status"] = status
            status_headers["headers"] = dict(headers)

        request_body = json.dumps(json_body).encode("utf-8") if json_body is not None else b""
        environ = {
            "REMOTE_ADDR": "127.0.0.1",
            "REQUEST_METHOD": method,
            "PATH_INFO": path_info,
            "QUERY_STRING": query_string,
            "SERVER_NAME": "127.0.0.1",
            "SERVER_PORT": "8686",
            "wsgi.version": (1, 0),
            "wsgi.url_scheme": "http",
            "wsgi.input": io.BytesIO(request_body),
            "wsgi.errors": io.StringIO(),
            "wsgi.multithread": False,
            "wsgi.multiprocess": False,
            "wsgi.run_once": False,
        }
        if json_body is not None:
            environ["CONTENT_LENGTH"] = str(len(request_body))
            environ["CONTENT_TYPE"] = "application/json"
        response_iter = server.app(environ, start_response)
        try:
            body = b"".join(
                chunk if isinstance(chunk, bytes) else chunk.encode("utf-8")
                for chunk in response_iter
            )
        finally:
            if hasattr(response_iter, "close"):
                response_iter.close()
        return status_headers["status"], status_headers["headers"], body.decode("utf-8")

    def test_job_status_actions_allowed_while_task_active(self):
        """评分任务运行中，四个岗位状态接口不得再以 active_task_conflict 拒绝。"""
        active = {"id": "task-1", "mode": "score", "label": "单独 AI 评分"}
        cases = [
            ("/api/jobs/soft-delete", {"job_ids": ["ghost"], "confirmed": True}),
            ("/api/jobs/restore", {"job_ids": ["ghost"], "confirmed": True}),
            ("/api/jobs/manual-sent", {"job_ids": ["ghost"], "confirmed": True}),
            ("/api/jobs/permanent-delete", {
                "job_ids": ["ghost"], "confirmed": True, "confirmation": "DELETE",
            }),
        ]
        with patch.object(server.task_runner, "status", return_value={"active": active}):
            for path, payload in cases:
                with self.subTest(path=path):
                    _, _, body_text = self._request(path, "POST", payload)
                    result = json.loads(body_text)
                    self.assertNotEqual(
                        result.get("code"), "active_task_conflict", body_text,
                    )

    def test_browser_actions_still_blocked_while_task_active(self):
        """碰浏览器的操作（开聊天页）保持任务互斥。"""
        active = {"id": "task-1", "mode": "deliver", "label": "确认投递"}
        with patch.object(server.task_runner, "status", return_value={"active": active}):
            _, _, body_text = self._request("/api/history/1/open-chat", "POST", {})
        self.assertIn("active_task_conflict", body_text)


class ScoreSkipSoftDeletedTests(unittest.TestCase):
    def test_score_jobs_skips_job_deleted_after_launch(self):
        """评分启动后才被移入回收站的岗位：不写分、不消耗 AI 评分调用。"""
        db_path = Path(tempfile.mkdtemp()) / "score.db"
        db = get_db(db_path)
        try:
            insert_job(db, _job("deleted-after-launch"))
            insert_job(db, _job("alive"))
            soft_delete_jobs(db, ["deleted-after-launch"], confirmed=True, reason="test")
        finally:
            db.close()

        # 模拟评分启动时拉到的岗位快照：含一个启动后才被软删的岗位。
        # quick_score 打为预筛淘汰，测试聚焦"已删跳过"，不依赖 AI 链路。
        snapshot = [_job("deleted-after-launch"), _job("alive")]
        with \
                patch("bosshunter.ai.scorer.get_db", side_effect=lambda: get_db(db_path)), \
                patch("bosshunter.ai.scorer._load_resume", return_value="resume"), \
                patch("bosshunter.ai.scorer.select_scoring_jobs", return_value=snapshot), \
                patch("bosshunter.ai.scorer.quick_score", return_value=(0, "mock-prefilter")), \
                patch("bosshunter.ai.scorer._request_score") as request_score:
            score_jobs(
                {"scoring": {"threshold": 60}},
                scope="selected",
                job_ids=["deleted-after-launch", "alive"],
            )

        db = get_db(db_path)
        try:
            deleted_row = db.execute(
                "SELECT status, score FROM jobs WHERE id = 'deleted-after-launch'"
            ).fetchone()
            alive_status = db.execute(
                "SELECT status FROM jobs WHERE id = 'alive'"
            ).fetchone()["status"]
        finally:
            db.close()
        self.assertEqual(deleted_row["status"], "pending", "已删岗位状态不应被评分流程改动")
        self.assertEqual(alive_status, "filtered", "活岗位应正常走预筛淘汰路径")
        request_score.assert_not_called()


if __name__ == "__main__":
    unittest.main()
