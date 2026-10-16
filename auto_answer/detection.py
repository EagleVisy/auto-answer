from __future__ import annotations

from dataclasses import dataclass
import os
import re
import threading
import time

import numpy as np
from PIL import Image

from .models import Rect

_OCR_INIT_LOCK = threading.Lock()


@dataclass(frozen=True)
class TextBox:
    rect: Rect
    text: str
    confidence: float


class OcrReader:
    def __init__(self):
        # 只在后台线程创建；限制推理线程，避免占满桌面 CPU。
        from rapidocr_onnxruntime import RapidOCR
        # 1.2.3 的SessionOptions不支持线程kwargs，局部工厂设置（不改第三方磁盘代码）。
        import rapidocr_onnxruntime.utils as ocr_utils
        def limited_options():
            options = original_options()
            options.intra_op_num_threads = min(4, max(1, os.cpu_count() or 2))
            options.inter_op_num_threads = 1
            return options
        with _OCR_INIT_LOCK:
            original_options = ocr_utils.SessionOptions
            ocr_utils.SessionOptions = limited_options
            try:
                self.reader = RapidOCR(det_model_path=None, det_limit_side_len=1280, det_limit_type="max")
            finally:
                ocr_utils.SessionOptions = original_options
        # 和OCR一起后台预热中文查询分词，正式请求不承担冷初始化。
        from .search import make_query
        make_query("二分查找算法")

    def read_screen(self, image: Image.Image) -> list[TextBox]:
        """全屏先粗定位，再原分辨率精读候选；弱候选时补重叠分块。"""
        from .question_layout import question_candidates, option_label, is_question_start
        coarse = self.read(image)
        candidates = question_candidates(coarse, image.width, image.height)
        regions = []
        if candidates and (candidates[0].option_count >= 4 and not any('多选' in box.text or '多项选择' in box.text for box in coarse if candidates[0].rect.contains(box.rect)) or (candidates[0].option_count >= 2 and any('判断题' in box.text for box in coarse if candidates[0].rect.contains(box.rect)))) and any(signal in candidates[0].signals for signal in ('题号','题型')):
            # 完整的强结构候选无需对侧栏弱候选重复OCR，避免4K扫描拖到数十秒。
            return coarse
        if candidates:
            for candidate in candidates[:1]:
                rect = candidate.rect
                x=max(0,rect.x-60);y=max(0,rect.y-60)
                right=min(image.width,rect.x+rect.width+100);bottom=min(image.height,rect.y+rect.height+100)
                if candidate.option_count < 4 or any('多选' in box.text for box in coarse if rect.contains(box.rect)):
                    # 粗读可能漏C/D：同列精读延伸到下一题/屏幕底部，不能只放大残缺bbox。
                    boundary=min((box.rect.y-8 for box in coarse if box.rect.y>rect.y+80 and is_question_start(box.text) and abs(box.rect.x-rect.x)<200),default=image.height)
                    bottom=min(image.height,max(bottom,boundary))
                regions.append(Rect(x,y,right-x,bottom-y))
        elif max(image.size)>2200:
            # 4K小字在整屏缩图里可能连题号都丢；最多6块，不需要云端定位。
            tile_width,tile_height=1800,1300
            xs=list(range(0,image.width,1500));ys=list(range(0,image.height,1100))
            for y in ys:
                for x in xs:
                    if len(regions)>=6:break
                    if image.width-x<200 or image.height-y<150:continue
                    regions.append(Rect(x,y,min(tile_width,image.width-x),min(tile_height,image.height-y)))
        result=list(coarse)
        for rect in regions:
            crop=image.crop((rect.x,rect.y,rect.x+rect.width,rect.y+rect.height))
            for box in self.read(crop):
                box=TextBox(Rect(box.rect.x+rect.x,box.rect.y+rect.y,box.rect.width,box.rect.height),box.text,box.confidence)
                duplicates=[]
                for index,previous in enumerate(result):
                    overlap=previous.rect.intersection(box.rect)
                    if overlap and overlap.width*overlap.height/min(previous.rect.width*previous.rect.height,box.rect.width*box.rect.height)>.65:
                        duplicates.append(index)
                if duplicates:
                    # 精读若只剩选项内容，不能用高置信度覆盖粗读识别到的A/B字母。
                    group=[box,*[result[index] for index in duplicates]]
                    protected=[b for b in group if option_label(b.text) or is_question_start(b.text)]
                    # 结构字母和选项正文各自保留；不可用窄A框替代整段选项文字。
                    best=max(group,key=lambda b:(len(b.text),b.confidence))
                    for index in reversed(duplicates):result.pop(index)
                    result.append(best)
                    for anchor in protected:
                        if anchor not in result and (option_label(anchor.text) != option_label(best.text) or anchor.text != best.text):
                            result.append(anchor)
                else:result.append(box)
        return sorted(result,key=lambda box:(box.rect.y//12,box.rect.x))

    def read(self, image: Image.Image) -> list[TextBox]:
        scale = min(1.0, 1800 / max(image.size))
        reduced = image.resize((round(image.width * scale), round(image.height * scale))) if scale < 1 else image
        results, _ = self.reader(np.asarray(reduced))
        boxes = []
        for points, text, confidence in results or []:
            confidence = float(confidence)  # RapidOCR 1.2.3 返回字符串置信度。
            if confidence < .45 or not text.strip():
                continue
            left = round(min(p[0] for p in points) / scale)
            top = round(min(p[1] for p in points) / scale)
            right = round(max(p[0] for p in points) / scale)
            bottom = round(max(p[1] for p in points) / scale)
            boxes.append(TextBox(Rect(left, top, max(1, right - left), max(1, bottom - top)), text.strip(), float(confidence)))
        return sorted(boxes, key=lambda box: (box.rect.y // 12, box.rect.x))


QUESTION = re.compile(r"[?？]|选择|下列|以下|正确|错误|求|计算|填空|证明|解答|阅读|写出|简述|多少|哪[个项]|等于|单选|多选|判断|题目|问题|what|which|solve|calculate|choose", re.I)
OPTION = re.compile(r"^\s*[A-HＡ-Ｈ][.、:：)）\s]|^[A-HＡ-Ｈ]$")


def normalized_text(boxes: list[TextBox]) -> str:
    return re.sub(r"\s+", "", "\n".join(box.text for box in boxes)).casefold()


def looks_like_question(boxes: list[TextBox], manual: bool = False) -> bool:
    text = normalized_text(boxes)
    if len(text) < 4:
        return False
    return bool(QUESTION.search(text) or any(OPTION.match(box.text) for box in boxes)
                or (manual and len(text) >= 6) or re.search(r"\d\s*[+×÷=]\s*\d", text))


def locate_question(boxes: list[TextBox], width: int, height: int) -> Rect | None:
    from .question_layout import locate_structured_question
    return locate_structured_question(boxes, width, height)


def feature(image: Image.Image) -> np.ndarray:
    # 保留原始像素，避免 4K 中的小数字被缩略图平均后漏检。
    # uint8 特征减少常驻内存；比较时才提升精度以避免减法溢出。
    return np.asarray(image.convert("L"), dtype=np.uint8)


def same_frame(left: np.ndarray | None, right: np.ndarray) -> bool:
    if left is None or left.shape != right.shape:
        return False
    difference = np.abs(left.astype(np.int16) - right.astype(np.int16))
    return bool(np.max(difference) < 26 and np.mean(difference) < 1.2)


class StableGate:
    def __init__(self, stable_seconds: float):
        self.stable_seconds = stable_seconds
        self.last: np.ndarray | None = None
        self.since = 0.0
        self.last_identity: str | None = None
        self.submitted_frame: np.ndarray | None = None

    def observe(self, image: Image.Image, now: float | None = None) -> tuple[bool, bool]:
        now = time.monotonic() if now is None else now
        current = feature(image)
        changed = not same_frame(self.last, current)
        if self.submitted_frame is not None and not same_frame(self.submitted_frame, current):
            changed = True  # 累计渐变也必须使原题失效。
        if changed:
            self.since = now
        self.last = current
        return changed, now - self.since >= self.stable_seconds

    def should_submit(self, identity: str) -> bool:
        # 同文 OCR 的单像素变化仍可能是数学公式/选项变动，不忽略画面变化。
        return identity != self.last_identity or not same_frame(self.submitted_frame, self.last)

    def mark_submitted(self, identity: str) -> None:
        self.last_identity = identity
        self.submitted_frame = self.last.copy() if self.last is not None else None


class RequestEpoch:
    """独立的请求代数，取消与最终发布都必须检查它。"""
    def __init__(self):
        self.lock = threading.RLock()
        self.value = 0
        self.cancel = threading.Event()

    def invalidate(self) -> int:
        with self.lock:
            self.cancel.set()
            self.cancel = threading.Event()
            self.value += 1
            return self.value

    def token(self) -> tuple[int, threading.Event]:
        with self.lock:
            return self.value, self.cancel

    def current(self, version: int) -> bool:
        with self.lock:
            return self.value == version and not self.cancel.is_set()
