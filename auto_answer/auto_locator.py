"""自动模式独立候选跟踪/完整性/稳定及超时锁定，不改手动行为。"""
from __future__ import annotations

from dataclasses import dataclass
import re
import time

import cv2
import numpy as np
from PIL import Image

from .models import Rect
from .question_layout import question_candidates, is_question_start, option_label, clean, TYPE


@dataclass(frozen=True)
class LocationProposal:
    rect: Rect
    identity: str
    complete: bool
    reason: str
    option_count: int
    needs_tail_probe: bool = False
    functions_only: bool = False


def _intersects_vertical(a: Rect, b: Rect) -> bool:
    return max(a.x,b.x)<min(a.x+a.width,b.x+b.width)


def _content_panel(image: Image.Image, seed: Rect) -> Rect | None:
    """识别白/浅色内容卡片边界；整屏白底不假定整个屏幕就是一题。"""
    scale=min(1,700/image.width)
    array=np.asarray(image.resize((round(image.width*scale),round(image.height*scale))).convert('RGB'))
    light=(array.min(axis=2)>=232).astype(np.uint8)
    count,labels,stats,centers=cv2.connectedComponentsWithStats(light,8)
    sx=int((seed.x+seed.width/2)*scale);sy=int((seed.y+min(seed.height/2,50))*scale)
    if not (0<=sx<light.shape[1] and 0<=sy<light.shape[0]):return None
    label=int(labels[sy,sx])
    if label==0:return None
    x,y,w,h,area=stats[label]
    if w<image.width*scale*.25 or h<image.height*scale*.18:return None
    if w>=image.width*scale*.94 and h>=image.height*scale*.92:return None
    return Rect(round(x/scale),round(y/scale),round(w/scale),round(h/scale))


