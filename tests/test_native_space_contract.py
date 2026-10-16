"""原生C回调/WinAPI契约测试；伪系统API，不作为真实全局按键验收。"""
import ctypes
from ctypes import wintypes
import os,unittest
from unittest.mock import patch
from auto_answer.native_space import NativeSpaceListener


class Function:
    def __init__(self,body):self.body=body
    def __call__(self,*args):return self.body(*args)


class NativeApiContractTests(unittest.TestCase):
    def run_api(self,events=(),failure='',held=False):
        if os.name!='nt':self.skipTest('需要Windows ctypes回调ABI')
        hits=[];errors=[];trace={'forwarded':[],'unhooked':[],'callbacks':[]}
        listener=NativeSpaceListener(lambda:hits.append(1),errors.append)
        class KeyboardEvent(ctypes.Structure):
            _fields_=[('vkCode',wintypes.DWORD),('scanCode',wintypes.DWORD),('flags',wintypes.DWORD),('time',wintypes.DWORD),('extra',ctypes.c_size_t)]
        class Api:pass
        user=Api();kernel=Api();queue=list(events)
        def install(kind,callback,module,thread):
            self.assertEqual(kind,13);self.assertEqual(thread,0)
            trace['callback']=callback
            return 0 if failure=='install' else 1234
        def forward(handle,code,message,pointer):
            trace['forwarded'].append((code,message))
            return 9876
        def get_message(*args):
            if failure=='message':return -1
            if queue:
                code,message,vk=queue.pop(0)
                event=KeyboardEvent(vk,0,0,0,0)
                pointer=ctypes.addressof(event) if code>=0 else 0
                trace['callbacks'].append(trace['callback'](code,message,pointer))
                return 1
            listener.stop_requested.set()
            return 0
        def module(name):
            if failure=='initialization':raise OSError('测试初始化失败')
            return 4321
        user.SetWindowsHookExW=Function(install)
        user.CallNextHookEx=Function(forward)
        user.UnhookWindowsHookEx=Function(lambda handle:trace['unhooked'].append(handle) or 1)
        user.GetMessageW=Function(get_message)
        user.PeekMessageW=Function(lambda *args:0)
        user.TranslateMessage=Function(lambda *args:1)
        user.DispatchMessageW=Function(lambda *args:0)
        user.GetAsyncKeyState=Function(lambda *args:0x8000 if held else 0)
        kernel.GetCurrentThreadId=Function(lambda:4242)
        kernel.GetModuleHandleW=Function(module)
        with patch('auto_answer.native_space.ctypes.WinDLL',side_effect=lambda name,**kwargs:user if name=='user32' else kernel):
            listener._run()
        return listener,hits,errors,trace

    def test_c_callback_forwards_all_messages_and_space_repeat_once(self):
        events=[(-1,0x100,0),(0,0x100,65),(0,0x100,32),(0,0x100,32),(0,0x101,32),(0,0x100,32),(0,0x101,32)]
        listener,hits,errors,trace=self.run_api(events)
        self.assertEqual(len(hits),2);self.assertEqual(errors,[])
        self.assertEqual(trace['callbacks'],[9876]*len(events),'必须原样透传CallNextHook返回值')
        self.assertEqual(len(trace['forwarded']),len(events))
        self.assertEqual(trace['unhooked'],[1234]);self.assertIsNone(listener.callback)
        self.assertEqual(listener.thread_id,0);self.assertFalse(listener.installed)

    def test_held_space_requires_release_before_next_press(self):
        events=[(0,0x100,32),(0,0x101,32),(0,0x100,32),(0,0x101,32)]
        _,hits,errors,_=self.run_api(events,held=True)
        self.assertEqual(len(hits),1);self.assertEqual(errors,[])

    def test_message_loop_error_is_visible_and_unhooks(self):
        listener,hits,errors,trace=self.run_api(failure='message')
        self.assertEqual(hits,[]);self.assertEqual(len(errors),1)
        self.assertIn('监听异常',errors[0]);self.assertEqual(trace['unhooked'],[1234])
        self.assertTrue(listener.ready.is_set());self.assertEqual(listener.thread_id,0)

    def test_install_failure_releases_thread_identity_and_reports(self):
        listener,_,errors,trace=self.run_api(failure='install')
        self.assertEqual(len(errors),1);self.assertIn('安装失败',errors[0])
        self.assertEqual(trace['unhooked'],[]);self.assertIsNone(listener.callback)
        self.assertEqual(listener.thread_id,0);self.assertFalse(listener.installed)

    def test_initialization_error_does_not_stay_silent(self):
        listener,_,errors,_=self.run_api(failure='initialization')
        self.assertEqual(len(errors),1);self.assertIn('监听异常',errors[0])
        self.assertTrue(listener.ready.is_set());self.assertEqual(listener.thread_id,0)


if __name__=='__main__':unittest.main()
