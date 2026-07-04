import unittest

from wg_monitor.extraction import analyse_article, extract_emails, extract_time_windows


APP_CONFIG = {
    "monitor": {
        "keywords": {
            "required_any": ["勤工助学", "助学岗位"],
            "focus_any": ["岗位", "招聘", "报名", "下学期"],
        }
    },
    "safety": {"min_keyword_hits": 2},
}


class ExtractionTest(unittest.TestCase):
    def test_extract_unique_email(self):
        self.assertEqual(extract_emails("请发送到 test@example.com。"), ["test@example.com"])

    def test_analyse_target_article(self):
        text = "下学期勤工助学岗位开始报名，请发送简历到 job@example.edu.cn，周一 14:00-16:00 工作。"
        analysis = analyse_article("勤工助学岗位招聘", text, APP_CONFIG)

        self.assertTrue(analysis.is_target)
        self.assertEqual(analysis.emails, ["job@example.edu.cn"])
        self.assertEqual(len(analysis.time_windows), 1)

    def test_extract_day_range(self):
        windows = extract_time_windows("工作时间：周一至周三 09:00-11:00。")
        self.assertEqual(len(windows), 3)


if __name__ == "__main__":
    unittest.main()
