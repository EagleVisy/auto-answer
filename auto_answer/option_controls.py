"""用实际可见控件补全OCR漏掉的选项字母，不以普通四行正文猜ABCD。"""
from __future__ import annotations
import re
import cv2
import numpy as np
from .models import Rect
from .question_layout import is_question_start, option_label, clean


def recover_option_controls(boxes, image):
    from .detection import TextBox
    headings=[b for b in boxes if is_question_start(b.text)]
    if not headings:return list(boxes)
    # 控件仅搜索题型/题号之后；不拿菜单、图例里的圆形当选项。
    heading=min(headings,key=lambda b:b.rect.y)
    if not re.search(r'选择|单选|多选|判断',heading.text):return list(boxes)
    h=heading.rect.height
    gray=cv2.cvtColor(np.asarray(image.convert('RGB')),cv2.COLOR_RGB2GRAY)
    binary=(gray<245).astype(np.uint8)*255
    contours,_=cv2.findContours(binary,cv2.RETR_LIST,cv2.CHAIN_APPROX_SIMPLE)
    controls=[]
    for contour in contours:
        x,y,w,hh=cv2.boundingRect(contour)
        if not max(10,h*.3)<=w<=max(46,h*1.8) or not .78<=w/hh<=1.25:continue
        if y<heading.rect.y+h*2 or not heading.rect.x-60<=x<=heading.rect.x+max(220,h*6):continue
        area=cv2.contourArea(contour);perimeter=cv2.arcLength(contour,True)
        circular=perimeter>0 and 4*np.pi*area/perimeter**2>.72 and area/(w*hh)>.5
        square=len(cv2.approxPolyDP(contour,.035*perimeter,True))==4 and area/(w*hh)>.75
        if not (circular or square):continue
        # 真空心控件中心浅色；实心汉字/数字0也可能圆，须中心和边缘都有证据。
        center=gray[y+hh//3:y+2*hh//3,x+w//3:x+2*w//3]
        if center.size==0 or np.mean(center)>255 or np.mean(center)<235:continue
        rect=Rect(x,y,w,hh)
        if not any(abs(rect.x-old.x)<6 and abs(rect.y-old.y)<6 for old in controls):controls.append(rect)
    groups=[]
    for rect in sorted(controls,key=lambda r:(r.x,r.y)):
        group=next((g for g in groups if abs(g[0].x-rect.x)<=max(8,h*.35)),None)
        if group is None:groups.append([rect])
        else:group.append(rect)
    # 正文同行的内容或图边框是必要佐证，且最多A-H。
    best=[]
    for group in groups:
        group.sort(key=lambda r:r.y)
        if not 2<=len(group)<=8:continue
        if any(b.y-(a.y+a.height)<h for a,b in zip(group,group[1:])):continue
        if len(group)>2:
            gaps=np.diff([r.y for r in group])
            if max(gaps)>min(gaps)*2.2:continue
        if not best or len(group)>len(best):best=group
    if not best:return list(boxes)
    records=[]
    for index,control in enumerate(best):
        cy=control.y+control.height/2
        row=[b for b in boxes if abs(b.rect.y+b.rect.height/2-cy)<=max(h*.7,b.rect.height*.65) and b.rect.x+ b.rect.width>control.x+control.width and b.rect.x<=control.x+image.width*.6]
        row.sort(key=lambda b:b.rect.x)
        expected=chr(65+index)
        labels={option_label(b.text) for b in row if option_label(b.text)}
        if labels and expected not in labels:
            # 明确读到C/D时不能从A重编号，防把缺首项页面凑成完整ABCD。
            return list(boxes)
        body=[b for b in row if len(clean(b.text))>1]
        # 图像选项：轮廓边框/坐标轴包围的大图，其垂直区间覆盖控件中心。
        visuals=[]
        for contour in contours:
            x,y,w,hh=cv2.boundingRect(contour)
            if w<100 or hh<70 or w>image.width*.7 or hh>image.height*.5:continue
            if x<control.x+control.width+10 or x>control.x+300:continue
            if not y-20<=cy<=y+hh+20:continue
            if w/hh>5:continue
            visuals.append(Rect(x,y,w,hh))
        visual=max(visuals,key=lambda r:r.width*r.height) if visuals else None
        if not body and not visual:return list(boxes)
        right=max([control.x+control.width,*[b.rect.x+b.rect.width for b in body],visual.x+visual.width if visual else 0])
        top=min([control.y,*[b.rect.y for b in body],visual.y if visual else control.y])
        bottom=max([control.y+control.height,*[b.rect.y+b.rect.height for b in body],visual.y+visual.height if visual else 0])
        content='图像选项' if visual else ' '.join(b.text for b in body)
        records.append(TextBox(Rect(control.x,top,right-control.x,bottom-top),f'{expected}. '+content,.82))
    # 至少一项OCR读出字母，或四个真实对齐控件；2个图仅可供缺文件指导，不声明完整4项。
    has_anchor=any(option_label(b.text) for b in boxes if any(r.rect.intersection(b.rect) for r in records))
    if not has_anchor and len(records)<4 and not all('图像选项' in r.text for r in records):return list(boxes)
    return sorted([*boxes,*records],key=lambda b:(b.rect.y,b.rect.x))
