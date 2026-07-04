"""GET /admin/opportunities 只读查询接口测试：过滤、字段白名单、参数校验。"""

from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi.testclient import TestClient

import test_pipeline_golden as golden

from wg_monitor.db import MonitorDB, default_db_path
from wg_monitor.server import create_app, normalize_since
from wg_monitor.settings import invalidate_project_cache


def iso_utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def opportunity_payload(opp_id: str, **overrides) -> dict:
    base = dict(
        id=opp_id,
        article_item_id=f"article-{opp_id}",
        category="volunteer",
        title=f"志愿活动 {opp_id}",
        source_name="测试源",
        article_url=f"https://example.com/{opp_id}",
        signup_url=f"https://v.wjx.cn/vm/{opp_id}.aspx",
        activity_time="",
        deadline="",
        location="",
        schedule_status="available",
        free_time_text="",
        matched_time_text="",
        raw_text="正文原文不应出现在 API 响应里",
        status="pending_confirmation",
        feishu_message_id="",
        score=None,
        score_reasons="",
    )
    base.update(overrides)
    return base


class OpportunitiesApiTest(unittest.TestCase):
    def setUp(self):
        self.old_env = {name: os.environ.get(name) for name in golden.ENV_KEYS}
        for name in golden.ENV_KEYS:
            os.environ.pop(name, None)
        invalidate_project_cache()
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        golden.write_project(self.root)
        self.db = MonitorDB(default_db_path(self.root))
        app = create_app(self.root, start_background=False)
        self.client = TestClient(app)

        now = datetime.now(timezone.utc)
        self.t_old = iso_utc(now - timedelta(days=2))
        self.t_mid = iso_utc(now - timedelta(hours=2))
        self.t_new = iso_utc(now - timedelta(hours=1))
        self.db.upsert_opportunity(
            opportunity_payload(
                "opp-old", score=80, score_reasons="关键词命中 +40", created_at=self.t_old
            )
        )
        self.db.upsert_opportunity(
            opportunity_payload(
                "opp-mid",
                category="work_study",
                score=30,
                status="pending_decision",
                created_at=self.t_mid,
            )
        )
        self.db.upsert_opportunity(
            opportunity_payload(
                "opp-new", score=None, status="pending_decision", created_at=self.t_new
            )
        )

    def tearDown(self):
        self.client.close()
        for name, value in self.old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        invalidate_project_cache()
        self.tmp.cleanup()

    def get_items(self, **params) -> dict:
        response = self.client.get("/admin/opportunities", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_no_params_returns_all_newest_first(self):
        data = self.get_items()
        self.assertEqual(data["count"], 3)
        self.assertEqual([item["id"] for item in data["items"]], ["opp-new", "opp-mid", "opp-old"])

    def test_public_fields_whitelist(self):
        data = self.get_items()
        item = data["items"][0]
        self.assertNotIn("raw_text", item)
        self.assertNotIn("feishu_message_id", item)
        self.assertIn("category_label", item)
        self.assertEqual(item["category_label"], "志愿活动")
        work_study = next(entry for entry in data["items"] if entry["id"] == "opp-mid")
        self.assertEqual(work_study["category_label"], "勤工助学")

    def test_since_filters_out_older_rows(self):
        since = iso_utc(datetime.now(timezone.utc) - timedelta(days=1))
        data = self.get_items(since=since)
        self.assertEqual([item["id"] for item in data["items"]], ["opp-new", "opp-mid"])

    def test_since_accepts_z_suffix_and_naive_as_utc(self):
        base = datetime.now(timezone.utc) - timedelta(days=1)
        with_z = iso_utc(base).replace("+00:00", "Z")
        self.assertEqual(self.get_items(since=with_z)["count"], 2)
        naive = base.replace(tzinfo=None).isoformat(timespec="seconds")
        self.assertEqual(self.get_items(since=naive)["count"], 2)

    def test_min_score_excludes_null_and_lower_scores(self):
        data = self.get_items(min_score=40)
        self.assertEqual([item["id"] for item in data["items"]], ["opp-old"])

    def test_status_filter(self):
        data = self.get_items(status="pending_decision")
        self.assertEqual([item["id"] for item in data["items"]], ["opp-new", "opp-mid"])

    def test_combined_filters_can_yield_empty(self):
        since = iso_utc(datetime.now(timezone.utc) - timedelta(days=1))
        data = self.get_items(since=since, min_score=90)
        self.assertEqual(data["count"], 0)
        self.assertEqual(data["items"], [])

    def test_limit_caps_results(self):
        data = self.get_items(limit=1)
        self.assertEqual([item["id"] for item in data["items"]], ["opp-new"])

    def test_invalid_since_returns_400(self):
        response = self.client.get("/admin/opportunities", params={"since": "昨天"})
        self.assertEqual(response.status_code, 400)

    def test_normalize_since_matches_db_format(self):
        self.assertEqual(
            normalize_since("2026-07-01T08:00:00Z"), "2026-07-01T08:00:00+00:00"
        )
        self.assertEqual(
            normalize_since("2026-07-01T16:00:00+08:00"), "2026-07-01T08:00:00+00:00"
        )


if __name__ == "__main__":
    unittest.main()
