"""独立Win32消息线程监听空格边沿，始终CallNextHook，不吞键不记录其他键。"""
from __future__ import annotations
import ctypes
from ctypes import wintypes
import os
import threading


class NativeSpaceListener:
    def __init__(self,on_press,on_error):
        self.on_press=on_press;self.on_error=on_error
        self.thread=None;self.thread_id=0;self.hook=None;self.callback=None
        self.ready=threading.Event();self.stop_requested=threading.Event();self.down=False
        self.installed=False;self.error=''
    def feed(self,message,vk):
        # 不检查/存储其他键值，仅空格回调。
        if vk!=0x20:return
        if message in (0x100,0x104):
            if not self.down:
                self.down=True
                if not self.stop_requested.is_set():self.on_press()
        elif message in (0x101,0x105):self.down=False
    def start(self):
        if self.thread and self.thread.is_alive():return
        self.ready.clear();self.stop_requested.clear();self.error='';self.installed=False
        self.thread=threading.Thread(target=self._run,name='space-native-message',daemon=True)
        self.thread.start()
    def _run(self):
        try:self._run_windows()
        except Exception as exc:
            self.error=f'全局空格监听异常（{type(exc).__name__}），请暂停再开始或用锁定按钮'
            self.installed=False;self.thread_id=0;self.callback=None;self.ready.set()
            if not self.stop_requested.is_set():self.on_error(self.error)
    def _run_windows(self):
        if os.name!='nt':
            self.error='仅Windows支持全局空格监听';self.ready.set();self.on_error(self.error);return
        user32=ctypes.WinDLL('user32',use_last_error=True);kernel32=ctypes.WinDLL('kernel32',use_last_error=True)
        LRESULT=ctypes.c_ssize_t
        CALLBACK=ctypes.WINFUNCTYPE(LRESULT,ctypes.c_int,wintypes.WPARAM,wintypes.LPARAM)
        class KeyboardEvent(ctypes.Structure):
            _fields_=[('vkCode',wintypes.DWORD),('scanCode',wintypes.DWORD),('flags',wintypes.DWORD),('time',wintypes.DWORD),('extra',ctypes.c_size_t)]
        user32.SetWindowsHookExW.argtypes=[ctypes.c_int,CALLBACK,wintypes.HINSTANCE,wintypes.DWORD];user32.SetWindowsHookExW.restype=wintypes.HANDLE
        user32.CallNextHookEx.argtypes=[wintypes.HANDLE,ctypes.c_int,wintypes.WPARAM,wintypes.LPARAM];user32.CallNextHookEx.restype=LRESULT
        user32.UnhookWindowsHookEx.argtypes=[wintypes.HANDLE];user32.UnhookWindowsHookEx.restype=wintypes.BOOL
        user32.GetMessageW.argtypes=[ctypes.POINTER(wintypes.MSG),wintypes.HWND,wintypes.UINT,wintypes.UINT];user32.GetMessageW.restype=wintypes.BOOL
        user32.PeekMessageW.argtypes=[ctypes.POINTER(wintypes.MSG),wintypes.HWND,wintypes.UINT,wintypes.UINT,wintypes.UINT]
        user32.TranslateMessage.argtypes=[ctypes.POINTER(wintypes.MSG)];user32.TranslateMessage.restype=wintypes.BOOL
        user32.DispatchMessageW.argtypes=[ctypes.POINTER(wintypes.MSG)];user32.DispatchMessageW.restype=LRESULT
        user32.GetAsyncKeyState.argtypes=[ctypes.c_int];user32.GetAsyncKeyState.restype=ctypes.c_short
        kernel32.GetCurrentThreadId.restype=wintypes.DWORD
        kernel32.GetModuleHandleW.argtypes=[wintypes.LPCWSTR];kernel32.GetModuleHandleW.restype=wintypes.HMODULE
        self.thread_id=kernel32.GetCurrentThreadId()
        msg=wintypes.MSG();user32.PeekMessageW(ctypes.byref(msg),None,0,0,0)  # 建立线程消息队列
        @CALLBACK
        def callback(code,wparam,lparam):
            if code>=0:
                try:
                    event=ctypes.cast(lparam,ctypes.POINTER(KeyboardEvent)).contents
                    if event.vkCode==0x20:self.feed(int(wparam),0x20)
                except Exception:pass
            return user32.CallNextHookEx(self.hook,code,wparam,lparam)
        self.callback=callback
        self.hook=user32.SetWindowsHookExW(13,callback,kernel32.GetModuleHandleW(None),0)
        if not self.hook:
            self.error=f'全局空格监听安装失败（Windows {ctypes.get_last_error()}），可用锁定按钮'
            self.thread_id=0;self.callback=None
            self.ready.set();self.on_error(self.error);return
        # 安装后采样避免安装前释放空窗；采样时尚未泵消息，不会双触发。
        self.down=bool(user32.GetAsyncKeyState(0x20)&0x8000)
        self.installed=True;self.ready.set()
        try:
            while not self.stop_requested.is_set():
                result=user32.GetMessageW(ctypes.byref(msg),None,0,0)
                if result<0:
                    raise OSError(ctypes.get_last_error(),'GetMessageW failed')
                if result==0:
                    if not self.stop_requested.is_set():
                        self.error='全局空格监听意外结束，请暂停后重新开始'
                        self.on_error(self.error)
                    break
                user32.TranslateMessage(ctypes.byref(msg));user32.DispatchMessageW(ctypes.byref(msg))
        finally:
            user32.UnhookWindowsHookEx(self.hook)
            self.hook=None;self.installed=False;self.down=False;self.thread_id=0;self.callback=None
    def close(self):
        self.stop_requested.set()
        if os.name=='nt' and self.thread_id:
            user32=ctypes.WinDLL('user32',use_last_error=True)
            user32.PostThreadMessageW.argtypes=[wintypes.DWORD,wintypes.UINT,wintypes.WPARAM,wintypes.LPARAM]
            user32.PostThreadMessageW(self.thread_id,0x12,0,0)
        if self.thread and self.thread is not threading.current_thread():self.thread.join(timeout=1.0)
