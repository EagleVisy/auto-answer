from __future__ import annotations

import ctypes
from ctypes import wintypes
import os

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen
from PySide6.QtWidgets import QApplication, QLabel, QTextEdit, QVBoxLayout, QWidget

from .capture import monitors
from .models import Rect, Settings


def screen_pairs():
    physical = monitors()
    screens = QGuiApplication.screens()
    primary = QGuiApplication.primaryScreen()
    screens = sorted(screens, key=lambda screen: (screen != primary, screen.geometry().x(), screen.geometry().y()))
    return list(zip(physical, screens))


def physical_to_logical(rect: Rect) -> QRect:
    pairs = screen_pairs()
    center = Rect(rect.x + rect.width // 2, rect.y + rect.height // 2, 1, 1)
    physical, screen = next(((p, s) for p, s in pairs if p.contains(center)), pairs[0])
    ratio = screen.devicePixelRatio()
    logical = screen.geometry()
    return QRect(round(logical.x() + (rect.x - physical.x) / ratio), round(logical.y() + (rect.y - physical.y) / ratio),
                 max(24, round(rect.width / ratio)), max(24, round(rect.height / ratio)))


def logical_to_physical(rect: QRect) -> Rect:
    pairs = screen_pairs()
    physical, screen = next(((p, s) for p, s in pairs if s.geometry().contains(rect.center())), pairs[0])
    ratio = screen.devicePixelRatio()
    logical = screen.geometry()
    return Rect(round(physical.x + (rect.x() - logical.x()) * ratio), round(physical.y + (rect.y() - logical.y()) * ratio),
                round(rect.width() * ratio), round(rect.height() * ratio))


def apply_native_flags(widget: QWidget, passive: bool) -> None:
    if os.name != "nt":
        return
    handle = wintypes.HWND(int(widget.winId()))
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_long = user32.GetWindowLongPtrW
    get_long.argtypes = [wintypes.HWND, ctypes.c_int]
    get_long.restype = ctypes.c_ssize_t
    set_long = user32.SetWindowLongPtrW
    set_long.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
    set_long.restype = ctypes.c_ssize_t
    style = get_long(handle, -20)
    style |= 0x80  # TOOLWINDOW：不与主窗口共享最小化生命周期。
    style = style | 0x08000000 | 0x20 if passive else style & ~0x08000000 & ~0x20
    set_long(handle, -20, style)
    user32.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.UINT]
    user32.SetWindowPos(handle, wintypes.HWND(-1), 0, 0, 0, 0, 0x0010 | 0x0001 | 0x0002 | 0x0020)


