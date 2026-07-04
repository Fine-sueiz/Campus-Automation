"""评分器测试：加权评分、负面词、影子模式分歧记录、三档拦截。"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

import test_pipeline_golden as golden

from wg_monitor.db import MonitorDB, default_db_path
from wg_monitor.opportunity import OpportunityAnalysis
from wg_monitor.pipeline import scan_cycle
from wg_monitor.scoring import score_opportunity
from wg_monitor.settings import invalidate_project_cache


def make_analysis(**overrides) -> OpportunityAnalysis:
    base = dict(
        id="opp-score-1",
        is_target=True,
        category="volunteer",
        category_label="志愿活动",
        title="图书馆志愿服务活动招募通知",
        source_name="测试源",
        article_item_id="a1",
        article_url="https://example.com/a",
        signup_url="https://v.wjx.cn/vm/x.aspx",
        activity_time="",
        deadline="",
        location="",
        schedule_status="available",
        free_time_text="",
        matched_time_text="",
        raw_text="",
        keyword_hits=["志愿服务", "招募", "志愿活动", "报名"],
        reasons=[],
    )
    base.update(overrides)
    return OpportunityAnalysis(**base)


class ScoringUnitTest(unittest.TestCase):
    def test_recruitment_article_reaches_notify(self):
        result = score_opportunity(
            "图书馆志愿服务活动招募通知",
            "志愿活动招募，周二 14:00-16:00，报名链接 https://v.wjx.cn/vm/x.aspx",
            make_analysis(),
            {},
        )
        self.assertGreaterEqual(result.score, 60)
        self.assertEqual(result.verdict, "notify")
        self.assertTrue(any("关键词命中" in reason for reason in result.reasons))

    def test_aftermath_announcement_is_ignored(self):
        analysis = make_analysis(
            title="志愿服务时长名单公示",
            signup_url="https://example.com/a",  # 与原文相同 → 无独立报名链接加分
            schedule_status="unknown_time",
            keyword_hits=["志愿服务", "志愿者"],
        )
        result = score_opportunity(
            "志愿服务时长名单公示",
            "各位志愿者请核对志愿服务时长名单，如有问题请联系。",
            analysis,
            {},
        )
        self.assertLess(result.score, 40)
        self.assertEqual(result.verdict, "ignore")
        self.assertTrue(any("疑似事后文" in reason for reason in result.reasons))

    def test_negative_points_are_capped(self):
        analysis = make_analysis()
        three = score_opportunity("公示名单总结招募报名", "申请征集", analysis, {})
        four = score_opportunity("公示名单总结回顾招募报名", "申请征集", analysis, {})
        # 负面词超过封顶后不再继续扣分
        self.assertEqual(three.score, four.score)
        self.assertGreaterEqual(three.score, 0)

    def test_thresholds_come_from_config(self):
        config = {"scoring": {"notify_min": 95, "inbox_min": 5}}
        result = score_opportunity(
            "图书馆志愿服务活动招募通知",
            "志愿活动招募，报名链接 https://v.wjx.cn/vm/x.aspx",
            make_analysis(),
            config,
        )
        self.assertEqual(result.verdict, "inbox")

    def test_inverted_thresholds_are_sanitized(self):
        # 误配 inbox_min > notify_min 时不能出现"够 notify 却被判 ignore"的空档
        config = {"scoring": {"notify_min": 30, "inbox_min": 80}}
        result = score_opportunity(
            "图书馆志愿服务活动招募通知",
            "志愿活动招募，报名链接 https://v.wjx.cn/vm/x.aspx",
            make_analysis(),
            config,
        )
        self.assertEqual(result.verdict, "notify")
        # notify_min=300 clamp 到 100：常规分数够不到 notify；inbox_min=-5 clamp 到 0
        config_high = {"scoring": {"notify_min": 300, "inbox_min": -5}}
        result_high = score_opportunity("通知", "无关内容", make_analysis(keyword_hits=[]), config_high)
        self.assertEqual(result_high.verdict, "inbox")


GONGSHI_FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>公示测试源</title>
    <item>
      <title>志愿服务时长名单公示通知</title>
      <link>https://example.com/gongshi-article</link>
      <guid>golden-gongshi-001</guid>
      <pubDate>Mon, 08 Jun 2026 10:00:00 +0800</pubDate>
      <description><![CDATA[各位志愿者请核对志愿服务时长名单，如有问题请联系学院办公室。]]></description>
    </item>
  </channel>
</rss>
"""

