from pathlib import Path
import tempfile,threading,time,unittest
from unittest.mock import patch
from PIL import Image,ImageDraw
from PySide6.QtWidgets import QApplication
from auto_answer.models import Settings,Rect,AnswerResult
from auto_answer.engine import Engine
from auto_answer.storage import HistoryStore
from auto_answer.space_key import SpaceKeyWatcher
from auto_answer.ui import MainWindow


class SpaceLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def wait(self,predicate,seconds=6):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            self.app.processEvents()
            if predicate():return True
            time.sleep(.01)
        return False

    def test_key_edges_long_press_and_disabled(self):
        down={'value':False};hits=[]
        watcher=SpaceKeyWatcher(state_reader=lambda:down['value']);watcher.pressed.connect(lambda:hits.append(1))
        try:
            watcher.set_enabled(True);down['value']=True
            for i in range(20):watcher.poll()
            self.assertEqual(len(hits),1)
            down['value']=False;watcher.poll();down['value']=True;watcher.poll()
            self.assertEqual(len(hits),2)
            watcher.set_enabled(False);down['value']=False;watcher.poll();down['value']=True;watcher.poll()
            self.assertEqual(len(hits),2)
        finally:watcher.close()

    def test_button_focus_does_not_trigger_pause_or_repeat_lock(self):
        from PySide6.QtCore import QEvent,Qt
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtWidgets import QPushButton
        watcher=SpaceKeyWatcher(state_reader=lambda:True);hits=[];clicks=[]
        button=QPushButton('Pause');button.clicked.connect(lambda:clicks.append(1))
        watcher.pressed.connect(lambda:hits.append(1))
        try:
            watcher.set_enabled(True);watcher.was_down=False
            self.app.sendEvent(button,QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_Space,Qt.KeyboardModifier.NoModifier,' ',False,1))
            for i in range(5):
                self.app.sendEvent(button,QKeyEvent(QEvent.Type.KeyRelease,Qt.Key.Key_Space,Qt.KeyboardModifier.NoModifier,' ',True,1))
                self.app.sendEvent(button,QKeyEvent(QEvent.Type.KeyPress,Qt.Key.Key_Space,Qt.KeyboardModifier.NoModifier,' ',True,1))
                watcher.poll()
            self.app.sendEvent(button,QKeyEvent(QEvent.Type.KeyRelease,Qt.Key.Key_Space,Qt.KeyboardModifier.NoModifier,' ',False,1))
            self.assertEqual(len(hits),1);self.assertEqual(clicks,[])
        finally:watcher.close();button.close()

    def test_lock_indicator_survives_minimize_and_hidden_overlays(self):
        with tempfile.TemporaryDirectory() as folder:
            window=MainWindow(Path(folder))
            try:
                window.show();window.engine.running.set();window._running(True)
                window._answer('FROZEN');window.engine.toggle_output_lock();self.app.processEvents()
                self.assertTrue(window.lock_indicator.isVisible())
                self.assertEqual((window.lock_indicator.width(),window.lock_indicator.height()),(12,14),'小锁应缩至12×14逻辑像素')
                window.showMinimized();self.wait(lambda:window.isMinimized(),1)
                window.settings.show_capture=False;window.settings.show_answer=False;window._sync_overlays();self.app.processEvents()
                self.assertTrue(window.lock_indicator.isVisible())
                self.assertFalse(window.answer_overlay.isVisible())
                window._answer('SHOULD NOT CHANGE')
                self.assertEqual(window.answer_overlay.text.toPlainText(),'FROZEN')
                window.engine.toggle_output_lock();self.app.processEvents()
                self.assertFalse(window.lock_indicator.isVisible())
            finally:window.close()

    def test_real_global_space_with_main_minimized_and_external_focus(self):
        import ctypes,os
        from ctypes import wintypes
        from PySide6.QtWidgets import QTextEdit
        if os.name!='nt':self.skipTest('仅Windows')
        if os.environ.get('AUTO_ANSWER_INTERACTIVE_KEY_TEST')!='1':
            self.skipTest('真实跨应用空格为显式交互验收，本轮未启用；不计为通过')
        user32=ctypes.WinDLL('user32',use_last_error=True)
        with tempfile.TemporaryDirectory() as folder:
            window=MainWindow(Path(folder));external=QTextEdit();external.setWindowTitle('Offline space key receiver')
            try:
                window.show();window.engine.running.set();window._running(True);window._answer('LOCK-CONTENT')
                external.show();external.raise_();external.activateWindow();external.setFocus()
                window.showMinimized();self.wait(lambda:window.isMinimized(),1)
                user32.SetForegroundWindow.argtypes=[wintypes.HWND];user32.SetForegroundWindow.restype=wintypes.BOOL
                user32.SetForegroundWindow(wintypes.HWND(int(external.winId())))
                if not self.wait(lambda:external.isActiveWindow(),.5):
                    self.skipTest('当前会话无法切换前台至测试接收窗口，跨应用实键需人工验收')
                user32.keybd_event.argtypes=[wintypes.BYTE,wintypes.BYTE,wintypes.DWORD,ctypes.c_size_t]
                user32.keybd_event(0x20,0,0,0)
                try:
                    user32.GetAsyncKeyState.argtypes=[ctypes.c_int];user32.GetAsyncKeyState.restype=ctypes.c_short
                    self.wait(lambda:False,.06)
                    if not bool(user32.GetAsyncKeyState(0x20)&0x8000):
                        self.skipTest('当前执行会话注入键不可见：需交互桌面实键验收')
                    self.assertTrue(self.wait(lambda:window.engine.output_locked,1))
                finally:user32.keybd_event(0x20,0,2,0)
                self.wait(lambda:False,.2)
                self.assertTrue(window.lock_indicator.isVisible())
                self.assertEqual(window.answer_overlay.text.toPlainText(),'LOCK-CONTENT')
                self.assertIn(' ',external.toPlainText(),'全局空格不应吞掉外部应用按键')
                user32.keybd_event(0x20,0,0,0)
                try:self.assertTrue(self.wait(lambda:not window.engine.output_locked,1))
                finally:user32.keybd_event(0x20,0,2,0)
                self.app.processEvents();self.assertFalse(window.lock_indicator.isVisible())
            finally:
                user32.keybd_event(0x20,0,2,0)
                external.close();window.close()

    def test_manual_locked_gate_blocks_ai_until_unlock(self):
        state={'reads':0,'calls':0,'digit':1}
        def capture(*args):
            image=Image.new('RGB',(700,400),'white');ImageDraw.Draw(image).rectangle((80,70,100+state['digit']*10,90),fill='black');return image
        class Reader:
            def read(self,image):
                from auto_answer.detection import TextBox
                state['reads']+=1;return [TextBox(Rect(60,60,500,30),f'1、计算数字{state["digit"]}（ ）',.99)]
        class Client:
            def __init__(self,*args):pass
            def answer(self,*args):state['calls']+=1;return AnswerResult('离线手动','MANUAL',True,True)
        with tempfile.TemporaryDirectory() as folder:
            history=HistoryStore(Path(folder))
            with patch('auto_answer.engine.OcrReader',Reader),patch('auto_answer.engine.monitors',return_value=[Rect(0,0,1920,1080)]),patch('auto_answer.engine.grab',side_effect=capture),patch('auto_answer.engine.DeepSeekClient',Client):
                engine=Engine(history);answers=[];engine.answer.connect(answers.append)
                try:
                    engine.configure(Settings(mode='manual',capture_rect=Rect(100,100,700,400),scan_interval_ms=250,stable_ms=300,privacy_accepted=True),'mock');engine.start();engine.toggle_output_lock()
                    state['digit']=2;self.wait(lambda:False,.8)
                    self.assertGreater(state['reads'],0);self.assertEqual(state['calls'],0)
                    engine.toggle_output_lock();self.assertTrue(self.wait(lambda:'MANUAL' in answers));self.assertEqual(state['calls'],1)
                finally:engine.close();history.close()

    def test_auto_lock_continues_location_and_blocks_requests_then_current_on_unlock(self):
        state={'digit':1,'calls':[],'reads':0}
        screen=Rect(0,0,1200,800)
        def capture(*args):
            image=Image.new('RGB',(1200,800),'white');ImageDraw.Draw(image).rectangle((500,220,510+state['digit']*8,240),fill='black')
            original=image.crop
            def crop(bounds):
                result=original(bounds);result.info['origin']=bounds[:2];return result
            image.crop=crop;return image
        class Reader:
            def read(self,image):
                from auto_answer.detection import TextBox
                state['reads']+=1;x,y=image.info.get('origin',(0,0))
                rows=[(400,150,700,f'1、（单选题）当前数字为{state["digit"]}（ ）'),(420,320,300,'A. 一'),(420,420,300,'B. 二'),(420,520,300,'C. 三'),(420,620,300,'D. 四')]
                return [TextBox(Rect(a-x,b-y,w,30),s,.99) for a,b,w,s in rows]
        class Client:
            def __init__(self,*args):pass
            def answer(self,image,cancel):
                value=state['digit'];state['calls'].append((value,cancel))
                if value==1:cancel.wait(.7)
                return AnswerResult('合成锁测试',f'ANS{value}',True,True)
        with tempfile.TemporaryDirectory() as folder:
            history=HistoryStore(Path(folder))
            with patch('auto_answer.engine.OcrReader',Reader),patch('auto_answer.engine.monitors',return_value=[screen]),patch('auto_answer.engine.grab',side_effect=capture),patch('auto_answer.engine.DeepSeekClient',Client):
                engine=Engine(history);answers=[];engine.answer.connect(answers.append)
                try:
                    engine.configure(Settings(mode='auto',auto_refresh_ms=100,auto_stable_ms=200,privacy_accepted=True),'mock');engine.start()
                    self.assertTrue(self.wait(lambda:len(state['calls'])==1))
                    engine.toggle_output_lock();frozen=answers[-1];reads=state['reads']
                    state['digit']=2
                    self.wait(lambda:False,.8)
                    self.assertEqual(len(state['calls']),1)
                    self.assertGreater(state['reads'],reads,'锁定时后台定位应继续')
                    self.assertTrue(state['calls'][0][1].is_set())
                    self.assertEqual(answers[-1],frozen)
                    self.assertNotIn('ANS1',answers,'晚结果覆盖锁内容')
                    engine.toggle_output_lock()
                    self.assertTrue(self.wait(lambda:'ANS2' in answers))
                    self.assertEqual([x[0] for x in state['calls']],[1,2])
                    engine.pause();self.assertFalse(engine.output_locked)
                finally:engine.close();history.close()


if __name__=='__main__':unittest.main()