class EditableOverlay(QWidget):
    geometry_committed = Signal(object)

    def __init__(self, title: str, color: str):
        # 不传入主窗口 parent，避免 owner 被最小化时隐藏。
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        # 选择器接受最小30逻辑像素；范围层不得悄悄扩大已选区域。
        self.setMinimumSize(24, 24)
        self.setMouseTracking(True)
        self.title, self.color = title, QColor(color)
        self.editing = True
        self._drag = None
        self._resizing = False
        self._initial = QRect()
        self._updating = False

    def set_physical_rect(self, rect: Rect) -> None:
        self._updating = True
        self.setGeometry(physical_to_logical(rect))
        self._updating = False

    def physical_rect(self) -> Rect:
        return logical_to_physical(self.geometry())

    def set_editing(self, editing: bool) -> None:
        self.editing = editing
        apply_native_flags(self, not editing)
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        apply_native_flags(self, not self.editing)

    def paintEvent(self, event):
        if not self.editing:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(self.color, 2))
        painter.drawRoundedRect(self.rect().adjusted(2, 2, -2, -2), 10, 10)
        painter.fillRect(3, 3, self.width() - 6, 28, self.color)
        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        painter.drawText(QRect(12, 3, self.width() - 24, 28), Qt.AlignmentFlag.AlignVCenter, self.title + " · 拖动标题 / 拖右下角缩放")
        painter.setPen(QPen(self.color, 3))
        painter.drawLine(self.width() - 18, self.height() - 5, self.width() - 5, self.height() - 18)
        painter.drawLine(self.width() - 11, self.height() - 5, self.width() - 5, self.height() - 11)

    def mousePressEvent(self, event):
        if self.editing and event.button() == Qt.MouseButton.LeftButton:
            point = event.position().toPoint()
            if point.y() <= 34 or (point.x() > self.width() - 22 and point.y() > self.height() - 22):
                self._drag = event.globalPosition().toPoint()
                self._initial = self.geometry()
                self._resizing = point.x() > self.width() - 22 and point.y() > self.height() - 22
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        point = event.position().toPoint()
        if self._drag is not None:
            difference = event.globalPosition().toPoint() - self._drag
            if self._resizing:
                self.resize(max(self.minimumWidth(), self._initial.width() + difference.x()), max(self.minimumHeight(), self._initial.height() + difference.y()))
            else:
                self.move(self._initial.topLeft() + difference)
            event.accept()
        elif self.editing:
            self.setCursor(Qt.CursorShape.SizeFDiagCursor if point.x() > self.width() - 22 and point.y() > self.height() - 22 else Qt.CursorShape.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if self._drag is not None:
            self._drag = None
            self.geometry_committed.emit(self.physical_rect())
        super().mouseReleaseEvent(event)


class CaptureOverlay(EditableOverlay):
    """标题位于有效采集区之外，避免盖住题目首行。"""
    def __init__(self):
        super().__init__("题目范围", "#0284C7")

    def set_physical_rect(self, rect: Rect) -> None:
        logical = physical_to_logical(rect)
        logical.adjust(0, -34, 0, 0)
        self.setGeometry(logical)

    def physical_rect(self) -> Rect:
        logical = self.geometry().adjusted(0, 34, 0, 0)
        return logical_to_physical(logical)

    def paintEvent(self, event):
        if not self.editing:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(0, 0, self.width(), 30, self.color)
        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        painter.drawText(QRect(12, 0, self.width() - 24, 30), Qt.AlignmentFlag.AlignVCenter, "题目范围 · 拖动标题 / 右下角缩放")
        painter.setPen(QPen(self.color, 2))
        painter.drawRect(self.rect().adjusted(2, 35, -2, -2))
        painter.setPen(QPen(self.color, 3))
        painter.drawLine(self.width() - 18, self.height() - 5, self.width() - 5, self.height() - 18)

    def exclusion_rectangles(self) -> list[Rect]:
        logical = self.geometry()
        strips = [QRect(logical.x(), logical.y(), logical.width(), 35),
                  QRect(logical.x(), logical.y() + 34, 4, logical.height() - 34),
                  QRect(logical.right() - 3, logical.y() + 34, 4, logical.height() - 34),
                  QRect(logical.x(), logical.bottom() - 3, logical.width(), 4)]
        return [logical_to_physical(strip) for strip in strips]


class LockIndicator(QWidget):
    """独立顶层小锁，不跟主窗最小化或编辑边框生命周期隐藏。"""
    def __init__(self):
        super().__init__(None,Qt.WindowType.Tool|Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(12,14)
        self.setAccessibleName('输出已锁定')
    def anchor(self,answer_rect:Rect):
        logical=physical_to_logical(answer_rect)
        self.move(logical.x(),logical.y())
    def showEvent(self,event):
        super().showEvent(event);apply_native_flags(self,True)
    def paintEvent(self,event):
        painter=QPainter(self);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.scale(.6,.6)
        painter.setPen(QPen(QColor('#fbbf24'),2))
        painter.setBrush(QColor('#142A32'))
        painter.drawRoundedRect(5,2,10,13,5,5)
        painter.setBrush(QColor('#fbbf24'));painter.drawRoundedRect(2,9,16,12,3,3)
        painter.setPen(QPen(QColor('#142A32'),2));painter.drawLine(10,13,10,17)


class AnswerOverlay(EditableOverlay):
    def __init__(self):
        super().__init__("答案位置", "#0F766E")
        self.setMinimumSize(100, 70)
        self.background = QColor("#142A32")
        self.opacity = 88
        self.text = QTextEdit(self)
        self.text.setReadOnly(True)
        self.text.setFrameStyle(0)
        self.text.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.text.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.text.setStyleSheet("QTextEdit {background:transparent; border:0; padding:5px;} QScrollBar:vertical {width:8px; background:transparent;} QScrollBar::handle:vertical {background:#64748B; min-height:20px; border-radius:4px;}")
        self.set_answer("等待开始")

    def apply_settings(self, settings: Settings):
        self.background = QColor(settings.background_color)
        self.opacity = settings.opacity
        self.text.setFont(QFont(settings.font_family, settings.font_size))
        palette = self.text.palette()
        palette.setColor(palette.ColorRole.Text, QColor(settings.text_color))
        self.text.setPalette(palette)
        self.update()

    def set_answer(self, text: str):
        self.text.setPlainText(text)
        self.text.verticalScrollBar().setValue(0)

    def set_editing(self, editing: bool):
        super().set_editing(editing)
        self.text.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not editing)
        self.resizeEvent(None)

    def resizeEvent(self, event):
        top = 36 if self.editing else 8
        self.text.setGeometry(8, top, max(10, self.width() - 16), max(10, self.height() - top - 12))
        if event is not None:
            super().resizeEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self.background)
        color.setAlpha(round(self.opacity * 2.55))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 12, 12)
        painter.end()
        super().paintEvent(event)


