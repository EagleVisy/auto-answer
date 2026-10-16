import os,time,unittest
from auto_answer.native_space import NativeSpaceListener
from auto_answer.space_key import SpaceKeyWatcher
from PySide6.QtWidgets import QApplication


class NativeSpaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def test_native_edge_does_not_repeat_or_consume_other_keys(self):
        hits=[];listener=NativeSpaceListener(lambda:hits.append(1),lambda text:None)
        for i in range(8):listener.feed(0x100,0x20)
        listener.feed(0x100,65)
        self.assertEqual(len(hits),1)
        listener.feed(0x101,0x20);listener.feed(0x100,0x20)
        self.assertEqual(len(hits),2)
        listener.stop_requested.set();listener.feed(0x101,0x20);listener.feed(0x100,0x20)
        self.assertEqual(len(hits),2)
    def test_real_install_message_thread_and_unhook(self):
        if os.name!='nt':self.skipTest('仅Windows')
        errors=[];listener=NativeSpaceListener(lambda:None,errors.append)
        try:
            listener.start();self.assertTrue(listener.ready.wait(2))
            self.assertTrue(listener.installed,errors)
            self.assertGreater(listener.thread_id,0)
            self.assertTrue(listener.thread.is_alive())
        finally:listener.close()
        self.assertFalse(listener.thread.is_alive());self.assertFalse(listener.installed)
        self.assertEqual(listener.thread_id,0)
    def test_old_queued_generation_does_not_toggle_after_reenable(self):
        watcher=SpaceKeyWatcher(state_reader=lambda:False);hits=[];watcher.pressed.connect(lambda:hits.append(1))
        try:
            watcher.set_enabled(True);generation=watcher.generation
            watcher._native_press.emit(generation)
            watcher.set_enabled(False);watcher.set_enabled(True)
            self.app.processEvents();self.assertEqual(hits,[])
            watcher._native_press.emit(watcher.generation)
            self.app.processEvents();self.assertEqual(len(hits),1)
        finally:watcher.close()


if __name__=='__main__':unittest.main()
