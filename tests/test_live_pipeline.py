"""真实 Windows 截图 + OCR + 模拟官方 SSE；不发送任何外部网络请求。"""
import base64
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from PIL import Image
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import QApplication, QWidget

from auto_answer.api import DeepSeekClient
from auto_answer.capture import enable_dpi_awareness, monitors
from auto_answer.engine import Engine
from auto_answer.models import Rect, Settings
from auto_answer.overlays import physical_to_logical
from auto_answer.storage import HistoryStore


class QuestionWindow(QWidget):
    def __init__(self):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.digit = 1
        self.ticks = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self._tick)
        self.timer.start(400)
        self.setGeometry(physical_to_logical(monitors()[0]))

    def _tick(self):
        self.ticks += 1
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("white"))
        painter.setPen(QColor("#111111"))
        painter.setFont(QFont("Microsoft YaHei UI", 22))
        if self.digit is None:
            return
        # 刻意布置密集侧栏关键词、顶部纯数字计时和大间距选项。
        painter.setFont(QFont("Microsoft YaHei UI", 15))
        for index in range(16):
            painter.drawText(25, 100 + index * 44, "正确学习 / 阅读解答 / 题目通知")
        painter.setFont(QFont("Microsoft YaHei UI", 22))
        painter.drawText(650, 100, f"01:57:{59 - self.ticks % 60:02d}")
        for index, line in enumerate([f"1、（单选题）请选择正确结果：{self.digit} + 1 = （ ）", "A. 1", "B. 2", "C. 3", "D. 4"]):
            painter.drawText(650, 260 + index * 120, line)
        painter.setFont(QFont("Microsoft YaHei UI", 16))
        painter.drawText(650, 920, "下一题")


class LiveScreenPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait_for(self, predicate, seconds=18):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(.015)
        return False

    def test_auto_real_screen_ocr_mock_sse_then_manual(self):
        question = QuestionWindow()
        question.show()
        question.raise_()
        self.app.processEvents()
        state = {"calls": 0, "answer": "B", "images": []}
        def handler(request):
            self.assertEqual(request.url.host, "api.deepseek.com")
            body = json.loads(request.content)
            encoded = body["messages"][1]["content"][1]["image_url"]["url"].split(",", 1)[1]
            image = Image.open(io.BytesIO(base64.b64decode(encoded)))
            state["images"].append(image.size)
            state["calls"] += 1
            result = {"question": "请选择正确结果", "answer": state["answer"], "certain": True, "is_question": True}
            blocks = [
                {"choices": [{"delta": {"reasoning_content": "不可展示的模拟推理"}}]},
                {"choices": [{"delta": {"content": json.dumps(result, ensure_ascii=False)}}]},
                {"choices": [{"delta": {}, "finish_reason": "stop"}]},
            ]
            text = "\n\n".join("data: " + json.dumps(block, ensure_ascii=False) for block in blocks) + "\n\ndata: [DONE]\n\n"
            return httpx.Response(200, text=text, headers={"content-type": "text/event-stream"})
        real_client = DeepSeekClient
        def mock_client(settings, key):
            return real_client(settings, key, httpx.MockTransport(handler))
        with tempfile.TemporaryDirectory() as directory:
            history = HistoryStore(Path(directory))
            with patch("auto_answer.engine.DeepSeekClient", side_effect=mock_client):
                engine = Engine(history)
                answers, regions = [], []
                times={'start':0.0,'first_region':None,'first_answer':None}
                def region_event(rect):
                    regions.append(rect)
                    if times['first_region'] is None:times['first_region']=time.monotonic()-times['start']
                def answer_event(text):
                    answers.append(text)
                    if text=='B' and times['first_answer'] is None:times['first_answer']=time.monotonic()-times['start']
                engine.answer.connect(answer_event)
                engine.region_changed.connect(region_event)
                try:
                    settings = Settings(mode="auto", scan_interval_ms=300, stable_ms=300, privacy_accepted=True)
                    engine.configure(settings, "offline-placeholder")
                    times['start']=time.monotonic()
                    engine.start()
                    self.assertTrue(self.wait_for(lambda: "B" in answers), str(answers))
                    self.assertTrue(regions)
                    self.assertLess(times['first_region'],4.0,'首次候选反馈过慢')
                    self.assertLess(times['first_region'],times['first_answer'],'答题应晚于定位反馈')
                    Path('build').mkdir(exist_ok=True)
                    Path('build/auto-location-latency.json').write_text(json.dumps(times,ensure_ascii=False,indent=2),encoding='utf-8')
                    self.assertLess(state["images"][0][0], monitors()[0].width)
                    self.assertGreater(state["images"][0][0], 300)
                    self.assertGreater(regions[-1].x, monitors()[0].x + 700, '真实OCR误锁左侧繁杂菜单')
                    self.assertGreater(regions[-1].height, 700, '遗漏大间距的末项')
                    baseline = state["calls"]
                    self.wait_for(lambda: False, seconds=1)
                    self.assertEqual(state["calls"], baseline)
                    question.digit = 2
                    state["answer"] = "C"
                    question.update()
                    self.app.processEvents()
                    self.assertTrue(self.wait_for(lambda: "C" in answers), str(answers))
                    self.assertEqual(len(history.list()), 2)
                    engine.pause()
                    # 同一真实屏幕改用手动范围，验证两种模式皆走实际截图/OCR。
                    settings.mode = "manual"
                    settings.capture_rect = regions[-1]
                    engine.configure(settings, "offline-placeholder")
                    answers.clear()
                    engine.start()
                    self.assertTrue(self.wait_for(lambda: "C" in answers))
                    self.assertEqual(len(history.list()), 3)
                    self.assertTrue(all("模拟推理" not in row["answer"] for row in history.list()))
                    # 持续运行超过自动全屏重定位周期，仍不重复请求同一道题。
                    engine.pause()
                    settings.mode = "auto"
                    engine.configure(settings, "offline-placeholder")
                    answers.clear()
                    engine.start()
                    self.assertTrue(self.wait_for(lambda: "C" in answers))
                    current_calls = state["calls"]
                    self.wait_for(lambda: False, seconds=13)
                    self.assertEqual(state["calls"], current_calls, "自动重定位导致同题重复答题")
                    # 自动全屏最终判定没有题目后，不保留旧答案。
                    question.digit = None
                    question.update()
                    self.assertTrue(self.wait_for(lambda: answers[-1] == "等待题目", seconds=18))
                finally:
                    engine.close()
            history.close()
        question.close()


if __name__ == "__main__":
    enable_dpi_awareness()
    unittest.main()
