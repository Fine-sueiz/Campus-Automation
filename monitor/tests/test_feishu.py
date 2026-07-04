import unittest

from wg_monitor.feishu import (
    callback_toast,
    extract_card_action,
    extract_message_event,
    is_url_verification,
    opportunity_card,
    verify_callback_token,
)


class FeishuTest(unittest.TestCase):
    def test_url_verification(self):
        self.assertTrue(is_url_verification({"type": "url_verification", "challenge": "abc"}))

    def test_message_binding_event_parse(self):
        payload = {
            "event": {
                "message": {"chat_id": "oc_x", "message_type": "text", "content": "{\"text\":\"绑定\"}"},
                "sender": {"sender_id": {"open_id": "ou_x"}},
            }
        }
        chat_id, message_type, text = extract_message_event(payload)
        self.assertEqual(chat_id, "oc_x")
        self.assertEqual(message_type, "text")
        self.assertEqual(text, "绑定")

    def test_card_action_parse(self):
        payload = {"event": {"action": {"value": {"opportunity_id": "opp1", "decision": "join"}}, "operator": {"open_id": "ou_x"}}}
        self.assertEqual(extract_card_action(payload), ("opp1", "join", "ou_x"))

    def test_token_verification(self):
        verify_callback_token({"header": {"token": "t"}}, "t")
        with self.assertRaises(Exception):
            verify_callback_token({"header": {"token": "x"}}, "t")

    def test_card_contains_callback_buttons(self):
        card = opportunity_card({"id": "opp1", "title": "活动", "category": "volunteer", "schedule_status": "available"})
        self.assertEqual(card["body"]["elements"][1]["actions"][0]["behaviors"][0]["value"]["decision"], "join")
        self.assertEqual(callback_toast("ok")["toast"]["content"], "ok")


if __name__ == "__main__":
    unittest.main()