class _SelectionScreen(QWidget):
    selected = Signal(object)
    cancelled = Signal()

    def __init__(self, screen):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(screen.geometry())
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.begin = None
        self.end = None
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(9, 24, 34, 100))
        painter.setPen(QColor("#FFFFFF"))
        painter.setFont(QFont("Microsoft YaHei UI", 15))
        painter.drawText(QRect(32, 30, self.width() - 64, 80), "拖动鼠标框选区域\nEsc 取消 · 每次选择一个显示器内的区域")
        if self.begin is not None and self.end is not None:
            box = QRect(self.begin, self.end).normalized()
            painter.fillRect(box, QColor(15, 118, 110, 55))
            painter.setPen(QPen(QColor("#5EEAD4"), 2))
            painter.drawRect(box)
            painter.drawText(box.adjusted(8, 8, 0, 0), Qt.AlignmentFlag.AlignTop, f"{box.width()} × {box.height()}")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.begin = event.position().toPoint()
            self.end = self.begin
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self.cancelled.emit()

    def mouseMoveEvent(self, event):
        if self.begin is not None:
            self.end = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.begin is not None:
            self.end = event.position().toPoint()
            box = QRect(self.begin, self.end).normalized().intersected(self.rect())
            if box.width() >= 30 and box.height() >= 30:
                box.translate(self.geometry().topLeft())
                self.selected.emit(logical_to_physical(box))
            else:
                self.begin = self.end = None
                self.update()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.cancelled.emit()


class RegionSelector(QWidget):
    selected = Signal(object)
    cancelled = Signal()

    def __init__(self):
        super().__init__()
        self.windows = []
        for screen in QApplication.screens():
            window = _SelectionScreen(screen)
            window.selected.connect(self._selected)
            window.cancelled.connect(self._cancelled)
            self.windows.append(window)

    def open(self):
        for window in self.windows:
            window.show()
            window.raise_()
        for window in self.windows:
            if window.geometry().contains(QGuiApplication.primaryScreen().geometry().center()):
                window.activateWindow()
                window.setFocus()

    def _dispose(self):
        for window in self.windows:
            window.close()
            window.deleteLater()
        self.windows.clear()

    def _selected(self, rect):
        self._dispose()
        self.selected.emit(rect)
        self.deleteLater()

    def _cancelled(self):
        self._dispose()
        self.cancelled.emit()
        self.deleteLater()
