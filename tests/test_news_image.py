from __future__ import annotations
import io
import socket
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
import daily_news
import news_image


class NewsImageTests(unittest.TestCase):
    def draft(self, count=5):
        return daily_news.NewsDraft(title="AI 早报", blocks=[], items=[
            {"fact":"新模型开放测试，支持较长的文本输入。" * 4, "comment":"更关心实际使用体验。", "source":"示例来源", "url":"https://example.com/article"}
            for _ in range(count)
        ])

    def test_long_chinese_news_renders_with_photo_and_excerpt(self):
        photo = io.BytesIO()
        Image.new("RGB", (800, 500), "#1b98a0").save(photo, "PNG")
        media = [news_image.NewsMedia(image=photo.getvalue()), news_image.NewsMedia(excerpt="这是带出处的原文摘录。" * 4)]
        data = news_image.render_news(self.draft(), now=time.time(), media=media)
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.width, 1080)
            self.assertGreater(image.height, 2000)
            self.assertLess(image.height, 6000)

    def test_no_photos_is_offline_and_retains_attributed_excerpt(self):
        with patch.object(news_image, "fetch_public", side_effect=AssertionError("network not allowed")):
            asset = news_image.article_media({"url":"https://example.com/a", "content":"正文摘录" * 30}, photos=False)
        self.assertEqual(len(asset.excerpt), 101)
        self.assertIsNone(asset.image)

    def test_article_meta_parser_does_not_execute_html(self):
        parser = news_image.ArticleMedia()
        parser.feed('<script>deleteEverything()</script><meta content="/photo.jpg" property="og:image">')
        self.assertEqual(parser.images, ["/photo.jpg"])

    def test_private_addresses_and_credentials_are_rejected(self):
        for url in ("http://example.com", "https://user:pass@example.com", "https://example.com:8001/a"):
            with self.assertRaises(ValueError): news_image.public_target(url)
        for ip in ("127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "192.168.1.1"):
            rows=[(socket.AF_INET,socket.SOCK_STREAM,6,"",(ip,443))]
            with patch.object(socket,"getaddrinfo",return_value=rows):
                with self.assertRaises(ValueError): news_image.public_target("https://example.com/a")

    def test_personnel_and_negative_claims_need_primary_verbatim_evidence(self):
        source={"url":"https://nvidianews.nvidia.com/news/test", "title":"CEO resigns", "content":"The CEO has resigned as President and Chief Executive Officer.", "source":"英伟达官方"}
        written={"items":[{"id":1,"fact":"公司首席执行官辞职。"}]}
        self.assertEqual(daily_news.verify_written(written,[source],[])[0],[])
        written["items"][0]["evidence"]="The CEO has resigned as President and Chief Executive Officer."
        self.assertEqual(len(daily_news.verify_written(written,[source],[])[0]),1)
        written["items"][0]["fact"]="公司首席执行官被开除。"
        self.assertEqual(daily_news.verify_written(written,[source],[])[0],[])
        source["url"]="https://nvidia.com.example.org/news/test"
        written["items"][0]["fact"]="公司首席执行官辞职。"
        self.assertEqual(daily_news.verify_written(written,[source],[])[0],[])
