import unittest

from wg_monitor.emailer import EmailSettings, compose_application_email
from wg_monitor.extraction import analyse_article


class EmailerTest(unittest.TestCase):
    def test_compose_application_email(self):
        app_config = {
            "user": {"name": "张三", "phone": "13800000000", "contact_email": "me@example.com"},
            "email": {"subject_template": "勤工助学岗位申请 - {name} - 可用时间"},
            "monitor": {},
            "safety": {},
        }
        analysis = analyse_article(
            "勤工助学岗位招聘",
            "下学期勤工助学岗位报名，请发送到 job@example.com",
            {"monitor": {}, "safety": {"min_keyword_hits": 2}},
        )

        msg = compose_application_email(app_config, analysis, "job@example.com", "https://example.com", "周一：空闲", [])

        self.assertEqual(msg["Subject"], "勤工助学岗位申请 - 张三 - 可用时间")
        self.assertIn("张三", msg.get_content())

    def test_email_settings_defaults_to_dry_run(self):
        settings = EmailSettings.from_env()
        self.assertTrue(settings.dry_run)


if __name__ == "__main__":
    unittest.main()