APP_YML_TEMPLATE = """monitor:
  feed_urls:
    - name: 志愿测试源
      url: "{volunteer_feed}"
    - name: 公示测试源
      url: "{gongshi_feed}"
  article_timeout_seconds: 5
  fetch_article_html: false

volunteer:
  enabled: true
  source_accounts:
    - 志愿测试源
    - 公示测试源
  confirm_by_email: true
  notify_email: "me@example.com"

opportunity:
  enabled_categories:
    - work_study

scoring:
  enabled: true
  shadow_mode: {shadow_mode}
  notify_min: {notify_min}
  inbox_min: {inbox_min}

safety:
  auto_send: false

email:
  auto_send_opportunities: false

user:
  name: 测试同学
  phone: "13800000000"
  contact_email: "me@example.com"
"""


def write_scoring_project(root: Path, *, shadow_mode: str, notify_min: int, inbox_min: int) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    volunteer_feed = root / "volunteer_feed.xml"
    gongshi_feed = root / "gongshi_feed.xml"
    volunteer_feed.write_text(golden.VOLUNTEER_FEED, encoding="utf-8")
    gongshi_feed.write_text(GONGSHI_FEED, encoding="utf-8")
    (root / "config" / "app.yml").write_text(
        APP_YML_TEMPLATE.format(
            volunteer_feed=volunteer_feed.as_posix(),
            gongshi_feed=gongshi_feed.as_posix(),
            shadow_mode=shadow_mode,
            notify_min=notify_min,
            inbox_min=inbox_min,
        ),
        encoding="utf-8",
    )
    (root / "config" / "schedule.yml").write_text(golden.SCHEDULE_YML, encoding="utf-8")


class ScoringPipelineTest(unittest.TestCase):
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

    def test_shadow_mode_records_scores_without_changing_behavior(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_scoring_project(root, shadow_mode="true", notify_min=60, inbox_min=40)
            db = MonitorDB(default_db_path(root))

            counts = self.scan(root, db)

            # 影子模式：布尔判定照旧，两条都建机会、都发提醒
            self.assertEqual(counts["opportunities"], 2)
            self.assertEqual(counts["volunteer_reminders_sent"], 2)
            self.assertEqual(counts["score_suppressed"], 0)
            # 公示文：布尔说是机会、评分说忽略 → 记一次分歧
            self.assertEqual(counts["score_disagreements"], 1)

            rows = {row["title"]: row for row in db.list_opportunities()}
            recruit = next(row for title, row in rows.items() if "招募" in title)
            gongshi = next(row for title, row in rows.items() if "公示" in title)
            self.assertGreaterEqual(recruit["score"], 60)
            self.assertLess(gongshi["score"], 40)
            self.assertIn("疑似事后文", gongshi["score_reasons"])

            disagreement_logs = [
                row for row in db.recent_logs(20) if "score disagreement" in str(row.get("message"))
            ]
            self.assertEqual(len(disagreement_logs), 1)

    def test_enforcement_suppresses_low_and_quiets_middle_tier(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # notify_min=95：招募文只够 inbox 档；公示文低于 inbox_min → 直接忽略
            write_scoring_project(root, shadow_mode="false", notify_min=95, inbox_min=10)
            db = MonitorDB(default_db_path(root))

            counts = self.scan(root, db)

            self.assertEqual(counts["score_suppressed"], 1)
            self.assertEqual(counts["opportunities"], 1)
            # inbox 档：建机会但不发确认邮件
            self.assertEqual(counts["volunteer_reminders_sent"], 0)
            self.assertGreaterEqual(counts["volunteer_reminders_skipped"], 1)
            rows = db.list_opportunities()
            self.assertEqual(len(rows), 1)
            self.assertIn("招募", rows[0]["title"])
            # inbox 档没发确认邮件，状态不能伪装成"等待确认"
            self.assertEqual(rows[0]["status"], "pending_decision")
            # inbox 档不做 Web 推送：物化的用户机会已被预标记为已推送
            self.assertEqual(db.list_unpushed_user_opportunities(), [])


if __name__ == "__main__":
    unittest.main()
