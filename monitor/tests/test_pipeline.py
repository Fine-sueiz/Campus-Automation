"""统一流水线的新行为测试（黄金用例之外的增量语义）。"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import test_pipeline_golden as golden

from wg_monitor.config import load_project
from wg_monitor.db import MonitorDB, default_db_path
from wg_monitor.monitor import run_once
from wg_monitor.pipeline import scan_cycle
from wg_monitor.settings import invalidate_project_cache


class PipelineUnificationTest(unittest.TestCase):
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

    def test_cli_and_server_share_sqlite_dedup(self):
        """CLI run_once 处理过的文章，服务端 scan_cycle 不再重复处理（state.json 退役）。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            golden.write_project(root)
            paths, app_config, schedule_config = load_project(root)

            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(run_once(paths, app_config, schedule_config), 0)

            self.assertFalse((root / "data" / "state.json").exists())
            db = MonitorDB(default_db_path(root))
            with db.connect() as conn:
                article_count = conn.execute("SELECT COUNT(*) AS n FROM articles").fetchone()["n"]
            self.assertEqual(article_count, 2)

            counts = scan_cycle(root, db, None)
            self.assertEqual(counts["items"], 2)
            self.assertEqual(counts["new_articles"], 0)
            self.assertEqual(counts["opportunities"], 0)

    def test_single_item_failure_does_not_abort_cycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            golden.write_project(root)
            db = MonitorDB(default_db_path(root))

            with mock.patch(
                "wg_monitor.pipeline.analyse_opportunity",
                side_effect=RuntimeError("boom"),
            ):
                counts = scan_cycle(root, db, None)

            self.assertEqual(counts["items"], 2)
            self.assertEqual(counts["new_articles"], 2)
            self.assertEqual(counts["item_failed"], 2)
            self.assertEqual(counts["opportunities"], 0)
            logs = [row for row in db.recent_logs(10) if "process item failed" in str(row.get("message"))]
            self.assertEqual(len(logs), 2)


if __name__ == "__main__":
    unittest.main()
