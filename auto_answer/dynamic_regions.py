"""独立状态计时排除：带标签、分离标签、纯时钟、可核验裸数字递减。"""
from __future__ import annotations

import re
import time
import unicodedata
from PIL import Image, ImageDraw

from .detection import QUESTION, OPTION, TextBox
from .models import Rect
from .question_layout import is_question_start, locate_structured_question

_LABEL = re.compile(r'^(?:剩余(?:答题|考试)?时间|答题倒计时|考试剩余时间|倒计时|剩余时长|剩余|time\s*remaining|remaining\s*time)\s*[:：]?\s*$', re.I)
_VALUE = re.compile(r'^(?:\d{1,3}\s*[:：]\s*\d{2}(?:\s*[:：]\s*\d{2})?|\d+(?:\s*(?:时|小时|分|分钟|秒|h|m|s)\s*\d*)+)\s*$', re.I)
_SECONDS = re.compile(r'^\d{1,5}\s*(?:秒|s)?$')


def _clean(value: str) -> str:
    return unicodedata.normalize('NFKC', value).strip()


def timer_status_line(box: TextBox) -> bool:
    value = _clean(box.text)
    if _LABEL.fullmatch(value) or _VALUE.fullmatch(value):
        return True
    for prefix in ('剩余答题时间','剩余考试时间','考试剩余时间','答题倒计时','剩余时间','剩余时长','倒计时','time remaining','remaining time'):
        if value.lower().startswith(prefix):
            tail = value[len(prefix):].lstrip(' :：')
            return bool(_VALUE.fullmatch(tail) or _SECONDS.fullmatch(tail))
    return bool(_LABEL.fullmatch(value) or _VALUE.fullmatch(value))


def _question_top(boxes: list[TextBox]) -> int | None:
    # 侧栏关键词不能决定题目顶部：有完整结构候选时以它为主。
    if boxes:
        width=max(box.rect.x+box.rect.width for box in boxes)+30
        height=max(box.rect.y+box.rect.height for box in boxes)+30
        structured=locate_structured_question(boxes,width,height)
        if structured is not None:
            rows=[box.rect.y for box in boxes if structured.contains(box.rect) and not timer_status_line(box)]
            if rows:return min(rows)
    anchors = [box.rect.y for box in boxes if not timer_status_line(box)
               and (is_question_start(box.text) or QUESTION.search(box.text) or OPTION.match(box.text))]
    return min(anchors) if anchors else None


def _same_row(a: Rect, b: Rect) -> bool:
    return abs(a.y+a.height/2-b.y-b.height/2) <= max(a.height,b.height)*.8


def _padding(rect: Rect, width: int, height: int) -> Rect:
    left=max(0,rect.x-12);top=max(0,rect.y-8)
    right=min(width,rect.x+rect.width+max(20,rect.height*3));bottom=min(height,rect.y+rect.height+8)
    return Rect(left,top,right-left,bottom-top)


def timer_masks(boxes: list[TextBox], width: int, height: int) -> list[Rect]:
    question_top = _question_top(boxes)
    if question_top is None:
        return []
    labels = [box for box in boxes if _LABEL.fullmatch(_clean(box.text))]
    associated = set()
    for label in labels:
        values = [box for box in boxes if box is not label and _same_row(box.rect,label.rect)
                  and box.rect.x >= label.rect.x+label.rect.width-15
                  and box.rect.x-(label.rect.x+label.rect.width) <= max(220,label.rect.height*7)
                  and (_VALUE.fullmatch(_clean(box.text)) or _SECONDS.fullmatch(_clean(box.text)))]
        if values:
            # 有完整时钟时优先它，否则只关联最近一个秒数，不把后面的数字一起吞掉。
            nearest=min(values,key=lambda box:(not bool(_VALUE.fullmatch(_clean(box.text))),box.rect.x))
            associated.add(nearest.rect)
    candidates = []
    for box in boxes:
        value = _clean(box.text)
        rect = box.rect
        # 只处理明确在题干之前的独立状态行，不忽略题干中的时间条件。
        if rect.y+rect.height+4 >= question_top:
            continue
        confirmed = timer_status_line(box)
        if not confirmed and _SECONDS.fullmatch(value):
            confirmed = rect in associated
        if _VALUE.fullmatch(value) and not any(_same_row(rect,label.rect) for label in labels):
            # 独立时钟前紧邻公共题材正文时，它可能是题目条件，不是计时状态。
            preceding=[other for other in boxes if other.rect.y+other.rect.height<=rect.y and rect.y-(other.rect.y+other.rect.height)<=max(90,rect.height*3) and abs(other.rect.x-rect.x)<max(120,rect.height*4) and len(_clean(other.text))>=6 and not timer_status_line(other)]
            if preceding:
                confirmed=False
        if confirmed:
            candidates.append(rect)
    # 标签和值被拆成多个框，局部行拼接后再次识别；只把相邻计时文本当整体。
    for label in labels:
        if label.rect.y+label.rect.height+4 >= question_top:
            continue
        peers = [label,*[box for box in boxes if box.rect in associated and _same_row(label.rect,box.rect)]]
        peers.sort(key=lambda box:box.rect.x)
        combined = ' '.join(_clean(box.text) for box in peers)
        synthetic = TextBox(label.rect,combined,.99)
        if timer_status_line(synthetic):
            candidates.extend(box.rect for box in peers)
    masks=[]
    for rect in candidates:
        padded=_padding(rect,width,height)
        if padded.y+padded.height>=question_top:
            padded=Rect(padded.x,padded.y,padded.width,max(1,question_top-padded.y-2))
        if not any(existing.contains(rect) for existing in masks):
            masks.append(padded)
    # padding不得把同排其他正文/数字白掉。
    safe=[]
    for mask in masks:
        if any(mask.intersection(box.rect) and box.rect not in candidates for box in boxes):
            core=next((rect for rect in candidates if mask.contains(rect)),None)
            if core is None:continue
            mask=Rect(max(0,core.x-2),max(0,core.y-2),min(width-core.x+2,core.width+4),min(height-core.y+2,core.height+4))
            if any(mask.intersection(box.rect) and box.rect not in candidates for box in boxes):continue
        safe.append(mask)
    return safe


