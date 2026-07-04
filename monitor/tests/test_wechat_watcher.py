import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from wg_monitor.db import MonitorDB
from wg_monitor.server import create_app, db_path_for
from wg_monitor.wechat_integration import WechatWatcherSettings
from wg_monitor.wechat_watcher import (
    UiTextNode,
    VisibleArticle,
    extract_from_lines,
    extract_visible_articles,
    run_cycle,
)


def write_test_config(root: Path) -> None:
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "config" / "app.yml").write_text(
        """
monitor:
  feed_urls: []
opportunity:
  enabled_categories: [work_study]
  required_any: [勤工助学]
  extra_keywords: [勤工助学]
volunteer:
  enabled: true
  confirm_by_email: false
  source_accounts:
    - example-univ青春金融
    - example-univ志愿者
  required_any: [志愿服务, 志愿活动, 志愿者, 志愿时长]
  focus_any: [报名, 招募, 参加, 志愿时长]
schedule_inbox:
  enabled: true
  api_base: http://127.0.0.1:8000
  api_key: test-schedule-key
  integration_key: test-integration-key
  usernames: [admin]
wechat_watcher:
  enabled: true
  api_base: http://127.0.0.1:8011
  integration_key: test-integration-key
  poll_seconds: 30
  window_title: 微信
  baseline_on_first_run: true
""",
        encoding="utf-8",
    )
    (root / "config" / "schedule.yml").write_text("days: {}\n", encoding="utf-8")


class WechatWatcherParserTest(unittest.TestCase):
    def test_extract_visible_article_from_positioned_controls(self):
        nodes = [
            UiTextNode("常看的号", 100, 80, 180, 110),
            UiTextNode("example-univ青春金融", 130, 240, 280, 265),
            UiTextNode("2小时前", 510, 240, 570, 265),
            UiTextNode("【青春领航】积极备考，诚信考试", 130, 278, 480, 305),
            UiTextNode("普通校园公众号", 130, 400, 280, 425),
            UiTextNode("图书馆志愿服务招募报名", 130, 438, 480, 465),
        ]
        items = extract_visible_articles(nodes, ["example-univ青春金融", "example-univ志愿者"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source_name, "example-univ青春金融")
        self.assertEqual(items[0].title, "【青春领航】积极备考，诚信考试")
        self.assertEqual(items[0].published_text, "2小时前")

    def test_extract_line_fallback_ignores_non_allowlisted_source(self):
        items = extract_from_lines(
            [
                "普通校园公众号",
                "图书馆志愿服务招募报名",
                "example-univ志愿者",
                "1小时前",
                "周末志愿服务活动开始报名",
            ],
            ["example-univ志愿者"],
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "周末志愿服务活动开始报名")

    def test_fuzzy_matches_windows_ocr_account_text(self):
        nodes = [
            UiTextNode("0 EXA 屿 PLE-UNIV 青 春 全 融", 266, 792, 528, 824, "OCR"),
            UiTextNode("2 小 时 前", 1035, 804, 1140, 830, "OCR"),
            UiTextNode("青 春 领 航 积 极 备 考 诚 信 考 试", 284, 859, 754, 890, "OCR"),
        ]
        items = extract_visible_articles(nodes, ["example-univ青春金融"])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].source_name, "example-univ青春金融")
        self.assertIn("诚信考试", items[0].title)


class WechatWatcherApiTest(unittest.TestCase):
    def test_api_requires_integration_key_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_test_config(root)
            app = create_app(root, start_background=False)
            client = TestClient(app)
            payload = {
                "watcher": {"status": "running", "visible_items": 1, "page_detected": True, "pid": 123},
                "items": [
                    {
                        "source_name": "example-univ志愿者",
                        "title": "图书馆志愿服务活动招募报名通知",
                        "published_text": "1小时前",
                        "raw_text": "图书馆志愿服务活动招募报名通知，地点：图书馆。",
                    }
                ],
            }

            self.assertEqual(client.post("/api/integrations/wechat/articles", json=payload).status_code, 401)
            with patch(
                "wg_monitor.wechat_integration.sync_opportunity_to_schedule_inbox",
                return_value={"status": "synced", "inbox_item_id": "inbox-1"},
            ):
                first = client.post(
                    "/api/integrations/wechat/articles",
                    headers={"X-Integration-Key": "test-integration-key"},
                    json=payload,
                )
                second = client.post(
                    "/api/integrations/wechat/articles",
                    headers={"X-Integration-Key": "test-integration-key"},
                    json=payload,
                )

            self.assertEqual(first.status_code, 200)
            self.assertEqual(first.json()["counts"]["created"], 1)
            self.assertEqual(first.json()["counts"]["inbox_sent"], 1)
            self.assertEqual(second.json()["counts"]["duplicates"], 1)
            db = MonitorDB(db_path_for(root))
            self.assertEqual(len(db.list_wechat_captures()), 1)
            self.assertEqual(len(db.list_opportunities()), 1)

            status = client.get(
                "/api/integrations/wechat/status",
                headers={"X-Integration-Key": "test-integration-key"},
            )
            self.assertEqual(status.status_code, 200)
            self.assertEqual(status.json()["watcher"]["status"], "running")

    def test_non_allowlisted_source_is_recorded_but_not_created(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_test_config(root)
            client = TestClient(create_app(root, start_background=False))
            response = client.post(
                "/api/integrations/wechat/articles",
                headers={"X-Integration-Key": "test-integration-key"},
                json={
                    "watcher": {"status": "running"},
                    "items": [
                        {
                            "source_name": "普通校园公众号",
                            "title": "志愿服务活动招募报名",
                        }
                    ],
                },
            )
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["counts"]["not_allowed"], 1)
            self.assertEqual(len(MonitorDB(db_path_for(root)).list_opportunities()), 0)

    def test_first_run_builds_baseline_without_posting_articles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_test_config(root)
            settings = WechatWatcherSettings.from_config(
                {
                    "wechat_watcher": {
                        "enabled": True,
                        "api_base": "http://127.0.0.1:8011",
                        "integration_key": "key",
                        "baseline_on_first_run": True,
                    }
                }
            )
            article = VisibleArticle("item-1", "example-univ志愿者", "志愿服务招募")
            captured = []

            def fake_post(_settings, watcher, items):
                captured.append((watcher, items))
                return {"ok": True}

            state_path = root / "data" / "wechat_watcher_state.json"
            with patch("wg_monitor.wechat_watcher.read_wechat_ui", return_value=([], True)), patch(
                "wg_monitor.wechat_watcher.extract_visible_articles", return_value=[article]
            ), patch("wg_monitor.wechat_watcher.post_articles", side_effect=fake_post):
                result = run_cycle(root, settings, state_path)

            self.assertEqual(result["status"], "baseline_ready")
            self.assertEqual(captured[0][1], [])
            self.assertIn("item-1", state_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
