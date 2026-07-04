import os
import tempfile
import unittest
from pathlib import Path

from wg_monitor.form_runner import run_form_task


class FormRunnerTest(unittest.TestCase):
    def test_fake_runner_generates_config(self):
        old = os.environ.get("FORM_RUNNER_MODE")
        os.environ["FORM_RUNNER_MODE"] = "fake"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                result = run_form_task(
                    root,
                    {"user": {"name": "张三", "phone": "13800000000", "student_id": "1"}, "questionnaire_profile": {"college": "学院", "grade": "大一"}},
                    {"title": "志愿活动", "signup_url": "https://v.wjx.cn/vm/a.aspx"},
                    "task1",
                )
                self.assertEqual(result.status, "submitted")
                self.assertTrue(Path(result.config_path).exists())
        finally:
            if old is None:
                os.environ.pop("FORM_RUNNER_MODE", None)
            else:
                os.environ["FORM_RUNNER_MODE"] = old


if __name__ == "__main__":
    unittest.main()