def mask_status_regions(image: Image.Image, masks: list[Rect]) -> Image.Image:
    if not masks:
        return image
    result=image.copy()
    painter=ImageDraw.Draw(result)
    for rect in masks:
        painter.rectangle((rect.x,rect.y,rect.x+rect.width-1,rect.y+rect.height-1),fill='white')
    return result


def timer_masks_still_safe(boxes: list[TextBox], masks: list[Rect], confirmed_numbers: list[Rect] | None = None) -> bool:
    labels=[box for box in boxes if _LABEL.fullmatch(_clean(box.text))]
    question_top=_question_top(boxes)
    for box in boxes:
        if timer_status_line(box) and question_top is not None and box.rect.y+box.rect.height+4<question_top:
            continue
        if _SECONDS.fullmatch(_clean(box.text)) and question_top is not None and box.rect.y+box.rect.height+4<question_top and any(rect.contains(box.rect) for rect in (confirmed_numbers or [])):
            continue
        if _SECONDS.fullmatch(_clean(box.text)) and question_top is not None and box.rect.y+box.rect.height+4<question_top and any(_same_row(box.rect,label.rect) and -15<=box.rect.x-(label.rect.x+label.rect.width)<=max(220,label.rect.height*7) for label in labels):
            continue
        if any(rect.intersection(box.rect) for rect in masks):
            return False
    return True


class CountdownTracker:
    """裸数字必须连续递减且在结构题目之外，不能仅凭数字抹掉一道题。"""
    def __init__(self):
        self.samples = []
        self.confirmed = []

    def update(self, boxes: list[TextBox], width: int, height: int, now: float | None = None) -> list[Rect]:
        now=time.monotonic() if now is None else now
        masks=timer_masks(boxes,width,height)
        question=locate_structured_question(boxes,width,height)
        question_top=_question_top(boxes)
        if question_top is None:
            self.samples=[];self.confirmed=[]
            return []
        new_samples=[]
        new_confirmed=[]
        for box in boxes:
            value=_clean(box.text)
            if not _SECONDS.fullmatch(value):continue
            if box.rect.y+box.rect.height+6>=question_top:continue
            if question and question.intersection(box.rect):continue
            number=int(re.match(r'\d+',value).group())
            # 页眉数字自身不能在第一帧被忽略，要有连续减小证据。
            previous=next((row for row in self.samples if abs(row[0].x-box.rect.x)<=25 and abs(row[0].y-box.rect.y)<=16),None)
            streak=0
            if previous:
                old_rect,old_value,old_time,old_streak=previous
                elapsed=now-old_time
                difference=old_value-number
                if .15<=elapsed<=10 and 0<difference<=max(3,int(elapsed)+2):
                    streak=old_streak+1
                elif difference==0:
                    streak=old_streak
            new_samples.append((box.rect,number,now,streak))
            if streak>=2:
                padded=_padding(box.rect,width,height)
                if any(padded.intersection(other.rect) and other is not box for other in boxes):
                    padded=Rect(max(0,box.rect.x-2),max(0,box.rect.y-2),box.rect.width+4,box.rect.height+4)
                if not any(padded.intersection(other.rect) and other is not box for other in boxes):
                    new_confirmed.append(padded)
        self.samples=new_samples
        self.confirmed=new_confirmed
        return masks+new_confirmed
