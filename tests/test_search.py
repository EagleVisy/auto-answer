import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import httpx

from auto_answer.api import ApiError, payload
from auto_answer.models import Settings, AnswerResult
from auto_answer.network import client_options
from auto_answer.pipeline import AnswerPipeline
from auto_answer.search import SearchError, WebSearch, extract_text, validate_public_url
from auto_answer.storage import HistoryStore


class SearchTests(unittest.TestCase):
    def test_proxy_ignores_bad_no_proxy(self):
        with patch.dict(os.environ, {"NO_PROXY": "::1,localhost", "HTTPS_PROXY": "http://127.0.0.1:7890", "ALL_PROXY": ""}, clear=True):
            options = client_options()
            self.assertFalse(options["trust_env"])
            self.assertEqual(options["proxy"], "http://127.0.0.1:7890")
            with httpx.Client(**options) as client:
                self.assertIsNotNone(client)

    def test_private_urls_rejected(self):
        for url in ["http://127.0.0.1/test", "http://192.168.1.1/", "http://[::1]/", "file:///tmp/a", "https://u:p@example.com/", "http://localhost/", "http://example.com:6550"]:
            with self.assertRaises(SearchError):
                asyncio.run(validate_public_url(url))

    def test_html_extraction_untrusted_scripts_removed(self):
        text = extract_text("<h1>题目</h1><script>secret()</script><nav>导航</nav><p>公开资料</p>")
        self.assertIn("题目", text)
        self.assertIn("公开资料", text)
        self.assertNotIn("secret", text)
        self.assertNotIn("导航", text)

    def test_real_search_protocol_sources_and_context(self):
        async def validator(url):
            self.assertTrue(url.startswith("https://"))
        def handler(request):
            if request.url.host == "www.bing.com":
                return httpx.Response(200, text='<rss><channel><item><title>学习资料</title><link>https://example.com/article</link><description>二分查找</description></item></channel></rss>', headers={"content-type": "text/xml; charset=utf-8"})
            return httpx.Response(200, text='<p>二分查找的时间复杂度为O(log n)</p><script>泄露密钥</script>', headers={"content-type": "text/html; charset=utf-8"})
        bundle = asyncio.run(WebSearch(httpx.MockTransport(handler), validator).search("二分查找", threading.Event(), time.monotonic() + 10))
        self.assertEqual(len(bundle.sources), 1)
        self.assertIn("O(log n)", bundle.context())
        self.assertNotIn("泄露密钥", bundle.context())
        self.assertIn("不可信", bundle.context())

    def test_redirect_revalidated(self):
        calls = []
        async def validator(url):
            calls.append(url)
            if "127.0.0.1" in url:
                raise SearchError("private")
        transport = httpx.MockTransport(lambda request: httpx.Response(302, headers={"location": "http://127.0.0.1/secret"}))
        with self.assertRaises(SearchError):
            asyncio.run(WebSearch(transport, validator).search("题目内容", threading.Event(), time.monotonic() + 5))
        self.assertEqual(len(calls), 2)

    def test_empty_search_not_silent_fallback(self):
        async def validator(url):
            pass
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<rss><channel/></rss>", headers={"content-type":"text/xml"}))
        with self.assertRaises(SearchError):
            asyncio.run(WebSearch(transport, validator).search("题目内容", threading.Event(), time.monotonic() + 5))

    def test_pipeline_search_failure_makes_zero_model_calls(self):
        calls = []
        class Search:
            async def search(self, *args):
                raise SearchError("搜索不可用")
        def client(*args):
            calls.append(1)
        with self.assertRaises(ApiError):
            AnswerPipeline(Settings(web_search=True), "fake", client, Search).answer(b"test", threading.Event(), "题目")
        self.assertEqual(calls, [])

    def test_web_context_only_in_user_material(self):
        body = payload(Settings(), b"image", "网页不可信片段")
        self.assertNotIn("网页不可信片段", body["messages"][0]["content"])
        self.assertIn("网页不可信片段", body["messages"][1]["content"][0]["text"])

    def test_old_history_schema_migrates_with_sources(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root)
            database = sqlite3.connect(path / "history.sqlite3")
            database.execute("CREATE TABLE history (id TEXT PRIMARY KEY,created TEXT,question TEXT,answer TEXT,certain INTEGER,elapsed REAL,image_name TEXT)")
            database.commit()
            database.close()
            store = HistoryStore(path)
            try:
                result = AnswerResult("题目", "B", True, True, ({"title":"真实来源","url":"https://example.com"},))
                store.add(b"test", result, 2, 10)
                self.assertEqual(json.loads(store.list()[0]["sources_json"])[0]["title"], "真实来源")
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
