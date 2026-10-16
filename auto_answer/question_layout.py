"""题号/题型/连续选项+空间关系的本地题目定位；不调用AI。"""
from __future__ import annotations

from dataclasses import dataclass
import re
import unicodedata

from .models import Rect

NUMBER = re.compile(r'^\s*(?:(?:第\s*\d{1,4}\s*题)|(?:[（(]\s*\d{1,4}\s*[）)]\s*[.、:：]?)|(?:\d{1,4}\s*[、．.）)](?!\d)))')
TYPE = re.compile(r'[（(【\[]?\s*(?:单项选择题|多项选择题|单选题|多选题|判断题|填空题|简答题|选择题|计算题|证明题)\s*[）)】\]]?')
BLANK = re.compile(r'[（(]\s*[）)]|_{2,}|[□]{2,}')
QUESTION_WORD = re.compile(r'[?？]|下列|以下|哪[个项]|求|计算|填空|证明|解答|简述|多少|正确的是|错误的是|what|which|solve|calculate|choose', re.I)
UI_TEXT = re.compile(r'^(?:下一题|上一题|提交(?:试卷|答案)?|操作题|素养题|转跳|跳转|题号|剩余时间|考试时间|倒计时|学习平台|新闻通知|系列教材|师资培训|大赛真题讲解|返回|目录|正确显示|错误提示|选择菜单|题目范围|答案位置)')


def clean(text: str) -> str:
    return unicodedata.normalize('NFKC', text).strip()


def option_label(text: str) -> str | None:
    value = clean(text).replace('‧','.').replace('·','.').replace('•','.')
    # 单选圆圈会被OCR当成O/0（如“OA 正确”“ＯB 错误”），只在随后有选项字母时去掉。
    value = re.sub(r'^(?:[○〇◯●◉□☐✓✔]|[O0o](?=\s*[A-Ha-h](?:\s|[.、:：]|$)))\s*', '', value)
    # A-H必须是独立选项前缀，不能把'choose/calculate'这样的题型语句当C选项。
    value = re.sub(r'^[)）]\s*(?=[A-H])', '', value)
    match = re.match(r'^\s*(?:[（(]([A-Ha-h])[）)]|([A-Ha-h])(?:[.、．:：)）]|\s|$)|([A-H])(?=[\u4e00-\u9fff\d]))', value)
    return (match.group(1) or match.group(2) or match.group(3)).upper() if match else None


def is_question_start(text: str) -> bool:
    value = clean(text)
    return not UI_TEXT.match(value) and bool(NUMBER.match(value) or TYPE.search(value))


def _same_line(a: Rect, b: Rect) -> bool:
    return abs((a.y+a.height/2)-(b.y+b.height/2)) <= max(a.height,b.height)*.55


def _bounds(boxes, width: int, height: int, padding: int = 18) -> Rect:
    left=max(0,min(b.rect.x for b in boxes)-padding)
    top=max(0,min(b.rect.y for b in boxes)-padding)
    # 空白作答括号/句末标点常被OCR漏掉，右侧保留额外文字余量。
    right=min(width,max(b.rect.x+b.rect.width for b in boxes)+max(60,int(max(b.rect.height for b in boxes)*2.5)))
    bottom=min(height,max(b.rect.y+b.rect.height for b in boxes)+padding)
    return Rect(left,top,max(24,right-left),max(24,bottom-top))


@dataclass(frozen=True)
class QuestionCandidate:
    rect: Rect
    score: float
    signals: tuple[str,...]
    option_count: int


