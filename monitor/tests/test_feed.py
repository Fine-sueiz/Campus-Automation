import unittest

from wg_monitor.feed import (
    configured_wechat2rss_feed_configs,
    parse_feed_xml,
    parse_html_list,
    parse_werss_feed_configs,
    parse_wechat2rss_list_configs,
    wechat2rss_url,
)


class FeedTest(unittest.TestCase):
    def test_parse_rss(self):
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <rss version="2.0">
          <channel>
            <title>测试</title>
            <item>
              <title>勤工助学岗位招聘</title>
              <link>https://example.com/a</link>
              <guid>1</guid>
              <description><![CDATA[报名发送到 job@example.com]]></description>
            </item>
          </channel>
        </rss>
        """
        items = parse_feed_xml(xml, source_name="测试源")

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "勤工助学岗位招聘")
        self.assertEqual(items[0].source_name, "测试源")

    def test_parse_html_list(self):
        html = """
        <ul class="news_list">
          <li><a href="/2026/0617/c17a1/page.htm">图书馆志愿活动招募通知</a><span>2026-06-17</span></li>
        </ul>
        """
        items = parse_html_list(
            html,
            source_name="网页源",
            base_url="https://lib.example.edu/17/list.htm",
            feed_config={"item_selector": "ul.news_list li", "link_selector": "a[href]"},
        )

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "图书馆志愿活动招募通知")
        self.assertEqual(items[0].link, "https://lib.example.edu/2026/0617/c17a1/page.htm")
        self.assertEqual(items[0].published, "2026-06-17")

    def test_parse_werss_feed_configs(self):
        xml = """<?xml version="1.0" encoding="utf-8"?>
        <rss version="2.0"><channel><item>
          <title>示例大学图书馆</title>
          <link>http://127.0.0.1:8001/rss/feed-123</link>
          <guid>feed-123</guid>
        </item></channel></rss>
        """

        self.assertEqual(
            parse_werss_feed_configs(xml, "http://127.0.0.1:8001/"),
            [{"name": "示例大学图书馆", "url": "http://127.0.0.1:8001/feed/feed-123.rss"}],
        )

    def test_parse_wechat2rss_list_configs(self):
        payload = """{
          "data": [
            {"id": "1234567890", "name": "示例大学图书馆"},
            {"bid": "12345", "title": "example-univ志愿者", "feed": "/feed/12345.xml"}
          ]
        }"""

        self.assertEqual(
            parse_wechat2rss_list_configs(payload, "http://127.0.0.1:8001", token="secret"),
            [
                {
                    "name": "示例大学图书馆",
                    "url": "http://127.0.0.1:8001/feed/1234567890.xml?k=secret",
                    "type": "rss",
                },
                {
                    "name": "example-univ志愿者",
                    "url": "http://127.0.0.1:8001/feed/12345.xml?k=secret",
                    "type": "rss",
                },
            ],
        )

    def test_configured_wechat2rss_feed_configs_and_urls(self):
        app_config = {
            "monitor": {
                "wechat2rss": {
                    "enabled": True,
                    "base_url": "http://127.0.0.1:8001/",
                    "token": "dev token",
                    "subscribe_ids": [{"id": "1234567890", "name": "图书馆"}],
                }
            }
        }

        self.assertEqual(
            configured_wechat2rss_feed_configs(app_config),
            [
                {
                    "name": "图书馆",
                    "url": "http://127.0.0.1:8001/feed/1234567890.xml?k=dev%20token",
                    "type": "rss",
                }
            ],
        )
        self.assertEqual(
            wechat2rss_url(app_config, "/addurl", url="https://mp.weixin.qq.com/s/demo"),
            "http://127.0.0.1:8001/addurl?url=https%3A%2F%2Fmp.weixin.qq.com%2Fs%2Fdemo&k=dev%20token",
        )


if __name__ == "__main__":
    unittest.main()
