import io
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest

import httpx
from PIL import Image, ImageDraw

from auto_answer.api import ApiError, Cancelled, DeepSeekClient, parse_stream, payload
from auto_answer.detection import RequestEpoch, StableGate, TextBox, locate_question, normalized_text, same_frame, feature
from auto_answer.models import AnswerResult, Rect, Settings
from auto_answer.storage import HistoryStore, KeyStore, SettingsStore


def response_lines(answer=None, finish="stop", done=True):
    answer = answer or {"question": "1+1=?", "answer": "2", "certain": True, "is_question": True}
    output = [': heartbeat', 'data: ' + json.dumps({"choices": [{"delta": {"reasoning_content": "隐秘推理不可保存"}}]}, ensure_ascii=False)]
    text = json.dumps(answer, ensure_ascii=False)
    for part in [text[:12], text[12:27], text[27:]]:
        output.append('data: ' + json.dumps({"choices": [{"delta": {"content": part}}]}, ensure_ascii=False))
    output.append('data: ' + json.dumps({"choices": [{"delta": {}, "finish_reason": finish}]}))
    if done:
        output.append('data: [DONE]')
    return output


class SettingsTests(unittest.TestCase):
    def test_round_trip(self):
        settings = Settings(capture_rect=Rect(-500, 20, 300, 200))
        self.assertEqual(Settings.from_dict(settings.to_dict()), settings)

    def test_validation(self):
        for invalid in [Settings(font_size=1), Settings(effort="medium"), Settings(opacity=101), Settings(show_answer="yes"), Settings(answer_rect=Rect(0, 0, 0, 0))]:
            with self.assertRaises(ValueError):
                invalid.validate()

    def test_store_and_corrupt_recovery(self):
        with tempfile.TemporaryDirectory() as root:
            store = SettingsStore(Path(root))
            store.save(Settings(font_size=30))
            self.assertEqual(store.load().font_size, 30)
            self.assertNotIn("api_key", store.path.read_text())
            store.path.write_text("bad", encoding="utf-8")
            self.assertEqual(store.load().font_size, 22)
            self.assertTrue(store.warning)
            self.assertEqual(store.path.read_text(), "bad")

    def test_rect(self):
        self.assertTrue(Rect(-500, 0, 1000, 500).contains(Rect(-300, 30, 100, 100)))
        self.assertEqual(Rect(0, 0, 100, 100).intersection(Rect(50, 30, 100, 100)), Rect(50, 30, 50, 70))
        self.assertIsNone(Rect(0, 0, 100, 100).intersection(Rect(300, 0, 30, 30)))


class StorageTests(unittest.TestCase):
    def test_key_encrypted_roundtrip_clear(self):
        with tempfile.TemporaryDirectory() as root:
            store = KeyStore(Path(root))
            fake = "unit-test-not-real-api-key"
            store.save(fake)
            self.assertEqual(store.load(), fake)
            self.assertNotIn(fake.encode(), store.path.read_bytes())
            store.clear()
            self.assertEqual(store.load(), "")

    def test_history_limit_delete_clear(self):
        with tempfile.TemporaryDirectory() as root:
            store = HistoryStore(Path(root))
            result = AnswerResult("题目", "B", True, True)
            for i in range(3):
                store.add(b"fake-image-test", result, .5, 2)
            self.assertEqual(len(store.list()), 2)
            self.assertEqual(len(list(store.images.glob("*.png"))), 2)
            item = store.list()[0]
            path = store.image_path(item)
            store.delete(item["id"])
            self.assertFalse(path.exists())
            self.assertEqual(len(store.list()), 1)
            store.clear()
            self.assertEqual(store.list(), [])
            self.assertEqual(list(store.images.glob("*.png")), [])
            store.close()

    def test_uncertain_no_question_and_path_safety(self):
        with tempfile.TemporaryDirectory() as root:
            store = HistoryStore(Path(root))
            store.add(b"test", AnswerResult("", "", False, False), .1, 10)
            self.assertFalse(store.list())
            with self.assertRaises(ValueError):
                store.image_path({"image_name": "../private.png"})
            store.close()


