import unittest
from PIL import Image, ImageDraw
from auto_answer.auto_locator import LocationLock, LocationProposal, propose_location
from auto_answer.detection import TextBox
from auto_answer.models import Rect, Settings


def row(x,y,w,text):return TextBox(Rect(x,y,w,30),text,.99)


class AutoLocatorTests(unittest.TestCase):
    def test_stable_region_locks_then_timeout_not_used(self):
        lock=LocationLock(650,5000)
        proposal=LocationProposal(Rect(400,200,1000,600),'1、',True,'',4)
        self.assertFalse(lock.observe(proposal,10))
        self.assertFalse(lock.observe(proposal,10.3))
        self.assertTrue(lock.observe(proposal,10.7))
        self.assertEqual(lock.reason,'定位稳定锁定')

    def test_moving_complete_candidate_locks_by_absolute_timeout(self):
        lock=LocationLock(650,2000)
        for i in range(4):
            proposal=LocationProposal(Rect(400+i*20,200,1000,600),'1、',True,'',4)
            self.assertFalse(lock.observe(proposal,10+i*.5))
        self.assertTrue(lock.observe(LocationProposal(Rect(500,200,1000,600),'1、',True,'',4),12))
        self.assertIn('超时',lock.reason)

    def test_incomplete_or_empty_never_forced_into_request(self):
        lock=LocationLock(650,2000)
        self.assertFalse(lock.observe(LocationProposal(Rect(400,200,1000,600),'1、',False,'',2),10))
        self.assertFalse(lock.observe(LocationProposal(Rect(400,200,1000,600),'1、',False,'',2),15))
        self.assertFalse(lock.observe(None,20))

    def test_missing_ocr_tail_and_diagram_inside_card_still_in_region(self):
        image=Image.new('RGB',(1920,1080),'#e5e7eb');d=ImageDraw.Draw(image)
        d.rectangle((400,170,1730,940),fill='white')
        d.rectangle((1100,330,1600,680),fill='#4488aa')
        rows=[row(450,230,750,'1、（单选题）根据下图选择（ ）'),row(480,450,250,'A. 一'),row(480,570,250,'B. 二'),row(480,730,250,'C. 三'),row(480,830,250,'D. 四'),row(450,970,120,'下一题')]
        proposal=propose_location(rows,image)
        self.assertTrue(proposal.complete)
        self.assertTrue(proposal.rect.contains(Rect(1100,330,500,350)),'不可只框OCR文字裁掉图')
        self.assertTrue(proposal.rect.contains(rows[-2].rect))

    def test_long_wrapped_last_option_content_retained(self):
        image=Image.new('RGB',(1920,1080),'white')
        rows=[row(500,180,900,'1、（单选题）选择（ ）'),row(520,300,250,'A. 一'),row(520,400,250,'B. 二'),row(520,500,250,'C. 三'),row(520,600,250,'D. 最后一项'),row(560,770,900,'尾项第三行重要条件'),row(520,970,120,'下一题')]
        proposal=propose_location(rows,image)
        self.assertTrue(proposal.rect.contains(rows[-2].rect))
        self.assertLess(proposal.rect.y+proposal.rect.height,970)

    def test_gray_background_far_right_diagram_included(self):
        image=Image.new('RGB',(1920,1080),'#d0d0d0');ImageDraw.Draw(image).rectangle((1370,360,1650,610),fill='blue')
        rows=[row(500,200,600,'1、（单选题）如图选择结果（ ）'),row(520,350,240,'A. 一'),row(520,450,240,'B. 二'),row(520,550,240,'C. 三'),row(520,650,240,'D. 四')]
        proposal=propose_location(rows,image)
        self.assertTrue(proposal.rect.contains(Rect(1370,360,280,250)))

    def test_multiple_questions_same_card_do_not_merge_previous(self):
        image=Image.new('RGB',(1920,1080),'#dddddd');ImageDraw.Draw(image).rectangle((400,80,1730,1030),fill='white')
        rows=[row(450,150,700,'1、（判断题）以下结论（ ）'),row(470,240,240,'A. 正确'),row(470,310,240,'B. 错误'),row(450,450,700,'2、（单选题）如图选择（ ）'),row(470,560,240,'A. 一'),row(470,660,240,'B. 二'),row(470,760,240,'C. 三'),row(470,860,240,'D. 四')]
        proposal=propose_location(rows,image)
        self.assertFalse(proposal.rect.intersection(rows[0].rect))
        self.assertTrue(proposal.rect.contains(rows[-1].rect))

    def test_upstream_diagram_without_panel_not_cropped(self):
        image=Image.new('RGB',(1920,1080),'#cccccc');ImageDraw.Draw(image).rectangle((580,90,980,250),fill='blue')
        rows=[row(500,350,800,'1、（单选题）如图选择结果（ ）'),row(520,460,250,'A. 一'),row(520,560,250,'B. 二'),row(520,660,250,'C. 三'),row(520,760,250,'D. 四')]
        self.assertTrue(propose_location(rows,image).rect.contains(Rect(580,90,400,160)))

    def test_out_of_screen_stem_rejected_as_complete(self):
        image=Image.new('RGB',(1920,1080),'white')
        rows=[row(1500,200,500,'1、（单选题）以下结论（ ）'),row(1510,350,260,'A. 一'),row(1510,450,260,'B. 二'),row(1510,550,260,'C. 三'),row(1510,650,260,'D. 四')]
        self.assertFalse(propose_location(rows,image).complete)

    def test_new_settings_migration_and_bounds(self):
        settings=Settings.from_dict({'mode':'auto'})
        self.assertEqual(settings.auto_lock_timeout_ms,5000)
        self.assertEqual(Settings.from_dict(settings.to_dict()).auto_refresh_ms,250)
        for name in ('auto_refresh_ms','auto_stable_ms','auto_lock_timeout_ms'):
            value=Settings();setattr(value,name,0)
            with self.assertRaises(ValueError):value.validate()


if __name__=='__main__':unittest.main()
