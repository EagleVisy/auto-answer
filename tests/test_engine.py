from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication

from auto_answer.engine import Engine
from auto_answer.detection import TextBox
from auto_answer.models import AnswerResult, Rect, Settings
from auto_answer.storage import HistoryStore


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.application = QApplication.instance() or QApplication([])

    def pump_until(self, predicate, seconds=6):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            self.application.processEvents()
            if predicate():
                return True
            time.sleep(.015)
        return False

    def test_pipeline_stable_duplicate_changed_pause_no_network(self):
        images = [Image.new("RGB", (600, 260), "white") for _ in range(2)]
        ImageDraw.Draw(images[0]).rectangle((90, 60, 140, 90), fill="black")
        ImageDraw.Draw(images[1]).rectangle((90, 60, 170, 90), fill="black")
        state = {"index": 0, "calls": 0}
        class Reader:
            def read(self, image):
                return [TextBox(Rect(50, 30, 300, 30), "请选择正确答案？", .99)]
        class Client:
            def __init__(self, settings, key):
                pass
            def answer(self, image, cancel):
                state["calls"] += 1
                return AnswerResult("请选择正确答案？", f"B{state['calls']}", True, True)
        with tempfile.TemporaryDirectory() as root:
            history = HistoryStore(Path(root))
            with patch("auto_answer.engine.OcrReader", Reader), patch("auto_answer.engine.monitors", return_value=[Rect(0, 0, 1920, 1080)]), patch("auto_answer.engine.grab", side_effect=lambda *args: images[state["index"]].copy()), patch("auto_answer.engine.DeepSeekClient", Client):
                engine = Engine(history)
                answers = []
                engine.answer.connect(answers.append)
                settings = Settings(capture_rect=Rect(100, 100, 600, 260), scan_interval_ms=250, stable_ms=300, privacy_accepted=True)
                engine.configure(settings, "test-placeholder")
                engine.start()
                self.assertTrue(self.pump_until(lambda: "B1" in answers))
                deadline = time.monotonic() + .8
                while time.monotonic() < deadline:
                    self.application.processEvents()
                    time.sleep(.02)
                self.assertEqual(state["calls"], 1)
                state["index"] = 1
                self.assertTrue(self.pump_until(lambda: "B2" in answers))
                self.assertEqual(state["calls"], 2)
                self.assertEqual(len(history.list()), 2)
                engine.pause()
                state["index"] = 0
                for _ in range(20):
                    self.application.processEvents()
                    time.sleep(.02)
                self.assertEqual(state["calls"], 2)
                engine.close()
            history.close()

    def test_transient_change_returning_to_same_question_recovers(self):
        image = Image.new("RGB", (600, 260), "white")
        ImageDraw.Draw(image).rectangle((90, 60, 140, 90), fill="black")
        altered = image.copy()
        ImageDraw.Draw(altered).rectangle((90, 60, 170, 90), fill="black")
        state = {"image": image, "calls": 0}
        class Reader:
            def read(self, image):
                return [TextBox(Rect(50, 30, 300, 30), "请选择正确答案？", .99)]
        class Client:
            def __init__(self, settings, key):
                pass
            def answer(self, image, cancel):
                state["calls"] += 1
                return AnswerResult("测试", "B", True, True)
        with tempfile.TemporaryDirectory() as root:
            history = HistoryStore(Path(root))
            with patch("auto_answer.engine.OcrReader", Reader), patch("auto_answer.engine.monitors", return_value=[Rect(0, 0, 1920, 1080)]), patch("auto_answer.engine.grab", side_effect=lambda *args: state["image"].copy()), patch("auto_answer.engine.DeepSeekClient", Client):
                engine = Engine(history)
                answers = []
                engine.answer.connect(answers.append)
                engine.configure(Settings(capture_rect=Rect(100, 100, 600, 260), scan_interval_ms=250, stable_ms=300, privacy_accepted=True), "test")
                engine.start()
                self.assertTrue(self.pump_until(lambda: "B" in answers))
                state["image"] = altered
                self.assertTrue(self.pump_until(lambda: answers[-1] == "等待题目稳定"))
                state["image"] = image
                self.assertTrue(self.pump_until(lambda: answers[-1] == "B"), "题目恢复原画面后一直等待")
                engine.close()
            history.close()

    def test_late_first_request_cannot_overwrite_second_question(self):
        first = Image.new("RGB", (600, 260), "white")
        second = first.copy()
        ImageDraw.Draw(first).rectangle((90, 60, 130, 90), fill="black")
        ImageDraw.Draw(second).rectangle((90, 60, 210, 90), fill="black")
        entered, release = threading.Event(), threading.Event()
        state = {"image": first, "calls": 0, "first_cancel": None}
        class Reader:
            def read(self, image):
                return [TextBox(Rect(50, 30, 300, 30), "请选择正确答案？", .99)]
        class Client:
            def __init__(self, settings, key):
                pass
            def answer(self, image, cancel):
                state["calls"] += 1
                if state["calls"] == 1:
                    state["first_cancel"] = cancel
                    entered.set()
                    release.wait(5)
                    # 故意忽略取消，模拟不能及时取消的旧服务响应。
                    return AnswerResult("旧题", "OLD", True, True)
                return AnswerResult("新题", "NEW", True, True)
        with tempfile.TemporaryDirectory() as root:
            history = HistoryStore(Path(root))
            with patch("auto_answer.engine.OcrReader", Reader), patch("auto_answer.engine.monitors", return_value=[Rect(0, 0, 1920, 1080)]), patch("auto_answer.engine.grab", side_effect=lambda *args: state["image"].copy()), patch("auto_answer.engine.DeepSeekClient", Client):
                engine = Engine(history)
                answers = []
                engine.answer.connect(answers.append)
                try:
                    engine.configure(Settings(capture_rect=Rect(100, 100, 600, 260), scan_interval_ms=250, stable_ms=300, privacy_accepted=True), "test")
                    engine.start()
                    self.assertTrue(self.pump_until(entered.is_set))
                    for step in range(1, 13):
                        state["image"] = Image.blend(first, second, step / 12)
                        self.pump_until(lambda: False, seconds=.27)
                    self.assertTrue(self.pump_until(lambda: "NEW" in answers))
                    self.assertTrue(state["first_cancel"].is_set())
                    release.set()
                    self.pump_until(lambda: False, seconds=.4)
                    self.assertNotIn("OLD", answers)
                    self.assertTrue(all(item["answer"] == "NEW" for item in history.list()))
                finally:
                    release.set()
                    engine.close()
            history.close()

    def test_full_screen_no_question_invalidates_inflight_answer(self):
        first_search = True
        entered, release = threading.Event(), threading.Event()
        full_reads = {"value": 0, "gone": False}
        image = Image.new("RGB", (900, 600), "white")
        ImageDraw.Draw(image).rectangle((200, 180, 260, 210), fill="black")
        class Reader:
            def read(self, image):
                if full_reads['gone']:
                    full_reads['value'] += 1
                    return []
                x,y=image.info.get('origin',(0,0))
                return [TextBox(Rect(150-x, 140-y, 350, 25), "1、（判断题）请选择正确答案（ ）", .99),
                        TextBox(Rect(165-x, 210-y, 180, 25), "A. 第一项", .99),
                        TextBox(Rect(165-x, 270-y, 180, 25), "B. 第二项", .99)]
        def capture(rect,excluded):
            frame=Image.new('RGB',image.size,'white') if full_reads['gone'] else image.copy()
            original_crop=frame.crop
            def with_origin(bounds):
                cropped=original_crop(bounds);cropped.info['origin']=bounds[:2]
                return cropped
            frame.crop=with_origin
            return frame
        state = {"cancel": None}
        class Client:
            def __init__(self, settings, key):
                pass
            def answer(self, data, cancel):
                state["cancel"] = cancel
                entered.set()
                release.wait(20)
                return AnswerResult("旧题", "OLD", True, True)
        with tempfile.TemporaryDirectory() as root:
            history = HistoryStore(Path(root))
            with patch("auto_answer.engine.OcrReader", Reader), patch("auto_answer.engine.monitors", return_value=[Rect(0, 0, 900, 600)]), patch("auto_answer.engine.grab", side_effect=capture), patch("auto_answer.engine.DeepSeekClient", Client):
                engine = Engine(history)
                answers = []
                engine.answer.connect(answers.append)
                try:
                    engine.configure(Settings(mode="auto", scan_interval_ms=250, stable_ms=300, privacy_accepted=True), "test")
                    engine.start()
                    self.assertTrue(self.pump_until(entered.is_set))
                    full_reads['gone'] = True
                    self.assertTrue(self.pump_until(lambda: full_reads["value"] > 1, seconds=14))
                    self.assertTrue(state["cancel"].is_set())
                    release.set()
                    self.pump_until(lambda: False, seconds=.3)
                    self.assertNotIn("OLD", answers)
                    self.assertEqual(history.list(), [])
                finally:
                    release.set()
                    engine.close()
                    history.close()

    def test_pause_during_ocr_initialization_prevents_capture(self):
        entered, release = threading.Event(), threading.Event()
        captures = []
        class Reader:
            def __init__(self):
                entered.set()
                release.wait(3)
            def read(self, image):
                return []
        with tempfile.TemporaryDirectory() as root:
            history = HistoryStore(Path(root))
            with patch("auto_answer.engine.OcrReader", Reader), patch("auto_answer.engine.monitors", return_value=[Rect(0, 0, 1920, 1080)]), patch("auto_answer.engine.grab", side_effect=lambda *args: captures.append(True)):
                engine = Engine(history)
                try:
                    engine.configure(Settings(capture_rect=Rect(100, 100, 600, 260), privacy_accepted=True), "test")
                    engine.start()
                    self.assertTrue(entered.wait(2))
                    engine.pause()
                    release.set()
                    self.pump_until(lambda: False, seconds=.4)
                    self.assertEqual(captures, [])
                finally:
                    release.set()
                    engine.close()
            history.close()

    def test_late_result_after_pause_is_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            history = HistoryStore(Path(root))
            engine = Engine(history)
            answers = []
            engine.answer.connect(answers.append)
            engine.running.set()
            version, event = engine.epoch.token()
            engine.pause()
            engine._accept_result(version, AnswerResult("旧题", "OLD", True, True), b"test", 1)
            self.assertNotIn("OLD", answers)
            self.assertEqual(history.list(), [])
            engine.close()
            history.close()


if __name__ == "__main__":
    unittest.main()