def propose_location(boxes, image: Image.Image) -> LocationProposal | None:
    from .option_controls import recover_option_controls
    boxes=recover_option_controls(boxes,image)
    from .practice import needs_external_data
    candidates=question_candidates(boxes,image.width,image.height)
    if not candidates:return None
    candidate=candidates[0]
    # 框越过屏幕边界是确定截断，不能仅因选项凑齐就允许提交。
    out_of_view=any((box.rect.x<0 or box.rect.y<0 or box.rect.x+box.rect.width>image.width or box.rect.y+box.rect.height>image.height) and candidate.rect.intersection(box.rect) for box in boxes)
    seed=candidate.rect
    selected=[box for box in boxes if seed.contains(box.rect)]
    text=' '.join(clean(box.text) for box in selected)
    headings=[box for box in selected if is_question_start(box.text)]
    heading=min(headings,key=lambda b:b.rect.y) if headings else min(selected,key=lambda b:b.rect.y)
    start=heading.rect.y
    left=max(0,seed.x-22)
    right=min(image.width,seed.x+seed.width+max(100,int(image.width*.10)))
    bottom=image.height-4
    panel=_content_panel(image,seed)
    if panel and panel.contains(heading.rect):
        left=max(0,min(left,panel.x+10))
        right=min(image.width,max(right,panel.x+panel.width-10))
        bottom=min(bottom,panel.y+panel.height-10)
    # 选项/图表可低于OCR已见尾项，先保守覆盖到操作栏或下一道独立题，之后细读。
    for box in boxes:
        value=clean(box.text)
        if box.rect.y<=start+heading.rect.height:continue
        if not left-30<=box.rect.x<=right:continue
        if re.match(r'^(?:下一题|上一题|提交试卷|操作题|素养题|转跳|跳转|题号|返回目录)',value):
            bottom=min(bottom,box.rect.y-12)
        elif is_question_start(value) and abs(box.rect.x-heading.rect.x)<=200:
            inner=bool(re.match(r'^\s*(?:\d{1,2}[)）]|[（(]\d{1,2}[)）])',value)) and not TYPE.search(value)
            if not inner:bottom=min(bottom,box.rect.y-12)
    # 显示/提交范围只从当前题文字及其可信附图获得，面板是上限，不是整块题目。
    # 判断题OCR可能只剩“正确”“错误”，不把它误判为无选项无限全屏回找。
    semantics=[]
    if '判断题' in text:
        for box in boxes:
            value=clean(box.text)
            value=re.sub(r'^[○〇◯●◉□☐O0o)）]*\s*[AB]?\s*[.、:：]?\s*','',value)
            if value not in ('正确','错误','对','错','是','否','True','False'):continue
            if box.rect.y<=start+heading.rect.height or box.rect.y>=bottom:continue
            if not heading.rect.x-100<=box.rect.x<=heading.rect.x+max(650,seed.width):continue
            semantics.append(box)
        semantics.sort(key=lambda b:(b.rect.y,b.rect.x))
        if len(semantics)>=2:
            # 无字母只凭语义的判断选项必须同列；远侧列表不能补成此题AB。
            anchor=semantics[0]
            semantics=[box for box in semantics if abs(box.rect.x-anchor.rect.x)<=max(90,anchor.rect.height*3)]
            words={re.sub(r'\s+','',clean(box.text)) for box in semantics}
            if any('正确' in word or word in ('对','是','True') for word in words) and any('错误' in word or word in ('错','否','False') for word in words):
                selected += [box for box in semantics if box not in selected]
    owned_bottom=max(box.rect.y+box.rect.height for box in selected)
    owned_right=max(box.rect.x+box.rect.width for box in selected)
    line_height=max(24,int(np.median([box.rect.height for box in selected])))
    # 长末项可能有多行，沿同列下方逐行吸收，不能以固定70px截断，也不吸导航。
    for box in sorted(boxes,key=lambda b:(b.rect.y,b.rect.x)):
        if box in selected or box.rect.y<owned_bottom:continue
        continuation_gap=max(160,line_height*5) if option_label(selected[-1].text) else max(80,line_height*3)
        if box.rect.y-owned_bottom>continuation_gap:continue
        if box.rect.y>=bottom or not heading.rect.x-50<=box.rect.x<=owned_right+100:continue
        if is_question_start(box.text) or re.search(r'下一题|上一题|提交|转跳|跳转|题号|学习平台|模拟练习|首页|关于我们',box.text):continue
        selected.append(box);owned_bottom=max(owned_bottom,box.rect.y+box.rect.height);owned_right=max(owned_right,box.rect.x+box.rect.width)
    left=max(0,min(box.rect.x for box in selected)-max(18,line_height//2))
    right=min(image.width,owned_right+max(60,int(line_height*2.5)))
    bottom=min(bottom,owned_bottom+max(22,line_height))
    # 前置材料不能只因无问号就被裁掉：同列近邻正文、图卡区域均保留。
    top=max(0,min(box.rect.y for box in selected)-max(16,line_height//2))
    if top>0:
        material=[box for box in boxes if (panel is None or panel.contains(box.rect)) and box.rect.y<start and start-(box.rect.y+box.rect.height)<=max(100,line_height*3) and abs(box.rect.x-heading.rect.x)<150 and re.search(r'已知|设|给定|材料|根据|条件|图中|图示',box.text) and not re.search(r'剩余|倒计时|^\d{1,3}[:：]\d{2}',box.text)]
        if material:
            top=max(panel.y+8 if panel else 0,min(box.rect.y for box in material)-16)
            left=min(left,max(0,min(box.rect.x for box in material)-18))
            right=max(right,min(image.width,max(box.rect.x+box.rect.width for box in material)+60))
    # 同一卡片有上道题时不把它当公共材料，也不从卡片顶整块并入。
    earlier=[box for box in boxes if is_question_start(box.text) and box.rect.y<start-heading.rect.height and abs(box.rect.x-heading.rect.x)<220]
    if earlier:
        previous_y=max(box.rect.y for box in earlier)
        top=max(top,previous_y+heading.rect.height)
        # 当前题号为硬起点，不能包含前一道题的选项。
        top=max(top,start-18)
    # 不因“如图”三个字就扩整页；图像必须满足下面的同题归属约束。
    # 图片不一定带“如图”文字。用大块非背景连通内容补回无OCR的图/表，不靠题干关键词。
    small=np.asarray(image.resize((max(1,image.width//4),max(1,image.height//4))).convert('RGB'))
    background=np.median(small.reshape(-1,3),axis=0)
    foreground=(np.max(np.abs(small.astype(np.int16)-background),axis=2)>28).astype(np.uint8)
    _,_,stats,_=cv2.connectedComponentsWithStats(foreground,8)
    for x,y,w,h,area in stats[1:]:
        visual=Rect(int(x*4),int(y*4),int(w*4),int(h*4))
        if visual.width<80 or visual.height<70 or area<350:continue
        if visual.width>image.width*.55 or visual.height>image.height*.65 or visual.width/visual.height>5:continue
        # 卡片背景/横幅常包围题目或跨多个区，不得当图。真正图块必须局部且属于题目带。
        if panel and visual.intersection(panel) and visual.width*visual.height>panel.width*panel.height*.65:continue
        if visual.contains(heading.rect):continue
        if visual.y>owned_bottom+line_height*2 or visual.y+visual.height<max(0,start-360):continue
        if visual.x+visual.width<left-max(200,seed.width*.25) or visual.x>image.width-24:continue
        if earlier and visual.y<start:continue
        if visual.y+visual.height<start:
            if start-(visual.y+visual.height)>max(120,line_height*4):continue
            if not _intersects_vertical(visual,seed):continue
            if not re.search(r'如图|图中|图示|上图',text) or ('下图' in text and not re.search('上图|如图',text)):continue
            if any(visual.y+visual.height<box.rect.y<start and re.search(r'剩余|提交|模拟练习|学习平台|首页',box.text) for box in boxes):continue
        else:
            gap=max(0,visual.x-owned_right)
            if gap>max(500,image.width*.25):continue
        left=min(left,max(0,visual.x-18));top=min(top,max(0,visual.y-12));right=max(right,min(image.width,visual.x+visual.width+18))
        bottom=max(bottom,min(image.height,visual.y+visual.height+18))
    bottom=min(image.height,max(bottom,min(image.height,seed.y+seed.height)))
    right=min(image.width,max(right,left+24))
    left=min(left,max(0,image.width-24));top=min(top,max(0,image.height-24))
    bottom=max(bottom,top+24)
    rect=Rect(left,top,max(24,right-left),max(24,bottom-top))
    kind='判断' if '判断题' in text else ('选择' if '选择' in text or '单选' in text or '多选' in text else '其他')
    # 图像扩框不能借另一列普通ABCD列表确认此题完整，只用原结构候选的选项。
    option_boxes=[box for box in selected if option_label(box.text)]
    letters=sorted({option_label(box.text) for box in option_boxes})
    bare_letters=[box for box in option_boxes if re.fullmatch(r'\s*[（(]?[A-H][)）]?[.、:：]?\s*',clean(box.text))]
    option_bodies_complete=True
    for bare in bare_letters:
        label=option_label(bare.text)
        same_line_body=any(other is not bare and abs(other.rect.y+other.rect.height/2-bare.rect.y-bare.rect.height/2)<=max(bare.rect.height,other.rect.height)*.7 and other.rect.x>=bare.rect.x and other.rect.x-(bare.rect.x+bare.rect.width)<=max(180,bare.rect.height*6) and option_label(other.text) in (None,label) and (len(clean(other.text))>2 and other not in bare_letters) for other in selected)
        if not same_line_body:option_bodies_complete=False
    contiguous=letters==list('ABCDEFGH'[:len(letters)])
    if kind=='判断' and len(letters)<2:
        # 语义选项只来自题干下方独立行，不能用题干里的正确/错误字眼伪造AB。
        words=[re.sub(r'^[○〇◯●◉□☐O0o)）]*\s*[AB]?\s*[.、:：]?\s*','',clean(box.text)) for box in semantics]
        if any(word in ('正确','对','是','True') for word in words) and any(word in ('错误','错','否','False') for word in words):letters=['A','B'];contiguous=True
    complete=(kind=='判断' and contiguous and len(letters)>=2) or (kind=='选择' and contiguous and len(letters)>=4) or (kind=='其他' and ('题号' in candidate.signals or '空白括号' in candidate.signals) and len(text)>8)
    # 明确有选项但开头/中间缺失，不可因超时强制提交；框仍保守包含其可见内容。
    if letters and not contiguous:complete=False
    if kind=='其他' and letters:complete=contiguous and len(letters)>=2
    cropped=any(box.rect.y+box.rect.height>=image.height-3 for box in boxes if rect.intersection(box.rect))
    if re.search('多选|多项选择',text):
        # 已见同列题后导航/卡片终点，则更远处屏幕底栏不属于此题。
        tail_anchor=min((box.rect.x for box in option_boxes),default=heading.rect.x)
        tail_right=max((box.rect.x+box.rect.width for box in option_boxes),default=owned_right)
        tail_boundary=any(box.rect.y>=owned_bottom and tail_anchor-160<=box.rect.x<=tail_right+80 and box.rect.width<=max(260,line_height*9) and re.fullmatch(r'.{0,1}(?:下一题|上一题|提交试卷|跳转|转跳|返回目录)\W*',clean(box.text)) for box in boxes)
        tail_boundary=tail_boundary or any(box.rect.y>owned_bottom and abs(box.rect.x-heading.rect.x)<=200 and is_question_start(box.text) and TYPE.search(box.text) for box in boxes)
        # 浅色连通面板可能恰被系统底栏截断，面板边缘本身不是安全题尾证据。
        if not tail_boundary:
            band_left=max(0,int(heading.rect.x));band_right=min(image.width,max(owned_right,int(heading.rect.x+seed.width)))
            rgb=np.asarray(image.convert('RGB'));gray=cv2.cvtColor(rgb,cv2.COLOR_RGB2GRAY)
            edge=gray[max(0,image.height-12):,band_left:band_right]
            # 全宽且各行色差极低的系统/窗口横条，不是半露下一项。
            strip=rgb[max(0,image.height-12):]
            row_spread=np.max(strip,axis=1).astype(np.int16)-np.min(strip,axis=1).astype(np.int16)
            uniform_bar=bool(strip.size and np.all(row_spread<=6) and np.count_nonzero(gray[-12:]<225)>gray[-12:].size*.8 and owned_bottom<image.height-max(40,line_height*2))
            if uniform_bar:
                # 色条可能遮住下一项：检查条的上沿，不能只看被横条覆盖的最底12px。
                bar_top=image.height-1
                for y in range(image.height-1,max(-1,image.height-max(120,line_height*3)),-1):
                    row=rgb[y]
                    if np.max(np.max(row.astype(np.int16),axis=0)-np.min(row.astype(np.int16),axis=0))>6 or np.count_nonzero(gray[y]<225)<image.width*.8:break
                    bar_top=y
                before=gray[max(0,bar_top-12):bar_top,band_left:band_right]
                if before.size and np.count_nonzero(before<225)>12:cropped=True
            elif edge.size and np.count_nonzero(edge<225)>12:cropped=True
    if cropped or out_of_view or not option_bodies_complete:complete=False
    material_floor=max(0,start-max(400,image.height*.3))
    if earlier:
        previous_start=max(box.rect.y for box in earlier)
        previous_options=[box for box in boxes if previous_start<box.rect.y<start and option_label(box.text) and abs(box.rect.x-heading.rect.x)<max(250,seed.width)]
        material_floor=max(material_floor,max((box.rect.y+box.rect.height for box in previous_options),default=previous_start+heading.rect.height)+4)
    if panel:material_floor=max(material_floor,panel.y)
    context_rows=[box for box in boxes if material_floor<=box.rect.y<start and abs(box.rect.x-heading.rect.x)<=160 and not is_question_start(box.text) and not re.search(r'剩余|提交|学习平台|模拟练习',box.text)]
    context='\n'.join(box.text for box in context_rows)+'\n'+text
    functions_only=needs_external_data(context)
    if functions_only:
        # 仅函数指导不依赖完整图选项，但数据文件说明必须保留；不把它冒称完整答案。
        file_rows=[box for box in context_rows if re.search(r'数据|\.xlsx?|\.csv|\.sav|\.dta|\.parquet',box.text,re.I)]
        if file_rows:
            # 文件链接相邻的连续材料行也属于上下文，不仅保留带后缀关键词的一行。
            block=list(file_rows);cursor=min(box.rect.y for box in block)
            for box in sorted(context_rows,key=lambda b:b.rect.y,reverse=True):
                if box in block or box.rect.y>=cursor:continue
                if cursor-(box.rect.y+box.rect.height)>max(60,line_height*2):break
                block.append(box);cursor=box.rect.y
            file_rows=block
            new_top=min(rect.y,min(box.rect.y for box in file_rows)-14)
            new_left=min(rect.x,max(0,min(box.rect.x for box in file_rows)-18))
            new_right=max(rect.x+rect.width,min(image.width,max(box.rect.x+box.rect.width for box in file_rows)+30))
            rect=Rect(new_left,max(0,new_top),new_right-new_left,rect.y+rect.height-max(0,new_top))
    identity=re.sub(r'\s+','',clean(heading.text))
    return LocationProposal(rect,identity,complete,'缺少原始文件，仅提供待运行实操代码' if functions_only else ('完整可见候选' if complete else '正在补全题干与选项'),len(letters),bool(re.search('多选|多项选择',text)),functions_only)


def probe_rect(proposal: LocationProposal, image: Image.Image) -> Rect:
    """补读窗口独立于显示/上传题框，避免框越大输入越多的正反馈。"""
    rect=proposal.rect
    left=max(0,rect.x-40);top=max(0,rect.y-40)
    right=min(image.width,rect.x+rect.width+100)
    bottom=image.height if proposal.needs_tail_probe or not proposal.complete else min(image.height,rect.y+rect.height+120)
    return Rect(left,top,max(24,right-left),max(24,bottom-top))


def steady_proposal(previous: LocationProposal, current: LocationProposal) -> LocationProposal:
    """同题几何迟滞：足够安全余量内的OCR框微调不改变可见题框。"""
    old,new=previous.rect,current.rect
    same=previous.identity==current.identity
    if not same:return current
    padded=Rect(old.x-8,old.y-8,old.width+16,old.height+16)
    # 文字细读变窄不是证据题目被裁：保留一次选定的完整框，防扩缩循环。
    if previous.complete and padded.contains(new):
        return LocationProposal(old,previous.identity,current.complete,current.reason,current.option_count,current.needs_tail_probe,current.functions_only)
    return current


def detection_frame(image: Image.Image, masks: list[Rect]) -> Image.Image:
    # 显示描边/DPI和resize角覆盖固定边缘，不允许我们的框触发画面换题。
    from .dynamic_regions import mask_status_regions
    from PIL import ImageDraw
    result=mask_status_regions(image,masks).copy()
    drawer=ImageDraw.Draw(result)
    drawer.rectangle((0,0,result.width-1,result.height-1),outline='#f4f4f4',width=10)
    return result


def close_rect(a: Rect, b: Rect, tolerance: int = 12) -> bool:
    return max(abs(a.x-b.x),abs(a.y-b.y),abs(a.width-b.width),abs(a.height-b.height))<=tolerance


class LocationLock:
    def __init__(self, stable_ms: int, timeout_ms: int):
        self.stable_seconds=stable_ms/1000
        self.timeout_seconds=timeout_ms/1000
        self.first=None
        self.stable_since=None
        self.proposal=None
        self.locked=False
        self.reason=''

    def observe(self, proposal: LocationProposal | None, now: float | None = None) -> bool:
        now=time.monotonic() if now is None else now
        if proposal is None:
            self.first=self.stable_since=None;self.proposal=None;self.locked=False
            return False
        if self.first is None or self.proposal is None or proposal.identity!=self.proposal.identity:
            self.first=self.stable_since=now;self.locked=False
        elif not close_rect(proposal.rect,self.proposal.rect):
            self.stable_since=now
        self.proposal=proposal
        if not proposal.complete and not proposal.functions_only:return False
        if now-self.stable_since>=self.stable_seconds:
            self.locked=True;self.reason='定位稳定锁定';return True
        if now-self.first>=self.timeout_seconds:
            self.locked=True;self.reason='定位等待超时，锁定最佳完整候选';return True
        return False
