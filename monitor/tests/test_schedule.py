import unittest

from wg_monitor.schedule import compute_free_blocks, format_free_blocks, parse_time


class ScheduleTest(unittest.TestCase):
    def test_compute_free_blocks(self):
        config = {
            "day_start": "08:00",
            "day_end": "12:00",
            "days": {
                "monday": {"busy": [{"name": "课", "start": "09:00", "end": "10:00"}]},
                "tuesday": {"busy": []},
            },
        }

        free = compute_free_blocks(config)

        self.assertEqual(free["monday"][0].start, parse_time("08:00"))
        self.assertEqual(free["monday"][0].end, parse_time("09:00"))
        self.assertEqual(free["monday"][1].start, parse_time("10:00"))
        self.assertIn("周一：08:00-09:00，10:00-12:00", format_free_blocks(free))


if __name__ == "__main__":
    unittest.main()
