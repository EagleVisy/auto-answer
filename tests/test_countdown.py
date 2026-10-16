"""题干不动但上方倒计时变化的回归；不使用真实API。"""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtWidgets import QApplication

from auto_answer.detection import TextBox, StableGate
from auto_answer.dynamic_regions import CountdownTracker, timer_masks, mask_status_regions, timer_masks_still_safe
from auto_answer.engine import Engine
from auto_answer.models import AnswerResult, Rect, Settings
from auto_answer.storage import HistoryStore


class CountdownTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.font = ImageFont.truetype('C:/Windows/Fonts/msyh.ttc', 24)

    def wait_for(self, predicate, seconds=7):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return True
            time.sleep(.01)
        return False

    def test_plain_and_split_header_clock_masks(self):
        question = TextBox(Rect(30, 250, 650, 32), '1、（判断题）以下说法正确吗？（ ）', .99)
        pure = TextBox(Rect(40, 60, 150, 32), '01:57:22', .99)
        self.assertEqual(len(timer_masks([pure, question], 1000, 600)), 1)
        label = TextBox(Rect(40, 60, 110, 32), '剩余时间', .99)
        digits = TextBox(Rect(180, 60, 150, 32), '01：57：22', .99)
        masks = timer_masks([label, digits, question], 1000, 600)
        self.assertTrue(any(mask.contains(digits.rect) for mask in masks))
        self.assertTrue(timer_masks_still_safe([label, digits, question], masks))
        # 普通单数字不能只因上方出现就被遮罩；必须邻近倒计时标签。
        number = TextBox(Rect(180, 60, 70, 32), '119', .99)
        self.assertEqual(timer_masks([number, question], 1000, 600), [])
        self.assertTrue(timer_masks([label, number, question], 1000, 600))

    def test_header_clock_inline_and_question_numeric_constraints(self):
        inline = TextBox(Rect(20, 250, 800, 32), '1、剩余时间为01:20:30，求经过时间（ ）', .99)
        self.assertEqual(timer_masks([inline], 1000, 600), [])
        clock_in_question = TextBox(Rect(20, 310, 160, 32), '01:20:30', .99)
        self.assertEqual(timer_masks([inline, clock_in_question], 1000, 600), [])

    def test_only_explicit_header_timer_is_masked(self):
        timer = TextBox(Rect(20, 20, 280, 30), '剩余时间：1时57分22秒', .99)
        question = TextBox(Rect(20, 140, 600, 30), '1、判断题：以下说法正确吗？', .99)
        masks = timer_masks([timer, question], 900, 500)
        self.assertEqual(len(masks), 1)
        self.assertIsNone(masks[0].intersection(question.rect))
        question_timer = TextBox(Rect(20, 160, 500, 30), '题目：剩余时间为10秒，求时间变化？', .99)
        self.assertEqual(timer_masks([question_timer], 900, 500), [])
        self.assertEqual(timer_masks([timer], 900, 500), [])
        # 原计时位置在换页后变成一道真实题，必须撤销遮罩。
        moved = TextBox(Rect(20, 24, 250, 30), '1、计算1+1等于多少？', .99)
        self.assertFalse(timer_masks_still_safe([moved], masks))

    def test_timer_neighbor_number_and_shared_premise_not_masked(self):
        question = TextBox(Rect(40, 250, 700, 30), '1、（判断题）以下说法正确吗？（ ）', .99)
        label = TextBox(Rect(40, 60, 110, 30), '剩余时间', .99)
        clock = TextBox(Rect(180, 60, 150, 30), '00:20', .99)
        unrelated = TextBox(Rect(350, 60, 60, 30), '80', .99)
        masks = timer_masks([label, clock, unrelated, question],1000,600)
        self.assertFalse(any(mask.intersection(unrelated.rect) for mask in masks))
        premise = TextBox(Rect(500,100,700,30),'某列车从甲站发车，发车时刻见图',.99)
        given = TextBox(Rect(500,140,140,30),'01:20:30',.99)
        lower = TextBox(Rect(500,240,800,30),'1、（计算题）计算到达时刻，需90分钟（ ）',.99)
        self.assertEqual(timer_masks([premise,given,lower],1920,1080),[])

    def test_sidebar_keywords_do_not_move_question_top_above_clock(self):
        rows=[TextBox(Rect(20,20+i*40,310,30),'正确学习 阅读解答 题目通知',.99) for i in range(10)]
        timer=TextBox(Rect(620,60,150,32),'01:57:22',.99)
        question=[TextBox(Rect(620,240,700,32),'12、（单选题）下列结论正确（ ）',.99),TextBox(Rect(650,400,250,32),'A. 第一项',.99),TextBox(Rect(650,550,250,32),'B. 第二项',.99)]
        masks=timer_masks([*rows,timer,*question],1920,1080)
        self.assertTrue(any(mask.contains(timer.rect) for mask in masks))

    def test_bare_numeric_timer_needs_monotonic_evidence(self):
        question = TextBox(Rect(40, 250, 700, 30), '1、（判断题）2+1=3。（ ）', .99)
        tracker = CountdownTracker()
        def rows(number):
            return [TextBox(Rect(40, 60, 70, 30), str(number), .99), question]
        self.assertEqual(tracker.update(rows(120), 1000, 600, 10), [])
        self.assertEqual(tracker.update(rows(119), 1000, 600, 11), [])
        self.assertTrue(tracker.update(rows(118), 1000, 600, 12))
        self.assertTrue(tracker.update(rows(118), 1000, 600, 12.5))
        # 题干位置的数字即使递减也不允许直接忽略。
        tracker = CountdownTracker()
        for i in range(4):
            inside = TextBox(Rect(100, 300, 70, 30), str(120-i), .99)
            self.assertEqual(tracker.update([question, inside], 1000, 600, 10+i), [])

    def test_ticking_timer_does_not_prevent_answer_or_cancel_it(self):
        self._run_timer_engine('labelled')

    def test_ticking_bare_number_engine_stabilizes(self):
        self._run_timer_engine('bare')

    def test_split_label_clock_engine_stabilizes(self):
        self._run_timer_engine('split')

    def _run_timer_engine(self, mode):
        state = {'captures': 0, 'calls': 0, 'digit': 1, 'cancel': None, 'started': time.monotonic()}
        def timer_text():
            remaining = 120 - int(time.monotonic() - state['started'])
            if mode == 'bare':
                return str(remaining)
            if mode == 'split':
                return f'00:{remaining // 60:02d}:{remaining % 60:02d}'
            return f'剩余时间：{remaining // 60}分{remaining % 60:02d}秒'
        def capture(*args):
            state['captures'] += 1
            image = Image.new('RGB', (900, 500), 'white')
            draw = ImageDraw.Draw(image)
            if mode == 'split':
                draw.text((24, 24), '剩余时间', font=self.font, fill='black')
                draw.text((180, 24), timer_text(), font=self.font, fill='black')
            else:
                draw.text((24, 24), timer_text(), font=self.font, fill='black')
            draw.text((24, 150), f"1、判断题：{state['digit']} + 1 = 2。（ ）", font=self.font, fill='black')
            draw.text((30, 300), 'A. 正确', font=self.font, fill='black')
            draw.text((30, 390), 'B. 错误', font=self.font, fill='black')
            return image
        class Reader:
            def read(self, image):
                header = [TextBox(Rect(24, 24, 100 if mode == 'bare' else 320, 34), timer_text(), .99)]
                if mode == 'split':
                    header = [TextBox(Rect(24, 24, 120, 34), '剩余时间', .99), TextBox(Rect(180, 24, 180, 34), timer_text(), .99)]
                return [*header,
                        TextBox(Rect(24, 150, 600, 34), f"1、判断题：{state['digit']} + 1 = 2。（ ）", .99),
                        TextBox(Rect(30, 300, 180, 34), 'A. 正确', .99),
                        TextBox(Rect(30, 390, 180, 34), 'B. 错误', .99)]
        class Client:
            def __init__(self, settings, key): pass
            def answer(self, image, cancel):
                state['calls'] += 1
                state['cancel'] = cancel
                if cancel.wait(.8):
                    raise AssertionError('倒计时变化取消了同一题请求')
                return AnswerResult('离线判断题', f"ANSWER{state['calls']}", True, True)
        with tempfile.TemporaryDirectory() as folder:
            history = HistoryStore(Path(folder))
            with patch('auto_answer.engine.OcrReader', Reader), patch('auto_answer.engine.monitors', return_value=[Rect(0, 0, 1920, 1080)]), patch('auto_answer.engine.grab', side_effect=capture), patch('auto_answer.engine.DeepSeekClient', Client):
                engine = Engine(history)
                answers = []
                engine.answer.connect(answers.append)
                try:
                    engine.configure(Settings(capture_rect=Rect(100, 100, 900, 500), scan_interval_ms=250, stable_ms=1000, privacy_accepted=True), 'offline-placeholder')
                    engine.start()
                    self.assertTrue(self.wait_for(lambda: 'ANSWER1' in answers), '倒计时每帧变化时一直等待稳定，未作答')
                    self.wait_for(lambda: False, seconds=1.2)
                    self.assertEqual(state['calls'], 1, '仅倒计时变化造成重复请求')
                    self.assertFalse(state['cancel'].is_set(), '题目未变仍取消成功请求')
                    state['digit'] = 2
                    self.assertTrue(self.wait_for(lambda: 'ANSWER2' in answers), '屏蔽倒计时后漏掉题干数字变化')
                    self.assertEqual(len(history.list()), 2)
                finally:
                    engine.close()
                    history.close()


if __name__ == '__main__':
    unittest.main()
