import unittest
from PIL import Image
from auto_answer.detection import OcrReader, TextBox, locate_question
from auto_answer.models import Rect


class RefinementTests(unittest.TestCase):
    def test_coarse_two_of_four_options_still_refined(self):
        image=Image.new('RGB',(2400,1400),'white')
        coarse=[TextBox(Rect(600,200,900,30),'1、（单选题）选择结果（ ）',.9),TextBox(Rect(620,350,500,30),'A. 一',.9),TextBox(Rect(620,450,500,30),'B. 二',.9)]
        class Fake:
            def __init__(self):self.calls=0
            def read(self,img):
                self.calls+=1
                if self.calls==1:return coarse
                return [TextBox(Rect(60,200,400,30),'C. 三',.95),TextBox(Rect(60,290,400,30),'D. 四',.95)]
        reader=Fake()
        boxes=OcrReader.read_screen(reader,image)
        self.assertGreater(reader.calls,1)
        self.assertTrue(any(b.text.startswith('C.') for b in boxes))
        self.assertTrue(any(b.text.startswith('D.') for b in boxes))

    def test_letter_preservation_does_not_delete_body(self):
        image=Image.new('RGB',(1800,1000),'white')
        coarse=[TextBox(Rect(500,100,900,30),'下列哪项正确？',.9),TextBox(Rect(520,200,20,30),'A',.9),TextBox(Rect(560,200,500,30),'第一项必要条件',.9),TextBox(Rect(520,320,20,30),'B',.9),TextBox(Rect(560,320,500,30),'第二项必要条件',.9)]
        class Fake:
            def __init__(self):self.calls=0
            def read(self,img):
                self.calls+=1
                if self.calls==1:return coarse
                # 与粗读A与正文同时重叠，但没有字母。
                return [TextBox(Rect(78,78,540,30),'第一项必要条件',.99)]
        reader=Fake();boxes=OcrReader.read_screen(reader,image)
        self.assertTrue(any(b.text=='A' for b in boxes))
        self.assertTrue(any('第一项必要条件' in b.text for b in boxes))
        self.assertTrue(any('第二项必要条件' in b.text for b in boxes))


if __name__=='__main__':unittest.main()
