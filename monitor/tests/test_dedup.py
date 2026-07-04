"""内容级去重测试：URL 归一化、标题指纹、跨公众号转发合并、时间窗过期。"""

from __future__ import annotations

import contextlib
import io
import os
import tempfile
import unittest
from pathlib import Path

import test_pipeline_golden as golden

from wg_monitor.db import MonitorDB, default_db_path
from wg_monitor.dedup import canonical_article_url, dedup_keys, normalized_title
from wg_monitor.pipeline import scan_cycle
from wg_monitor.settings import invalidate_project_cache


FORWARD_FEED = """<?xml version="1.0" encoding="utf-8"?>
<rss version="2.0">
  <channel>
    <title>转发测试源</title>
    <item>
      <title>【转发】图书馆志愿服务活动招募通知！</title>
      <link>https://example.com/forwarded-article</link>
      <guid>golden-volunteer-forwarded-001</guid>
      <pubDate>Sun, 07 Jun 2026 12:00:00 +0800</pubDate>
      <description><![CDATA[志愿活动招募，周二 14:00-16:00，地点图书馆一楼，报名链接 https://v.wjx.cn/vm/testVolunteer.aspx]]></description>
    </item>
  </channel>
</rss>
"""

APP_YML_TEMPLATE = """monitor:
  feed_urls:
    - name: 志愿测试源
      url: "{volunteer_feed}"
    - name: 转发测试源
      url: "{forward_feed}"
  article_timeout_seconds: 5
  fetch_article_html: false

volunteer:
  enabled: true
  source_accounts:
    - 志愿测试源
    - 转发测试源
  confirm_by_email: true
  notify_email: "me@example.com"

opportunity:
  enabled_categories:
    - work_study

dedup:
  enabled: true
  window_days: 14
  min_title_chars: 8

safety:
  auto_send: false

email:
  auto_send_opportunities: false

user:
  name: 测试同学
  phone: "13800000000"
  contact_email: "me@example.com"
"""


def write_forward_project(root: Path) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    volunteer_feed = root / "volunteer_feed.xml"
    forward_feed = root / "forward_feed.xml"
    volunteer_feed.write_text(golden.VOLUNTEER_FEED, encoding="utf-8")
    forward_feed.write_text(FORWARD_FEED, encoding="utf-8")
    (root / "config" / "app.yml").write_text(
        APP_YML_TEMPLATE.format(
            volunteer_feed=volunteer_feed.as_posix(),
            forward_feed=forward_feed.as_posix(),
        ),
        encoding="utf-8",
    )
    (root / "config" / "schedule.yml").write_text(golden.SCHEDULE_YML, encoding="utf-8")


