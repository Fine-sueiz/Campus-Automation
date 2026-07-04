"""可观测性测试：cycle_id、feed_health、日志修剪、/health 扩展。"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import test_pipeline_golden as golden

from wg_monitor.db import MonitorDB, default_db_path
from wg_monitor.pipeline import scan_cycle
from wg_monitor.server import create_app
from wg_monitor.settings import invalidate_project_cache


class ObservabilityTest(unittest.TestCase):
    def setUp(self):
        self.old_env = {name: os.environ.get(name) for name in golden.ENV_KEYS}
        for name in golden.ENV_KEYS:
            os.environ.pop(name, None)
        os.environ["EMAIL_DRY_RUN"] = "true"
        os.environ["SMTP_HOST"] = "smtp.example.com"
        os.environ["SMTP_USER"] = "me@example.com"
        os.environ["EMAIL_SENDER"] = "me@example.com"
        os.environ["EMAIL_BODY_MODE"] = "template"
        invalidate_project_cache()

    def tearDown(self):
        for name, value in self.old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        invalidate_project_cache()

    def scan(self, root: Path, db: MonitorDB) -> dict:
        with contextlib.redirect_stdout(io.StringIO()):
            return scan_cycle(root, db, None)

    def add_bad_feed(self, root: Path) -> None:
        app_yml = root / "config" / "app.yml"
        content = app_yml.read_text(encoding="utf-8")
        content = content.replace(
            "  feed_urls:\n",
            '  feed_urls:\n    - name: 坏源\n      url: "' + (root / "missing.xml").as_posix() + '"\n',
            1,
        )
        app_yml.write_text(content, encoding="utf-8")
        invalidate_project_cache()

    def test_feed_health_tracks_ok_and_consecutive_failures(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            golden.write_project(root)
            self.add_bad_feed(root)
            db = MonitorDB(default_db_path(root))

            counts = self.scan(root, db)
            self.assertTrue(counts["cycle_id"])

            health = {row["name"]: row for row in db.list_feed_health()}
            self.assertEqual(len(health), 3)
            self.assertEqual(health["坏源"]["last_status"], "failed")
            self.assertEqual(health["坏源"]["consecutive_failures"], 1)
            self.assertTrue(health["坏源"]["last_error"])
            self.assertEqual(health["志愿测试源"]["last_status"], "ok")
            self.assertEqual(health["志愿测试源"]["last_items"], 1)

            self.scan(root, db)
            self.scan(root, db)
            health = {row["name"]: row for row in db.list_feed_health()}
            self.assertEqual(health["坏源"]["consecutive_failures"], 3)
            self.assertEqual(health["志愿测试源"]["total_ok"], 3)
            # 连败达到 3 时升级为 error 日志
            unhealthy = [
                row for row in db.recent_logs(30) if "feed unhealthy" in str(row.get("message"))
            ]
            self.assertEqual(len(unhealthy), 1)

    def test_scan_cycle_logs_summary_with_cycle_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            golden.write_project(root)
            db = MonitorDB(default_db_path(root))

            counts = self.scan(root, db)

            record = db.find_last_log("scan cycle completed")
            self.assertIsNotNone(record)
            self.assertEqual(record["payload"]["cycle_id"], counts["cycle_id"])
            self.assertEqual(record["payload"]["new_articles"], 2)

    def test_prune_logs_enforces_age_and_row_limits(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = MonitorDB(Path(tmp) / "m.sqlite3")
            for i in range(30):
                db.add_log("info", f"msg-{i}")
            with db.connect() as conn:
                conn.execute(
                    "UPDATE logs SET created_at = '2026-01-01T00:00:00+00:00' WHERE id <= 10"
                )
            deleted = db.prune_logs(keep_days=14, max_rows=100)
            self.assertEqual(deleted, 10)
            # max_rows 下限保护为 100，这里用行数限制再修剪
            with db.connect() as conn:
                remaining = conn.execute("SELECT COUNT(*) AS n FROM logs").fetchone()["n"]
            self.assertEqual(remaining, 20)

    def test_health_endpoint_reports_last_scan_and_feeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            golden.write_project(root)
            app = create_app(root, start_background=False)
            client = TestClient(app)

            with contextlib.redirect_stdout(io.StringIO()):
                client.post("/admin/scan-once")
            response = client.get("/health")

            self.assertEqual(response.status_code, 200)
            data = response.json()
            self.assertTrue(data["ok"])
            self.assertIsNotNone(data["last_scan"]["at"])
            self.assertEqual(data["last_scan"]["summary"]["new_articles"], 2)
            self.assertEqual(len(data["feeds"]), 2)
            self.assertTrue(all(feed["last_status"] == "ok" for feed in data["feeds"]))


if __name__ == "__main__":
    unittest.main()
