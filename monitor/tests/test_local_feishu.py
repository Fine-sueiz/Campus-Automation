import os
import tempfile
import unittest
from pathlib import Path

from wg_monitor.db import MonitorDB
from wg_monitor.feishu_handlers import handle_binding_message, handle_card_decision
from wg_monitor.local_feishu import send_test_card


class FakeFeishuClient:
    def __init__(self):
        self.texts = []
        self.cards = []

    def send_text(self, chat_id, text):
        self.texts.append((chat_id, text))
        return f"text-{len(self.texts)}"

    def send_message(self, chat_id, card):
        self.cards.append((chat_id, card))
        return f"msg-{len(self.cards)}"


def make_db(root: Path) -> MonitorDB:
    return MonitorDB(root / "data" / "campus_monitor.sqlite3")


def insert_opportunity(db: MonitorDB, opportunity_id: str = "opp1", signup_url: str = "https://v.wjx.cn/vm/a.aspx"):
    db.upsert_opportunity(
        {
            "id": opportunity_id,
            "article_item_id": "item1",
            "category": "volunteer",
            "title": "志愿活动",
            "source_name": "测试公众号",
            "article_url": "https://example.com/article",
            "signup_url": signup_url,
            "activity_time": "周三 14:00-17:00",
            "deadline": "",
            "location": "图书馆",
            "schedule_status": "available",
            "free_time_text": "",
            "matched_time_text": "",
            "raw_text": "",
            "status": "pending_decision",
            "feishu_message_id": "",
        }
    )


class LocalFeishuTest(unittest.TestCase):
    def setUp(self):
        self.old_app_id = os.environ.get("FEISHU_APP_ID")
        self.old_app_secret = os.environ.get("FEISHU_APP_SECRET")
        os.environ["FEISHU_APP_ID"] = "cli_test"
        os.environ["FEISHU_APP_SECRET"] = "secret_test"

    def tearDown(self):
        if self.old_app_id is None:
            os.environ.pop("FEISHU_APP_ID", None)
        else:
            os.environ["FEISHU_APP_ID"] = self.old_app_id
        if self.old_app_secret is None:
            os.environ.pop("FEISHU_APP_SECRET", None)
        else:
            os.environ["FEISHU_APP_SECRET"] = self.old_app_secret

    def test_binding_message_saves_chat_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = make_db(Path(tmp))
            client = FakeFeishuClient()
            result = handle_binding_message(
                db,
                client,
                {
                    "event": {
                        "message": {
                            "chat_id": "oc_x",
                            "message_type": "text",
                            "content": "{\"text\":\"绑定\"}",
                        }
                    }
                },
            )
            self.assertEqual(result["bound_chat_id"], "oc_x")
            self.assertEqual(db.get_binding("default_chat_id"), "oc_x")
            self.assertEqual(client.texts[0][0], "oc_x")

    def test_card_join_creates_form_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = make_db(root)
            db.set_binding("default_chat_id", "oc_x")
            insert_opportunity(db)
            client = FakeFeishuClient()
            called = []

            toast = handle_card_decision(
                root,
                db,
                client,
                {"event": {"action": {"value": {"opportunity_id": "opp1", "decision": "join"}}}},
                after_join=lambda: called.append(True),
            )

            self.assertIn("报名任务已创建", toast["toast"]["content"])
            self.assertEqual(db.get_opportunity("opp1")["status"], "approved")
            self.assertEqual(len(db.due_form_tasks("9999-01-01T00:00:00+00:00")), 1)
            self.assertEqual(called, [True])

    def test_send_test_card_requires_bound_chat_and_sends_card(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            db = make_db(root)
            db.set_binding("default_chat_id", "oc_x")
            client = FakeFeishuClient()

            message_id = send_test_card(root, db, client)

            self.assertEqual(message_id, "msg-1")
            self.assertEqual(client.cards[0][0], "oc_x")
            self.assertEqual(db.get_opportunity("local-feishu-test")["status"], "pending_decision")


if __name__ == "__main__":
    unittest.main()
