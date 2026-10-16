from __future__ import annotations

from copy import deepcopy
import logging
import threading
import time

from PySide6.QtCore import QObject, Signal, Slot

from .api import ApiError, Cancelled, DeepSeekClient
from .capture import grab, monitors, png_bytes
from .detection import OcrReader, RequestEpoch, StableGate, locate_question, looks_like_question, normalized_text, same_frame
from .dynamic_regions import CountdownTracker, mask_status_regions, timer_masks_still_safe
from .models import Rect, Settings
from .pipeline import AnswerPipeline
from .storage import HistoryStore


class Engine(QObject):
    status = Signal(str)
    answer = Signal(str)
    history_changed = Signal()
    region_changed = Signal(object)
    running_changed = Signal(bool)
    output_lock_changed = Signal(bool)
    _status_event = Signal(int, str, str)
    _result_event = Signal(int, object, bytes, float)

    def __init__(self, history: HistoryStore):
        super().__init__()
        self.history = history
        self.epoch = RequestEpoch()
        self.lock = threading.RLock()
        self.running = threading.Event()
        self.shutdown = threading.Event()
        self.settings = Settings()
        self.key = ""
        self.exclusions: list[Rect] = []
        self.serial = 0
        self.request_threads: set[threading.Thread] = set()
        self.output_locked=False
        self.request_revision=0
        self._status_event.connect(self._accept_status)
        self._result_event.connect(self._accept_result)
        self.worker = threading.Thread(target=self._scan, name="screen-ocr", daemon=True)
        self.worker.start()

    def configure(self, settings: Settings, key: str) -> None:
        settings.validate()
        with self.lock:
            self.settings = deepcopy(settings)
            self.key = key
            self.serial += 1
            self.epoch.invalidate()
        if self.running.is_set() and not self.output_locked:
            self.answer.emit("等待题目稳定")

    def set_exclusions(self, rectangles: list[Rect]) -> None:
        with self.lock:
            self.exclusions = list(rectangles)

    def start(self) -> None:
        with self.lock:
            if not self.key:
                raise ValueError("请先在 AI 连接中保存 API Key")
            if not self.settings.privacy_accepted:
                raise ValueError("请先确认截图上传与费用提示")
            if self.settings.mode == "manual" and self.settings.capture_rect is None:
                raise ValueError("请先框选题目区域")
            screens = monitors()
            rect = self.settings.capture_rect
            if self.settings.mode == "manual" and not any(screen.contains(rect) for screen in screens):
                raise ValueError("题目区域必须完整位于一个可用显示器内，请重新框选")
            if self.settings.monitor_index >= len(screens):
                raise ValueError("所选显示器已断开，请重新选择")
            self.serial += 1
            self.epoch.invalidate()
            self.running.set()
        self.status.emit("正在初始化本地识题")
        self.answer.emit("等待题目")
        self.running_changed.emit(True)

    def pause(self) -> None:
        with self.lock:
            self.running.clear()
            self.epoch.invalidate()
            self.serial += 1
            self.output_locked=False
            self.request_revision+=1
        self.output_lock_changed.emit(False)
        self.status.emit("已暂停 · 不采集、不上传")
        self.answer.emit("已暂停")
        self.running_changed.emit(False)

    def toggle_output_lock(self) -> bool:
        with self.lock:
            if not self.running.is_set():return False
            self.output_locked=not self.output_locked
            self.request_revision+=1
            self.epoch.invalidate()
            locked=self.output_locked
        self.output_lock_changed.emit(locked)
        self.status.emit('输出已锁定 · 后台定位继续，AI请求暂停' if locked else '输出已解锁 · 正在确认当前题目')
        return locked

    @Slot(int, str, str)
    def _accept_status(self, version: int, status: str, answer: str) -> None:
        with self.lock:
            if not self.running.is_set() or not self.epoch.current(version):
                return
            self.status.emit(status)
            if answer and not self.output_locked:
                self.answer.emit(answer)

    @Slot(int, object, bytes, float)
    def _accept_result(self, version: int, result, image: bytes, elapsed: float) -> None:
        with self.lock:
            if not self.running.is_set() or self.output_locked or not self.epoch.current(version):
                return
            self.answer.emit(result.display_answer)
            self.status.emit(f"实操代码已更新 · 未运行，非确定答案 · {elapsed:.1f} 秒" if result.answer_kind=='practice_code' else (f"必要函数已更新 · 未读取数据文件，非确定答案 · {elapsed:.1f} 秒" if result.answer_kind=='functions' else (f"答案已更新 · {elapsed:.1f} 秒" if result.is_question else "未识别到题目 · 继续观察")))
            if result.is_question:
                try:
                    self.history.add(image, result, elapsed, self.settings.history_limit)
                    self.history_changed.emit()
                except Exception:
                    logging.error("history_write_failed")
                    self.status.emit("答案已更新，但历史保存失败，请检查磁盘权限")

    def _request(self, settings: Settings, key: str, image: bytes, version: int, cancel: threading.Event, query: str = "") -> None:
        started = time.monotonic()
        try:
            with self.lock:
                if self.output_locked or not self.running.is_set() or not self.epoch.current(version):return
            result = AnswerPipeline(settings, key, client_factory=DeepSeekClient).answer(image, cancel, query)
            self._result_event.emit(version, result, image, time.monotonic() - started)
        except Cancelled:
            pass
        except ApiError as exc:
            self._status_event.emit(version, str(exc) + " · 点击重新识题可重试", "暂未获得答案")
        except Exception:
            logging.error("answer_worker_failed")
            self._status_event.emit(version, "答题任务异常，请点击重新识题", "暂未获得答案")
        finally:
            with self.lock:
                self.request_threads.discard(threading.current_thread())

    def _scan(self) -> None:
        reader = None
        previous_serial = -1
        gate = StableGate(1.0)
        locked = None
        missing = 0
        last_full_search = 0.0
        countdown_regions = []
        countdown_tracker = CountdownTracker()
        countdown_ready = False
        countdown_shape = None
        last_countdown_check = 0.0
        previous_request_revision=self.request_revision
        while not self.shutdown.is_set():
            if not self.running.wait(.2):
                continue
            with self.lock:
                settings, key, serial, exclusions = deepcopy(self.settings), self.key, self.serial, list(self.exclusions)
                frame_revision=self.request_revision
            try:
                if reader is None:
                    reader = OcrReader()
                with self.lock:
                    if self.shutdown.is_set() or serial != self.serial or not self.running.is_set():
                        continue
                if previous_request_revision!=self.request_revision:
                    previous_request_revision=self.request_revision
                    gate=StableGate(settings.stable_ms/1000)
                if previous_serial != serial:
                    gate = StableGate(settings.stable_ms / 1000)
                    previous_serial, locked, missing, last_full_search = serial, None, 0, 0
                    countdown_regions, countdown_ready, countdown_shape = [], False, None
                    countdown_tracker = CountdownTracker()
                    last_countdown_check = 0.0
                if settings.mode == 'auto':
                    from .auto_scan import run_auto_session
                    run_auto_session(self, reader, settings, key, serial, grab_fn=grab, monitors_fn=monitors)
                    continue
                screens = monitors()
                if settings.monitor_index >= len(screens):
                    raise ValueError("显示器已断开，请暂停后重新选择")
                screen = screens[settings.monitor_index]
                if settings.mode == "auto" and (locked is None or time.monotonic() - last_full_search > 12):
                    with self.lock:
                        if self.shutdown.is_set() or serial != self.serial or not self.running.is_set():
                            continue
                        image = grab(screen, exclusions)
                    boxes = reader.read_screen(image) if hasattr(reader, 'read_screen') else reader.read(image)
                    with self.lock:
                        if self.shutdown.is_set() or serial != self.serial or not self.running.is_set():
                            continue
                    local = locate_question(boxes, image.width, image.height)
                    last_full_search = time.monotonic()
                    if local is None:
                        with self.lock:
                            if serial != self.serial or not self.running.is_set():
                                continue
                            if locked is not None:
                                self.epoch.invalidate()
                            locked = None
                            gate = StableGate(settings.stable_ms / 1000)
                            version, _ = self.epoch.token()
                        self._status_event.emit(version, "全屏搜索中 · 未找到明显题目，可改用手动框选", "等待题目")
                        self.shutdown.wait(settings.scan_interval_ms / 1000)
                        continue
                    found = Rect(screen.x + local.x, screen.y + local.y, local.width, local.height)
                    if locked is not None and max(abs(locked.x-found.x),abs(locked.y-found.y),abs(locked.width-found.width),abs(locked.height-found.height)) <= 8:
                        found = locked  # OCR边缘1–几像素抖动不是换题，不取消正常请求。
                    if locked != found:
                        locked = found
                        gate = StableGate(settings.stable_ms / 1000)
                        self.epoch.invalidate()
                        self.region_changed.emit(found)
                        countdown_ready = False
                        countdown_tracker = CountdownTracker()
                rect = locked if settings.mode == "auto" else settings.capture_rect
                if rect is None:
                    raise ValueError("请先框选题目区域")
                with self.lock:
                    if self.shutdown.is_set() or serial != self.serial or not self.running.is_set():
                        continue
                    image = grab(rect, exclusions)
                # 先定位明确的顶部状态倒计时，否则整框每秒变化会永远达不到稳定。
                # 只修改用于稳定检测/模型的副本，不修改用户框选坐标。
                if not countdown_ready or countdown_shape != image.size:
                    timer_boxes = reader.read(image)
                    with self.lock:
                        if self.shutdown.is_set() or serial != self.serial or not self.running.is_set():
                            continue
                    countdown_regions = countdown_tracker.update(timer_boxes, image.width, image.height)
                    countdown_shape, countdown_ready = image.size, True
                    last_countdown_check = time.monotonic()
                elif time.monotonic() - last_countdown_check >= (2.5 if countdown_regions else 1.0):
                    timer_boxes = reader.read(image)
                    with self.lock:
                        if self.shutdown.is_set() or serial != self.serial or not self.running.is_set():
                            continue
                    last_countdown_check = time.monotonic()
                    previous_regions = countdown_regions
                    detected_regions = countdown_tracker.update(timer_boxes, image.width, image.height)
                    if len(detected_regions)==len(previous_regions) and all(max(abs(a.x-b.x),abs(a.y-b.y),abs(a.width-b.width),abs(a.height-b.height))<=8 for a,b in zip(detected_regions,previous_regions)) and timer_masks_still_safe(timer_boxes, previous_regions, countdown_tracker.confirmed):
                        detected_regions=previous_regions
                    if detected_regions or not timer_masks_still_safe(timer_boxes, previous_regions, countdown_tracker.confirmed):
                        countdown_regions = detected_regions
                    if countdown_regions != previous_regions:
                        # 遮罩变化改变了特征基准，重新稳定且让旧任务失效。
                        gate = StableGate(settings.stable_ms / 1000)
                        self.epoch.invalidate()
                image = mask_status_regions(image, countdown_regions)
                changed, stable = gate.observe(image)
                with self.lock:
                    if serial != self.serial or not self.running.is_set():
                        continue
                    if changed:
                        version = self.epoch.invalidate()
                        # 变化会取消原请求/清空旧答案；即使画面随后恢复原题，也要重新提交。
                        gate.submitted_frame = None
                        gate.last_identity = None
                        self._status_event.emit(version, "检测到变化 · 等待画面稳定", "等待题目稳定")
                if stable:
                    current_feature = gate.last
                    # 同一静止画面无需每轮调用 OCR。
                    if same_frame(gate.submitted_frame, current_feature):
                        self.shutdown.wait(settings.scan_interval_ms / 1000)
                        continue
                    boxes = reader.read(image)
                    with self.lock:
                        if self.shutdown.is_set() or serial != self.serial or not self.running.is_set():
                            continue
                    identity = normalized_text(boxes)
                    if not looks_like_question(boxes, settings.mode == "manual"):
                        missing += 1
                        version, _ = self.epoch.token()
                        self._status_event.emit(version, "范围内暂未检测到题目", "等待题目")
                        if settings.mode == "auto" and missing >= 2:
                            locked = None
                    elif gate.should_submit(identity):
                        missing = 0
                        if self.output_locked:
                            self.shutdown.wait(settings.scan_interval_ms/1000)
                            continue
                        with self.lock:
                            if serial != self.serial or not self.running.is_set() or self.output_locked or self.request_revision!=frame_revision:
                                continue
                            # 限制被取消但尚未退出的请求，避免快速换题造成线程洪水。
                            if len(self.request_threads) >= 2:
                                self.shutdown.wait(.1)
                                continue
                            # 每一次实际提交都有新代数，即使渐变累计变化未触发单帧阈值。
                            self.epoch.invalidate()
                            version, cancel = self.epoch.token()
                            gate.mark_submitted(identity)
                            self._status_event.emit(version, "题目已锁定 · 网页检索后请求 DeepSeek" if settings.web_search else "题目已锁定 · 正在请求 DeepSeek", "正在思考")
                            worker = threading.Thread(target=self._request,
                                                      args=(settings, key, png_bytes(image), version, cancel, "\n".join(box.text for box in boxes)), daemon=True)
                            self.request_threads.add(worker)
                            worker.start()
                self.shutdown.wait(settings.scan_interval_ms / 1000)
            except Exception as exc:
                version, _ = self.epoch.token()
                message = str(exc) if isinstance(exc, (ValueError, OSError)) else "本地识题初始化或运行失败，请查看帮助中的诊断路径"
                logging.error("capture_ocr_failed: %s", type(exc).__name__)
                self._status_event.emit(version, message, "识题暂不可用")
                self.shutdown.wait(3)

    def close(self) -> None:
        self.pause()
        self.shutdown.set()
        self.worker.join(timeout=2)
        # OCR/HTTP 都为 daemon，不在 GUI 线程无限等待网络。
        with self.lock:
            for thread in list(self.request_threads):
                thread.join(timeout=.15)
