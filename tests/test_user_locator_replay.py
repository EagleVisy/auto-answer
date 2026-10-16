"""合成页面回归：不得把横幅/导航当题目，判断选项完整。"""
from pathlib import Path
import json,unittest
from PIL import Image
from auto_answer.detection import TextBox
from auto_answer.models import Rect
from auto_answer.auto_locator import propose_location, steady_proposal, probe_rect, detection_frame
from PIL import ImageDraw





class UserLocatorTests(unittest.TestCase):
    def test_multi_choice_tail_probe_is_wide_but_display_is_tight(self):
        image=Image.new('RGB',(1200,800),'white')
        boxes=[TextBox(Rect(400,40,600,30),'1、（多选题）下列结论（ ）',.99)]
        boxes += [TextBox(Rect(420,140+i*80,200,30),f'{letter}. 选项',.99) for i,letter in enumerate('ABCD')]
        proposal=propose_location(boxes,image)
        self.assertLess(proposal.rect.y+proposal.rect.height,500)
        self.assertEqual(probe_rect(proposal,image).y+probe_rect(proposal,image).height,800)

    def test_judgement_semantics_on_another_column_do_not_fill_missing_option(self):
        image=Image.new('RGB',(1920,1080),'white')
        boxes=[TextBox(Rect(500,300,400,30),'1、（判断题）某命题成立（ ）',.99),TextBox(Rect(520,460,150,30),'正确',.99),TextBox(Rect(1090,660,150,30),'错误',.99)]
        self.assertFalse(propose_location(boxes,image).complete)

    def test_stem_keywords_do_not_fake_two_judgement_options(self):
        image=Image.new('RGB',(1920,1080),'white')
        boxes=[TextBox(Rect(500,300,800,30),'1、（判断题）以下结论有正确和错误之分（ ）',.99),TextBox(Rect(520,460,200,30),'A. 正确',.99)]
        self.assertFalse(propose_location(boxes,image).complete)

    def test_nearby_banner_without_question_reference_is_not_material(self):
        image=Image.new('RGB',(1920,1080),'white');ImageDraw.Draw(image).rectangle((500,30,1100,220),fill='blue')
        boxes=[TextBox(Rect(500,300,800,30),'1、（单选题）下列结论（ ）',.99)]
        boxes += [TextBox(Rect(520,460+i*100,220,30),f'{letter}. 选项',.99) for i,letter in enumerate('ABCD')]
        self.assertGreater(propose_location(boxes,image).rect.y,250)

    def test_edge_drawing_not_stable_gate_feedback(self):
        from auto_answer.detection import StableGate
        image=Image.new('RGB',(900,400),'white');draw=ImageDraw.Draw(image);draw.rectangle((60,50,180,80),fill='black')
        edged=image.copy();ImageDraw.Draw(edged).rectangle((0,0,899,399),outline='blue',width=5)
        gate=StableGate(.3);gate.observe(detection_frame(image,[]),10)
        changed,stable=gate.observe(detection_frame(edged,[]),10.5)
        self.assertFalse(changed);self.assertTrue(stable)




if __name__=='__main__':unittest.main()
