"""真实可见题框与动态排除条参与的回归；只作离线Mock，不答用户截图。"""
import tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from auto_answer.overlays import CaptureOverlay
from auto_answer.engine import Engine
from auto_answer.storage import HistoryStore
from auto_answer.models import Settings,AnswerResult,Rect
from test_live_pipeline import QuestionWindow


class LiveOverlayLocatorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def wait(self,predicate,seconds=18):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            self.app.processEvents()
            if predicate():return True
            time.sleep(.01)
        return False
    def test_visible_overlay_does_not_relocate_or_cancel_same_question(self):
        window=QuestionWindow();window.show();overlay=CaptureOverlay();overlay.set_editing(True)
        state={'calls':0,'cancel':None};regions=[];answers=[]
        class Client:
            def __init__(self,settings,key):pass
            def answer(self,image,cancel):
                state['calls']+=1;state['cancel']=cancel
                if cancel.wait(.7):raise AssertionError('显示边框取消同题请求')
                return AnswerResult('合成练习','MOCK',True,True)
        with tempfile.TemporaryDirectory() as folder:
            history=HistoryStore(Path(folder))
            with patch('auto_answer.engine.DeepSeekClient',Client):
                engine=Engine(history);engine.answer.connect(answers.append)
                def region(rect):
                    regions.append(rect)
                    overlay.set_physical_rect(rect);overlay.show()
                    engine.set_exclusions(overlay.exclusion_rectangles())
                engine.region_changed.connect(region)
                try:
                    engine.configure(Settings(mode='auto',auto_refresh_ms=250,auto_stable_ms=650,privacy_accepted=True),'offline-placeholder');engine.start()
                    self.assertTrue(self.wait(lambda:'MOCK' in answers),answers)
                    self.wait(lambda:False,4)
                    self.assertEqual(state['calls'],1,'题目边框造成反复重答')
                    self.assertFalse(state['cancel'].is_set())
                    self.assertLessEqual(len(regions),3,'同题连续跳框')
                    for rect in regions:
                        self.assertGreater(rect.x,700,'不应扩至菜单')
                        self.assertGreater(rect.y,200,'不应扩至顶部计时')
                        self.assertLess(rect.height,1500,'不应扩整个页面')
                    window.digit=2;window.update()
                    self.assertTrue(self.wait(lambda:state['calls']==2),'真换题应识别')
                finally:
                    engine.close();history.close();overlay.close();window.close()


if __name__=='__main__':unittest.main()