class DedupUnitTest(unittest.TestCase):
    def test_wechat_long_url_strips_tracking_params(self):
        long_a = (
            "https://mp.weixin.qq.com/s?__biz=MzA5&mid=2650&idx=1&sn=abc123"
            "&chksm=xyz&scene=126&sessionid=1&subscene=91"
        )
        long_b = "https://mp.weixin.qq.com/s?sn=abc123&__biz=MzA5&idx=1&mid=2650&srcid=0702"
        self.assertEqual(canonical_article_url(long_a), canonical_article_url(long_b))
        self.assertNotIn("chksm", canonical_article_url(long_a))

    def test_wechat_short_url_drops_query(self):
        self.assertEqual(
            canonical_article_url("https://mp.weixin.qq.com/s/AbCdEf?from=timeline&isappinstalled=0"),
            "https://mp.weixin.qq.com/s/AbCdEf",
        )

    def test_generic_url_drops_fragment_and_utm(self):
        self.assertEqual(
            canonical_article_url("https://Example.com/a?utm_source=x&id=7#section"),
            "https://example.com/a?id=7",
        )

    def test_normalized_title_ignores_decorations_and_forward_prefix(self):
        self.assertEqual(
            normalized_title("【转发】图书馆志愿服务活动招募通知！！"),
            normalized_title("图书馆志愿服务活动招募通知"),
        )
        self.assertNotEqual(normalized_title("图书馆志愿招募"), normalized_title("体育馆志愿招募"))

    def test_short_title_has_no_title_key(self):
        keys = dedup_keys("通知", "https://example.com/a", min_title_chars=8)
        self.assertTrue(all(key.startswith("url:") for key in keys))

    def test_biz_only_wechat_url_is_not_article_identity(self):
        # 只有 __biz（公众号 ID）没有 sn/mid 时，不能压缩成同一个文章指纹
        article_a = canonical_article_url("https://mp.weixin.qq.com/s?__biz=MzA5&scene=126")
        article_b = canonical_article_url("https://mp.weixin.qq.com/s?__biz=MzA5&chksm=zzz")
        self.assertNotEqual(article_a, article_b)

    def test_naive_first_seen_timestamp_does_not_crash_window_check(self):
        from wg_monitor.pipeline import ScanContext, fingerprint_within_window

        ctx = ScanContext(root=Path("."), db=None, app_config={}, schedule_config={})  # type: ignore[arg-type]
        record = {"first_seen_at": "2026-07-01T00:00:00"}  # 无时区的历史数据
        self.assertTrue(fingerprint_within_window(ctx, record))
        record_old = {"first_seen_at": "2026-01-01T00:00:00"}
        self.assertFalse(fingerprint_within_window(ctx, record_old))


class DedupPipelineTest(unittest.TestCase):
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

    def test_forwarded_article_merges_into_one_opportunity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_forward_project(root)
            db = MonitorDB(default_db_path(root))

            counts = self.scan(root, db)

            self.assertEqual(counts["new_articles"], 2)
            self.assertEqual(counts["opportunities"], 1)
            self.assertEqual(counts["duplicates_merged"], 1)
            # 只发一封志愿确认邮件
            self.assertEqual(counts["volunteer_reminders_sent"], 1)
            self.assertEqual(len(db.list_opportunities()), 1)
            with db.connect() as conn:
                confirmations = conn.execute("SELECT * FROM volunteer_confirmations").fetchall()
                fingerprints = conn.execute("SELECT * FROM content_fingerprints").fetchall()
            self.assertEqual(len(confirmations), 1)
            # 标题键 seen_count 累计到 2（原文 + 转发）
            title_rows = [dict(row) for row in fingerprints if row["key"].startswith("title:")]
            self.assertEqual(len(title_rows), 1)
            self.assertEqual(title_rows[0]["seen_count"], 2)

    def test_expired_fingerprint_allows_new_round(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_forward_project(root)
            db = MonitorDB(default_db_path(root))

            first = self.scan(root, db)
            self.assertEqual(first["opportunities"], 1)

            # 把指纹伪造成 30 天前登记（超出 14 天窗口）
            with db.connect() as conn:
                conn.execute(
                    "UPDATE content_fingerprints SET first_seen_at = '2026-06-01T00:00:00+00:00'"
                )
                # 同时清空文章表模拟“新一期同名推文”（guid 不同）
                conn.execute("DELETE FROM articles")
                conn.execute("DELETE FROM volunteer_confirmations")
                conn.execute("DELETE FROM opportunities")

            second = self.scan(root, db)
            # 过窗后不合并：原文重新建机会，转发在同轮内合并到新机会
            self.assertEqual(second["opportunities"], 1)
            self.assertEqual(second["duplicates_merged"], 1)
            with db.connect() as conn:
                rows = conn.execute("SELECT * FROM content_fingerprints WHERE key LIKE 'title:%'").fetchall()
            self.assertEqual(len(rows), 1)
            # reset 语义：first_seen_at 已刷新为本期
            self.assertNotEqual(str(rows[0]["first_seen_at"]), "2026-06-01T00:00:00+00:00")


if __name__ == "__main__":
    unittest.main()
