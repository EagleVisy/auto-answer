from __future__ import annotations

import ctypes
from ctypes import wintypes
import io
import os

from PIL import Image, ImageDraw, ImageGrab

from .models import Rect


class _WinRect(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG), ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


def enable_dpi_awareness() -> None:
    if os.name == "nt":
        try:
            ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()


def monitors() -> list[Rect]:
    if os.name != "nt":
        image = ImageGrab.grab()
        return [Rect(0, 0, image.width, image.height)]
    found: list[tuple[bool, Rect]] = []
    class MonitorInfo(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", _WinRect), ("rcWork", _WinRect), ("dwFlags", wintypes.DWORD)]
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HANDLE, wintypes.HDC, ctypes.POINTER(_WinRect), wintypes.LPARAM)
    def callback(handle, dc, rect, param):
        info = MonitorInfo()
        info.cbSize = ctypes.sizeof(info)
        ctypes.windll.user32.GetMonitorInfoW(handle, ctypes.byref(info))
        box = info.rcMonitor
        found.append((bool(info.dwFlags & 1), Rect(box.left, box.top, box.right - box.left, box.bottom - box.top)))
        return True
    ctypes.windll.user32.EnumDisplayMonitors(None, None, callback_type(callback), 0)
    found.sort(key=lambda pair: (not pair[0], pair[1].x, pair[1].y))
    if not found:
        raise OSError("无法读取显示器，请检查远程桌面或屏幕权限")
    return [pair[1] for pair in found]


def window_rect(handle: int) -> Rect | None:
    box = _WinRect()
    if os.name == "nt" and ctypes.windll.user32.GetWindowRect(wintypes.HWND(handle), ctypes.byref(box)):
        return Rect(box.left, box.top, box.right - box.left, box.bottom - box.top)
    return None


def grab(rect: Rect, exclusions: list[Rect] | None = None) -> Image.Image:
    rect.validate()
    if rect.width * rect.height > 40000000:
        raise ValueError("截图范围过大，请选择单个显示器或缩小范围")
    image = ImageGrab.grab(bbox=(rect.x, rect.y, rect.x + rect.width, rect.y + rect.height), all_screens=True).convert("RGB")
    if image.size != (rect.width, rect.height):
        raise OSError("截图尺寸不匹配，请重新框选")
    drawer = ImageDraw.Draw(image)
    for excluded in exclusions or []:
        overlap = rect.intersection(excluded)
        if overlap:
            drawer.rectangle((overlap.x - rect.x, overlap.y - rect.y, overlap.x + overlap.width - rect.x - 1,
                              overlap.y + overlap.height - rect.y - 1), fill="#F4F4F4")
    # 边缘裁除用于防止范围编辑描边参与去重；保持返回坐标与像素大小一致。
    drawer.rectangle((0, 0, image.width - 1, image.height - 1), outline="#F4F4F4", width=4)
    return image


def png_bytes(image: Image.Image) -> bytes:
    stream = io.BytesIO()
    image.save(stream, format="PNG", optimize=False)
    return stream.getvalue()
