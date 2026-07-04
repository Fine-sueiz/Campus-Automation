import unittest

from wg_monitor.opportunity import analyse_opportunity


class OpportunityTest(unittest.TestCase):
    def test_detect_volunteer_activity_and_schedule_available(self):
        schedule = {
            "day_start": "08:00",
            "day_end": "22:00",
            "days": {
                "monday": {"busy": []},
                "tuesday": {"busy": [{"name": "课", "start": "08:00", "end": "10:00"}]},
            },
        }
        text = "志愿活动招募，活动时间：周二 14:00-16:00。活动地点：图书馆一楼。报名链接 https://v.wjx.cn/vm/abc.aspx"
        result = analyse_opportunity("图书馆志愿服务活动招募通知", text, "https://example.com/a", "测试源", "1", {}, schedule)

        self.assertTrue(result.is_target)
        self.assertEqual(result.category, "volunteer")
        self.assertEqual(result.schedule_status, "available")
        self.assertIn("图书馆", result.location)
        self.assertEqual(result.signup_url, "https://v.wjx.cn/vm/abc.aspx")

    def test_schedule_conflict(self):
        schedule = {
            "day_start": "08:00",
            "day_end": "22:00",
            "days": {"tuesday": {"busy": [{"name": "课", "start": "13:00", "end": "17:00"}]}},
        }
        text = "志愿者招募，报名参加，周二 14:00-16:00。"
        result = analyse_opportunity("志愿服务", text, "https://example.com/a", "测试源", "1", {}, schedule)

        self.assertTrue(result.is_target)
        self.assertEqual(result.schedule_status, "conflict")

    def test_config_can_limit_to_work_study_only(self):
        schedule = {"day_start": "08:00", "day_end": "22:00", "days": {}}
        app_config = {
            "opportunity": {
                "enabled_categories": ["work_study"],
                "required_any": ["勤工助学"],
                "extra_keywords": ["勤工助学"],
            }
        }
        volunteer = analyse_opportunity(
            "图书馆志愿服务活动招募通知",
            "志愿活动招募，活动时间：周二 14:00-16:00。",
            "https://example.com/a",
            "测试源",
            "1",
            app_config,
            schedule,
        )
        work_study = analyse_opportunity(
            "图书馆勤工助学岗位招聘通知",
            "勤工助学岗位报名，请发送邮件到 job@example.edu.cn。",
            "https://example.com/b",
            "测试源",
            "2",
            app_config,
            schedule,
        )

        self.assertFalse(volunteer.is_target)
        self.assertTrue(work_study.is_target)
        self.assertEqual(work_study.category, "work_study")


if __name__ == "__main__":
    unittest.main()