class ApiTests(unittest.TestCase):
    def test_stream_only_final_content(self):
        answer = parse_stream(response_lines(), threading.Event(), time.monotonic() + 5)
        self.assertEqual(answer.answer, "2")
        self.assertNotIn("隐秘", answer.question + answer.answer)

    def test_incomplete_and_length_fail(self):
        for lines in (response_lines(finish="length"), response_lines(done=False), ['data: not json', 'data: [DONE]']):
            with self.assertRaises(ApiError):
                parse_stream(lines, threading.Event(), time.monotonic() + 5)

    def test_cancel_and_timeout(self):
        event = threading.Event()
        event.set()
        with self.assertRaises(Cancelled):
            parse_stream(response_lines(), event, time.monotonic() + 5)
        with self.assertRaises(ApiError):
            parse_stream(response_lines(), threading.Event(), time.monotonic() - 1)

    def test_official_payload(self):
        settings = Settings(effort="none")
        request = payload(settings, b"PNG")
        self.assertEqual(request["thinking"], {"type": "disabled"})
        self.assertEqual(request["reasoning_effort"], "none")
        self.assertTrue(request["stream"])
        self.assertEqual(request["messages"][1]["content"][1]["image_url"]["detail"], "original")

    def test_transport_sse_utf8(self):
        seen = []
        def handler(request):
            seen.append(json.loads(request.content))
            self.assertEqual(str(request.url), "https://api.deepseek.com/chat/completions")
            return httpx.Response(200, text="\n\n".join(response_lines()), headers={"Content-Type": "text/event-stream"})
        result = DeepSeekClient(Settings(), "mock", httpx.MockTransport(handler)).answer(b"test", threading.Event())
        self.assertEqual(result.answer, "2")
        self.assertEqual(len(seen), 1)

    def test_error_does_not_leak(self):
        for status in [400, 401, 402, 403, 500]:
            transport = httpx.MockTransport(lambda request: httpx.Response(status, text="secret-api-key-and-answer"))
            with self.assertRaises(ApiError) as raised:
                DeepSeekClient(Settings(), "mock", transport).answer(b"test", threading.Event())
            self.assertNotIn("secret", str(raised.exception))

    def test_retry_has_limit(self):
        calls = []
        def handler(request):
            calls.append(1)
            return httpx.Response(503)
        with self.assertRaises(ApiError):
            DeepSeekClient(Settings(), "mock", httpx.MockTransport(handler)).answer(b"test", threading.Event())
        self.assertEqual(len(calls), 2)

    def test_uncertain_and_empty(self):
        result = AnswerResult.from_dict({"question": "题干残缺", "answer": "", "certain": False, "is_question": True})
        self.assertIn("无法确定", result.display_answer)
        with self.assertRaises(ValueError):
            AnswerResult.from_dict({"question": "", "answer": "", "certain": True, "is_question": True})
        with self.assertRaises(ValueError):
            AnswerResult.from_dict({"question": "", "answer": "A", "certain": "true", "is_question": True})


class DetectionTests(unittest.TestCase):
    def test_locate_question(self):
        boxes = [TextBox(Rect(300, 200, 600, 28), "请选择下列正确答案？", .98),
                 TextBox(Rect(300, 250, 300, 25), "A. 选项一", .98),
                 TextBox(Rect(300, 295, 300, 25), "B. 选项二", .98)]
        rect = locate_question(boxes, 1920, 1080)
        self.assertIsNotNone(rect)
        self.assertTrue(rect.contains(boxes[0].rect))
        self.assertTrue(rect.contains(boxes[2].rect))
        self.assertIsNone(locate_question([TextBox(Rect(0, 0, 30, 20), "菜单", .9)], 1920, 1080))

    def test_stable_dedupe_and_small_number_change(self):
        image = Image.new("RGB", (700, 400), "white")
        ImageDraw.Draw(image).text((80, 80), "1 + 1 = ?", fill="black")
        gate = StableGate(1)
        self.assertEqual(gate.observe(image, 10), (True, False))
        self.assertEqual(gate.observe(image, 11.1), (False, True))
        self.assertTrue(gate.should_submit("same"))
        gate.mark_submitted("same")
        self.assertFalse(gate.should_submit("same"))
        altered = image.copy()
        ImageDraw.Draw(altered).rectangle((90, 80, 96, 91), fill="black")
        self.assertTrue(gate.observe(altered, 12)[0])
        self.assertTrue(gate.should_submit("same"))

    def test_4k_single_digit_change_not_lost_by_downsampling(self):
        from PIL import ImageFont
        font = ImageFont.truetype("C:/Windows/Fonts/msyh.ttc", 18)
        image = Image.new("RGB", (3840, 2160), "white")
        changed = image.copy()
        ImageDraw.Draw(image).text((100, 100), "Calculate 1+1", font=font, fill="black")
        ImageDraw.Draw(changed).text((100, 100), "Calculate 2+1", font=font, fill="black")
        self.assertFalse(same_frame(feature(image), feature(changed)))

    def test_accumulated_fade_invalidates_submitted_question(self):
        original = Image.new("RGB", (600, 260), "white")
        ImageDraw.Draw(original).rectangle((80, 60, 160, 95), fill="black")
        target = Image.new("RGB", (600, 260), "white")
        ImageDraw.Draw(target).rectangle((80, 60, 200, 95), fill="black")
        gate = StableGate(1)
        gate.observe(original, 10)
        gate.mark_submitted("same-text")
        changes = []
        for step in range(1, 13):
            changes.append(gate.observe(Image.blend(original, target, step / 12), 10 + step * .1)[0])
        self.assertTrue(any(changes), "渐变累计已换题但未触发失效")

    def test_epoch_old_response_rejected(self):
        epoch = RequestEpoch()
        version, cancel = epoch.token()
        epoch.invalidate()
        self.assertTrue(cancel.is_set())
        self.assertFalse(epoch.current(version))
        current, cancel = epoch.token()
        self.assertTrue(epoch.current(current))
        self.assertFalse(cancel.is_set())


if __name__ == "__main__":
    unittest.main()