def question_candidates(boxes, width: int, height: int) -> list[QuestionCandidate]:
    usable=[b for b in boxes if b.confidence>=.4 and b.text.strip() and not UI_TEXT.match(clean(b.text))]
    usable.sort(key=lambda b:(b.rect.y,b.rect.x))
    candidates=[]
    for seed in usable:
        value=clean(seed.text)
        if option_label(value):
            continue
        has_number=bool(NUMBER.match(value))
        has_type=bool(TYPE.search(value))
        has_blank=bool(BLANK.search(value))
        has_question=bool(QUESTION_WORD.search(value))
        if not (has_number or has_type or has_blank or has_question):
            continue
        # 同一行拆开识别的“1、”“（判断题）”“题干”先合并；不得跨入侧栏。
        row=[seed]
        for b in usable:
            if b is seed or not _same_line(seed.rect,b.rect):continue
            gap=b.rect.x-(seed.rect.x+seed.rect.width)
            if -15<=gap<=max(160,seed.rect.height*6):row.append(b)
        row=sorted(row,key=lambda b:b.rect.x)
        row_text=' '.join(clean(b.text) for b in row)
        has_number=bool(NUMBER.match(row_text))
        has_type=bool(TYPE.search(row_text))
        has_blank=bool(BLANK.search(row_text))
        has_question=bool(QUESTION_WORD.search(row_text))
        left=min(b.rect.x for b in row)
        right=max(b.rect.x+b.rect.width for b in row)
        top=min(b.rect.y for b in row)
        row_height=max(b.rect.height for b in row)
        column_left=left-max(70,row_height*3)
        column_right=min(width,right+max(180,width*.16))
        limit=height
        # 题号相近且位于同一列的下一题是硬边界，而不是接着聚合。
        for other in usable:
            if other in row or other.rect.y<=top+row_height*.8:continue
            if not is_question_start(other.text):continue
            other_text=clean(other.text)
            inner_condition=bool(re.match(r'^\s*(?:\d{1,2}[)）]|[（(]\d{1,2}[)）])',other_text)) and not TYPE.search(other_text)
            if inner_condition:
                continue
            # 真正下一题允许一定缩进；同列范围仍限定，不能借选项换行并入下一题。
            if abs(other.rect.x-left)<=max(180,row_height*6):
                limit=min(limit,other.rect.y-6)
        option_rows=[]
        for b in usable:
            if b.rect.y<top+row_height*.6 or b.rect.y>=limit:continue
            if not column_left<=b.rect.x<=column_right:continue
            label=option_label(b.text)
            if label:option_rows.append((label,b))
        # 重叠重复字母不能中止整组选项；先消重再按真实行聚类排序。
        deduped=[]
        for label,b in option_rows:
            if any(label==old_label and _same_line(b.rect,old.rect) and (abs(b.rect.x-old.rect.x)<max(30,row_height*2) or (b.rect.intersection(old.rect) is not None and b.rect.intersection(old.rect).width>min(b.rect.width,old.rect.width)*.5)) for old_label,old in deduped):
                continue
            deduped.append((label,b))
        rows=[]
        for pair in sorted(deduped,key=lambda p:(p[1].rect.y,p[1].rect.x)):
            target=next((line for line in rows if _same_line(line[0][1].rect,pair[1].rect)),None)
            if target is None:rows.append([pair])
            else:target.append(pair)
        option_rows=[pair for line in rows for pair in sorted(line,key=lambda p:p[1].rect.x)]
        sequence=[]
        expected='A'
        for letter,b in option_rows:
            if letter==expected:
                sequence.append(b)
                expected=chr(ord(expected)+1)
            elif sequence and letter=='A':
                break
        count=len(sequence)
        # 仅有题号或关键词不足以触发；必须有题型、空白括号、连续选项等相互印证。
        if count<2 and not ((has_number and (has_type or has_blank)) or (has_type and (has_question or has_blank))):
            continue
        selected=list(row)
        if count>=2 and not has_number:
            # “已知...”前置条件不能因为没有问号而被漏掉；仅向上追同列近邻正文。
            before=sorted([b for b in usable if b.rect.y<top and abs(b.rect.x-left)<=max(90,row_height*3)],key=lambda b:b.rect.y,reverse=True)
            cursor=top
            for b in before[:4]:
                if cursor-(b.rect.y+b.rect.height)>max(85,row_height*3):break
                if is_question_start(b.text):break
                if re.search(r'已知|设|给定|满足|若|条件|图中|根据|for|given|suppose',clean(b.text),re.I) or cursor<top:
                    selected.append(b);cursor=b.rect.y
                    continue
                break
        if count>=2:
            first_y=min(b.rect.y for b in sequence)
            end_y=max(b.rect.y+b.rect.height for b in sequence)
            option_right=max(b.rect.x+b.rect.width for b in sequence)
            column_right=max(column_right,option_right+max(100,row_height*4))
            # 保留题干行、题型标题与首选项之间的正文。
            for b in usable:
                if top<=b.rect.y<first_y and column_left<=b.rect.x<=column_right:
                    if b not in selected:selected.append(b)
            for b in sequence:
                if b not in selected:selected.append(b)
                # 字母单独成框/选项换行，用同列邻近文本补全。
                for other in usable:
                    if other in selected or option_label(other.text):continue
                    if not column_left<=other.rect.x<=column_right:continue
                    same=_same_line(b.rect,other.rect) and other.rect.x>=b.rect.x-10
                    wrap=0<=other.rect.y-(b.rect.y+b.rect.height)<=max(70,row_height*2.5) and other.rect.x>=b.rect.x-12
                    if same or wrap:
                        if other.rect.y<limit and other.rect.y<=end_y+max(70,row_height*2.5):
                            selected.append(other)
        else:
            # 填空/简答题按正文行延伸，不将大面积空白和按钮包括进去。
            last_bottom=max(b.rect.y+b.rect.height for b in selected)
            for b in usable:
                if b in selected or b.rect.y<top or b.rect.y>=limit:continue
                if not column_left<=b.rect.x<=column_right:continue
                if b.rect.y-last_bottom>max(90,row_height*3):break
                selected.append(b);last_bottom=max(last_bottom,b.rect.y+b.rect.height)
        # 有连续选项时，噪声词数量不再能超过结构分。
        signals=[]
        score=0
        for flag,name,points in [(has_number,'题号',28),(has_type,'题型',32),(has_blank,'空白括号',18),(has_question,'题干语义',8)]:
            if flag:signals.append(name);score+=points
        if count>=2:signals.append('连续选项');score+=55+count*12
        score+=min(sum(len(b.text) for b in selected),400)/80
        rect=_bounds(selected,width,height)
        if rect.width<80 or rect.height<24:continue
        # 页面边缘密集的短菜单候选比完整题目略降分；不能仅凭中心位置判定。
        if rect.x<width*.12 and rect.width<width*.22:score-=18
        candidate=QuestionCandidate(rect,score,tuple(signals),count)
        if not any(c.rect==rect for c in candidates):candidates.append(candidate)
    return sorted(candidates,key=lambda c:(-c.score,c.rect.y,c.rect.x))


def locate_structured_question(boxes, width: int, height: int) -> Rect | None:
    candidates=question_candidates(boxes,width,height)
    return candidates[0].rect if candidates else None
