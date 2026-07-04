import tempfile
import unittest
from pathlib import Path

from wg_monitor.db import MonitorDB, utc_now


class DBTest(unittest.TestCase):
    def test_db_state_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = MonitorDB(Path(tmp) / "test.sqlite3")
            db.insert_article("item1", "源", "标题", "https://example.com")
            self.assertTrue(db.article_exists("item1"))
            db.set_binding("default_chat_id", "oc_x")
            self.assertEqual(db.get_binding("default_chat_id"), "oc_x")
            db.upsert_opportunity(
                {
                    "id": "opp1",
                    "article_item_id": "item1",
                    "category": "volunteer",
                    "title": "活动",
                    "source_name": "源",
                    "article_url": "https://example.com",
                    "signup_url": "https://v.wjx.cn/vm/a.aspx",
                    "activity_time": "",
                    "deadline": "",
                    "location": "",
                    "schedule_status": "available",
                    "free_time_text": "",
                    "matched_time_text": "",
                    "raw_text": "",
                    "status": "pending_decision",
                    "feishu_message_id": "",
                }
            )
            self.assertEqual(db.get_opportunity("opp1")["title"], "活动")
            db.create_form_task("task1", "opp1", "https://v.wjx.cn/vm/a.aspx", utc_now())
            self.assertEqual(len(db.due_form_tasks(utc_now())), 1)


if __name__ == "__main__":
    unittest.main()
