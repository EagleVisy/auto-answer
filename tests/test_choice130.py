from pathlib import Path
import json,unittest
from PIL import Image,ImageDraw
from auto_answer.models import Rect
from auto_answer.detection import TextBox
from auto_answer.auto_locator import propose_location

class Choice130Tests(unittest.TestCase):
    def rows(self,kind='多选题'):
        return [TextBox(Rect(350,80,650,30),f'1、（{kind}）以下命题（）',.99)]+[TextBox(Rect(380,180+i*110,300,30),f'{c}. 选项正文',.99) for i,c in enumerate('ABCDE')]
    def test_bottom_system_strip_without_navigation_not_false_cutoff(self):
        image=Image.new('RGB',(1300,1000),'white');ImageDraw.Draw(image).rectangle((0,978,1299,999),fill='#505050')
        self.assertTrue(propose_location(self.rows(),image).complete)
        ImageDraw.Draw(image).rectangle((0,978,1299,999),fill='#3a3d45')
        self.assertTrue(propose_location(self.rows(),image).complete,'非中性均匀色条同样不能误报')
    def test_partial_option_below_tail_still_rejected(self):
        image=Image.new('RGB',(1300,850),'white');draw=ImageDraw.Draw(image)
        draw.rectangle((380,843,405,849),outline='#888888',width=2);draw.rectangle((430,846,620,849),fill='black')
        self.assertFalse(propose_location(self.rows(),image).complete,'局部半露下一项仍须拒绝')
    def test_tail_words_in_body_and_option_above_system_bar_not_ignored(self):
        image=Image.new('RGB',(1300,850),'white');draw=ImageDraw.Draw(image)
        draw.rectangle((380,843,405,849),outline='#888888',width=2);draw.rectangle((430,846,620,849),fill='black')
        boxes=self.rows()+[TextBox(Rect(350,740,450,30),'提示：请完成本题后点下一题',.99)]
        self.assertFalse(propose_location(boxes,image).complete)
        self.assertFalse(propose_location(self.rows()+[TextBox(Rect(1000,740,120,30),'下一题',.99)],image).complete,'另一侧导航不能关闭选项尾探测')
        image=Image.new('RGB',(1300,1000),'white');draw=ImageDraw.Draw(image)
        draw.rectangle((0,978,1299,999),fill='#505050');draw.rectangle((380,967,405,990),outline='#888888',width=2);draw.rectangle((430,974,620,977),fill='black')
        self.assertFalse(propose_location(self.rows(),image).complete,'横条上沿半露项不能被忽略')
        panel_image=Image.new('RGB',(1300,1000),'#aaaaaa');panel_draw=ImageDraw.Draw(panel_image)
        panel_draw.rectangle((300,40,1090,999),fill='white');panel_draw.rectangle((0,978,1299,999),fill='#505050')
        panel_draw.rectangle((380,967,405,990),outline='#888888',width=2);panel_draw.rectangle((430,974,620,977),fill='black')
        rows=self.rows();rows[-1]=TextBox(Rect(380,880,300,30),'E. 最后一项',.99)
        self.assertFalse(propose_location(rows,panel_image).complete,'被横条截断的面板边界不是真题尾')

    def test_previous_missing_middle_and_bare_letter_guards_remain(self):
        image=Image.new('RGB',(1300,1000),'white');rows=self.rows('单选题')
        self.assertFalse(propose_location([r for r in rows if not r.text.startswith('C.')],image).complete)
        rows[-1]=TextBox(rows[-1].rect,'E',.99)
        self.assertFalse(propose_location(rows,image).complete)

if __name__=='__main__':unittest.main()
