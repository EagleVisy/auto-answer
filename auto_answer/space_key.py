"""空格输出锁：生产使用独立Win32事件线程，测试可注入状态读器。"""
from __future__ import annotations
from PySide6.QtCore import QObject,QTimer,Signal,QEvent,Qt,Slot
from PySide6.QtWidgets import QApplication,QAbstractButton
from .native_space import NativeSpaceListener


class SpaceKeyWatcher(QObject):
    pressed=Signal()
    unavailable=Signal(str)
    _native_press=Signal(int)
    _native_error=Signal(int,str)
    def __init__(self,parent=None,state_reader=None):
        super().__init__(parent)
        self.enabled=False;self.was_down=False;self.reader=state_reader;self.native=None;self.generation=0
        self.timer=QTimer(self);self.timer.setInterval(20);self.timer.timeout.connect(self.poll)
        self._native_press.connect(self._accept_native,Qt.ConnectionType.QueuedConnection)
        self._native_error.connect(self._accept_error,Qt.ConnectionType.QueuedConnection)
        app=QApplication.instance()
        if app:app.installEventFilter(self)
    @Slot(int)
    def _accept_native(self,generation):
        if self.enabled and generation==self.generation:self.pressed.emit()
    @Slot(int,str)
    def _accept_error(self,generation,text):
        if self.enabled and generation==self.generation:self.unavailable.emit(text)
    def set_enabled(self,enabled:bool):
        if self.enabled==enabled:return
        self.enabled=enabled;self.generation+=1
        if enabled:
            if self.reader is None:
                generation=self.generation
                self.native=NativeSpaceListener(lambda:self._native_press.emit(generation),lambda text:self._native_error.emit(generation,text))
                self.native.start()
            elif self.reader is not None:
                self.was_down=bool(self.reader());self.timer.start()
        else:
            self.timer.stop();self.was_down=False
            if self.native:self.native.close()
    def poll(self):
        if not self.enabled or self.reader is None:return
        down=bool(self.reader());edge=down and not self.was_down
        self.was_down=down
        if edge:self.pressed.emit()
    def eventFilter(self,watched,event):
        # 仅防本软件按钮空格额外click；其他应用/文本框按键仍透传。
        if self.enabled and isinstance(watched,QAbstractButton) and event.type() in (QEvent.Type.KeyPress,QEvent.Type.KeyRelease) and event.key()==Qt.Key.Key_Space:
            if self.reader is not None:
                if event.type()==QEvent.Type.KeyPress and not event.isAutoRepeat() and not self.was_down:
                    self.was_down=True;self.pressed.emit()
                elif event.type()==QEvent.Type.KeyRelease and not event.isAutoRepeat():self.was_down=False
            return True
        return super().eventFilter(watched,event)
    def close(self):
        self.set_enabled(False)
        if self.native:self.native.close()
        app=QApplication.instance()
        if app:app.removeEventFilter(self)
