"""候选快速反馈/等待/超时/暂停的自动全链路，不调用网络。"""
import tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image,ImageDraw
from PySide6.QtWidgets import QApplication
from auto_answer.engine import Engine
from auto_answer.detection import TextBox
from auto_answer.models import Rect,Settings,AnswerResult
from auto_answer.storage import HistoryStore


class AutoScanTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def wait(self,predicate,seconds=6):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            self.app.processEvents()
            if predicate():return True
            time.sleep(.01)
        return False

    def run_fixture(self,animation=False,incomplete=False):
        state={'captures':0,'calls':0,'events':[]}
        screen=Rect(0,0,1200,800)
        def capture(rect,exclusions):
            state['captures']+=1
            image=Image.new('RGB',(1200,800),'white');draw=ImageDraw.Draw(image)
            draw.rectangle((420,210,500+(state['captures']%2 if animation else 0)*10,240),fill='black')
            original_crop=image.crop
            def with_origin(bounds):
                cropped=original_crop(bounds);cropped.info['origin']=bounds[:2]
                return cropped
            image.crop=with_origin
            return image
        class Reader:
            def read(self,image):
                # 回填坐标相对各次裁剪，真实数据内容不随动画改变。
                offset=image.info.get('origin',(0,0))
                rows=[(400,160,600,'1、（单选题）下列哪项成立（ ）'),(420,300,250,'A. 一'),(420,400,250,'B. 二')]
                if not incomplete:rows += [(420,500,250,'C. 三'),(420,600,250,'D. 四')]
                return [TextBox(Rect(x-offset[0],y-offset[1],w,30),s,.99) for x,y,w,s in rows]
        class Client:
            def __init__(self,settings,key):pass
            def answer(self,image,cancel):
                state['calls']+=1;state['events'].append(('request',time.monotonic()))
                if animation and cancel.wait(.6):
                    raise AssertionError('超时锁定后的同一动画立即取消请求')
                return AnswerResult('模拟题','FIXTURE',True,True)
        with tempfile.TemporaryDirectory() as folder:
            history=HistoryStore(Path(folder))
            with patch('auto_answer.engine.OcrReader',Reader),patch('auto_answer.engine.monitors',return_value=[screen]),patch('auto_answer.engine.grab',side_effect=capture),patch('auto_answer.engine.DeepSeekClient',Client):
                engine=Engine(history);answers=[];statuses=[]
                engine.answer.connect(answers.append);engine.status.connect(statuses.append)
                engine.region_changed.connect(lambda rect:state['events'].append(('candidate',time.monotonic())))
                try:
                    settings=Settings(mode='auto',auto_refresh_ms=100,auto_stable_ms=500,auto_lock_timeout_ms=1000,privacy_accepted=True)
                    engine.configure(settings,'offline-placeholder');started=time.monotonic();engine.start()
                    if incomplete:
                        self.wait(lambda:False,2)
                        self.assertEqual(state['calls'],0,'明确缺少CD不可被超时提交')
                    else:
                        self.assertTrue(self.wait(lambda:'FIXTURE' in answers),statuses)
                        candidate=next(t for k,t in state['events'] if k=='candidate')
                        request=next(t for k,t in state['events'] if k=='request')
                        self.assertLess(candidate-started,.8,'候选框应先反馈')
                        self.assertGreater(request-candidate,.45,'应先等待范围稳定')
                        if animation:self.assertTrue(any('超时' in s for s in statuses),statuses)
                        self.wait(lambda:False,.5)
                        self.assertEqual(state['calls'],1,'超时锁定后已知非文字动画不应反复请求')
                finally:engine.close();history.close()

    def test_candidate_feedback_precedes_stable_request(self):self.run_fixture()
    def test_animation_wait_is_bounded_by_timeout(self):self.run_fixture(animation=True)
    def test_definitely_missing_options_never_forced(self):self.run_fixture(incomplete=True)


if __name__=='__main__':unittest.main()
