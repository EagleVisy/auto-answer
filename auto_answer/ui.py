from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import logging
from pathlib import Path
import threading

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction, QColor, QCloseEvent, QFont, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox,
    QFontComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem,
    QMainWindow, QMenu, QMessageBox, QPushButton, QScrollArea, QSlider, QSpinBox, QStackedWidget,
    QSystemTrayIcon, QTextEdit, QVBoxLayout, QWidget)

from .api import ApiError, DeepSeekClient
from .capture import monitors, window_rect
from .engine import Engine
from .models import Rect, Settings
from .overlays import AnswerOverlay, CaptureOverlay, RegionSelector, LockIndicator
from .space_key import SpaceKeyWatcher
from .storage import HistoryStore, KeyStore, SettingsStore
from .theme import STYLE, app_icon


def label(text: str, name: str = "", wrap: bool = False) -> QLabel:
    widget = QLabel(text)
    widget.setObjectName(name)
    widget.setWordWrap(wrap)
    return widget


def button(text: str, callback, name: str = "") -> QPushButton:
    widget = QPushButton(text)
    widget.setObjectName(name)
    widget.clicked.connect(callback)
    return widget


def spin(low: int, high: int, value: int, suffix: str = "") -> QSpinBox:
    widget = QSpinBox()
    widget.setRange(low, high)
    widget.setValue(value)
    widget.setSuffix(suffix)
    return widget


def card(title: str, desc: str = ""):
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(22, 20, 22, 22)
    layout.setSpacing(12)
    layout.addWidget(label(title, "sectionTitle"))
    if desc:
        layout.addWidget(label(desc, "muted", True))
    return frame, layout


class MainWindow(QMainWindow):
    connection_finished = Signal(bool, str)

    def __init__(self, root: Path):
        super().__init__()
        self.root = root
        self.settings_store, self.key_store = SettingsStore(root), KeyStore(root)
        self.settings = self.settings_store.load()
        self.history = HistoryStore(root)
        self.key_warning = ""
        self.hotkey_warning = ""
        try:
            self.key = self.key_store.load()
        except Exception:
            self.key = ""
            self.key_warning = "密钥无法读取，请在 AI 连接中重新设置。"
        self.closing = False
        self.test_cancel = threading.Event()
        self.test_thread = None
        self.selector = None
        self.capture_overlay, self.answer_overlay = CaptureOverlay(), AnswerOverlay()
        self.lock_indicator=LockIndicator()
        self.space_watcher=SpaceKeyWatcher(self)
        self.engine = Engine(self.history)
        self.space_watcher.pressed.connect(self.engine.toggle_output_lock)
        self.space_watcher.unavailable.connect(self._hotkey_unavailable)
        self.engine.output_lock_changed.connect(self._output_lock_changed)
        self.engine.status.connect(self._status)
        self.engine.answer.connect(self._answer)
        self.engine.running_changed.connect(self._running)
        self.engine.history_changed.connect(self.refresh_history)
        self.engine.region_changed.connect(self._auto_region)
        self.capture_overlay.geometry_committed.connect(self._capture_moved)
        self.answer_overlay.geometry_committed.connect(self._answer_moved)
        self.connection_finished.connect(self._connection_result)
        self.setWindowTitle("Auto Answer · 桌面答题助手")
        self.setWindowIcon(app_icon())
        self.resize(1100, 790)
        self.setMinimumSize(910, 660)
        self.setStyleSheet(STYLE)
        self._build_ui()
        self._setup_tray()
        self._restore_visible_coordinates()
        self._sync_overlays()
        self.engine.configure(self.settings, self.key)
        self.exclusion_timer = QTimer(self)
        self.exclusion_timer.timeout.connect(self._update_exclusions)
        self.exclusion_timer.start(250)
        self.refresh_history()
        self._status(self.settings_store.warning or self.key_warning or "就绪 · 尚未采集屏幕")

    def _build_ui(self):
        shell = QWidget()
        shell.setObjectName("shell")
        self.setCentralWidget(shell)
        outer = QHBoxLayout(shell)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(206)
        rail = QVBoxLayout(sidebar)
        rail.setContentsMargins(20, 32, 20, 25)
        rail.setSpacing(9)
        icon = QLabel()
        icon.setPixmap(app_icon().pixmap(44, 44))
        rail.addWidget(icon)
        rail.addWidget(label("Auto Answer", "brand"))
        rail.addWidget(label("让注意力回到题目", "brandSub"))
        rail.addSpacing(30)
        self.stack = QStackedWidget()
        self.nav_buttons = []
        for i, title in enumerate(["工作台", "AI 连接", "答案样式", "答题记录", "使用与隐私"]):
            nav = button(title, lambda checked=False, index=i: self._navigate(index), "nav")
            nav.setCheckable(True)
            nav.setAccessibleName(title)
            rail.addWidget(nav)
            self.nav_buttons.append(nav)
        rail.addStretch()
        rail.addWidget(label("本地识题 / 官方 API\n只保留最终答案", "brandSub", True))
        rail.addSpacing(10)
        rail.addWidget(label("VERSION 1.6.0", "brandSub"))
        outer.addWidget(sidebar)
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(30, 26, 30, 22)
        layout.addWidget(self.stack, 1)
        footer = QHBoxLayout()
        footer.addWidget(label("用于自学与允许辅助的练习", "muted"))
        footer.addStretch()
        footer.addWidget(button("保存设置", self.save_settings))
        layout.addLayout(footer)
        outer.addWidget(content, 1)
        self._build_dashboard()
        self._build_connection()
        self._build_appearance()
        self._build_history()
        self._build_help()
        self._navigate(0)

    def _page(self, eyebrow: str, title: str, subtitle: str):
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(14)
        layout.addWidget(label(eyebrow, "eyebrow"))
        layout.addWidget(label(title, "title"))
        layout.addWidget(label(subtitle, "subtitle", True))
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 10, 8, 4)
        body_layout.setSpacing(16)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        layout.addWidget(scroll, 1)
        self.stack.addWidget(container)
        return body_layout

    def _build_dashboard(self):
        layout = self._page("WORKSPACE / 01", "专注这一题。", "选择题目范围，放好答案位置。开始后，题目变化会自动触发下一次作答。")
        self.status_label = label("就绪", "status", True)
        layout.addWidget(self.status_label)
        frame, section = card("识题范围", "自动搜索在本机定位题目；手动框选更适合复杂公式、图表和多题页面。")
        form = QFormLayout()
        form.setSpacing(12)
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("手动框选 · 精确指定", "manual")
        self.mode_combo.addItem("自动搜索 · 全屏定位", "auto")
        self.mode_combo.setCurrentIndex(0 if self.settings.mode == "manual" else 1)
        self.monitor_combo = QComboBox()
        for i, monitor in enumerate(monitors()):
            self.monitor_combo.addItem(f"显示器 {i + 1} · {monitor.width} × {monitor.height}", i)
        self.monitor_combo.setCurrentIndex(min(self.settings.monitor_index, self.monitor_combo.count() - 1))
        form.addRow("识题方式", self.mode_combo)
        form.addRow("自动搜索显示器", self.monitor_combo)
        section.addLayout(form)
        row = QHBoxLayout()
        row.addWidget(button("框选题目区域", lambda: self.select_region("capture")))
        row.addWidget(button("框选答案位置", lambda: self.select_region("answer")))
        section.addLayout(row)
        self.rect_label = label(self._rect_text(), "muted", True)
        section.addWidget(self.rect_label)
        self.capture_check = QCheckBox("显示题目范围编辑边框")
        self.capture_check.setChecked(self.settings.show_capture)
        self.answer_check = QCheckBox("显示置顶答案")
        self.answer_check.setChecked(self.settings.show_answer)
        section.addWidget(self.capture_check)
        section.addWidget(self.answer_check)
        layout.addWidget(frame)
        frame, section = card("运行控制")
        row = QHBoxLayout()
        self.start_button = button("开始识题", self.toggle_running, "primary")
        self.retry_button = button("重新识题", self.retry)
        row.addWidget(self.start_button, 1)
        row.addWidget(self.retry_button)
        self.output_lock_button=button('锁定输出 · 空格',self.engine.toggle_output_lock)
        self.output_lock_button.setEnabled(False)
        row.addWidget(self.output_lock_button)
        row.addWidget(button("最小化，保留答案", self.showMinimized))
        section.addLayout(row)
        section.addWidget(label("最小化只隐藏编辑边框，答案仍置顶；恢复窗口后可拖动和调整范围。\n长答案可以在恢复后的答案框中滚动，也会完整保存在记录中。", "muted", True))
        advanced = QFormLayout()
        self.scan_spin = spin(250, 10000, self.settings.scan_interval_ms, " ms")
        self.stable_spin = spin(300, 10000, self.settings.stable_ms, " ms")
        advanced.addRow("画面采样间隔", self.scan_spin)
        advanced.addRow("画面稳定等待", self.stable_spin)
        self.auto_refresh_spin = spin(100, 2000, self.settings.auto_refresh_ms, " ms")
        self.auto_stable_spin = spin(200, 5000, self.settings.auto_stable_ms, " ms")
        self.auto_timeout_spin = spin(1000, 30000, self.settings.auto_lock_timeout_ms, " ms")
        advanced.addRow("自动定位刷新间隔", self.auto_refresh_spin)
        advanced.addRow("自动范围稳定等待", self.auto_stable_spin)
        advanced.addRow("自动定位锁定超时", self.auto_timeout_spin)
        section.addLayout(advanced)
        section.addWidget(label("自动模式先刷新候选范围；范围稳定或达到锁定超时后，再发送完整截图。实际刷新还受本机OCR耗时影响；无题目或明确缺失选项时不会锁空白。", "muted", True))
        layout.addWidget(frame)
        preview = QFrame()
        preview.setObjectName("preview")
        pv = QVBoxLayout(preview)
        pv.setContentsMargins(22, 18, 22, 20)
        pv.addWidget(label("最新答案 / 仅最终内容", "previewLabel"))
        self.preview_answer = label("等待开始", "previewAnswer", True)
        pv.addWidget(self.preview_answer)
        layout.addWidget(preview)
        layout.addStretch()
        for control in (self.mode_combo, self.monitor_combo):
            control.currentIndexChanged.connect(self._mode_changed)
        self.capture_check.toggled.connect(self._visibility_changed)
        self.answer_check.toggled.connect(self._visibility_changed)

    def _build_connection(self):
        layout = self._page("CONNECTION / 02", "连接你的 DeepSeek。", "请求只发送至 DeepSeek 官方接口。密钥由 Windows 当前账户加密保存。")
        frame, section = card("API Key", "密钥不会进入截图记录、日志或发布文件。请输入你自己的官方 API Key。")
        self.key_input = QLineEdit()
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setPlaceholderText("已有密钥，留空保留" if self.key else "在此输入官方 API Key")
        self.key_input.setAccessibleName("DeepSeek API Key")
        section.addWidget(self.key_input)
        row = QHBoxLayout()
        row.addWidget(button("保存密钥", self.save_key, "primary"))
        self.test_button = button("测试连接", self.test_connection)
        row.addWidget(self.test_button)
        row.addWidget(button("忘记密钥", self.forget_key, "danger"))
        section.addLayout(row)
        self.connection_label = label("密钥已保存" if self.key else "尚未设置密钥", "muted", True)
        section.addWidget(self.connection_label)
        layout.addWidget(frame)
        frame, section = card("速度与思考", "提示词可建议简短思考，但不能硬性保证推理字数。响应速度也受服务负载和网络影响。")
        form = QFormLayout()
        form.setSpacing(12)
        form.addRow("官方模型", label("deepseek-flash · 支持图像理解"))
        self.effort_combo = QComboBox()
        for text, value in [("关闭思考 · 优先实时性", "none"), ("低强度 · 推荐", "low"), ("高强度 · 复杂题", "high"), ("最高强度 · 较慢", "max")]:
            self.effort_combo.addItem(text, value)
        self.effort_combo.setCurrentIndex(self.effort_combo.findData(self.settings.effort))
        self.words_spin = spin(20, 2000, self.settings.reasoning_words, " 字（建议）")
        self.tokens_spin = spin(512, 32768, self.settings.max_tokens, " tokens")
        self.tokens_spin.setSingleStep(512)
        self.timeout_spin = spin(5, 180, self.settings.timeout_seconds, " 秒")
        form.addRow("思考强度", self.effort_combo)
        form.addRow("提示词思考建议", self.words_spin)
        form.addRow("总输出预算", self.tokens_spin)
        form.addRow("单次请求超时", self.timeout_spin)
        section.addLayout(form)
        section.addWidget(label("输出预算包含推理与最终作答。预算太低可能导致没有完整答案；程序会提示截断，而不会显示残缺内容。", "muted", True))
        layout.addWidget(frame)
        frame, section = card("网页搜索辅助", "真实查询 Bing 并读取少量公开网页，再结合截图作答。DeepSeek API 本身不执行网页搜索。")
        self.search_check = QCheckBox("启用网页搜索辅助（题目文字发送至 Bing）")
        self.search_check.setChecked(self.settings.web_search)
        section.addWidget(self.search_check)
        section.addWidget(label("检索耗时计入单次请求超时；失败会明确提示，不冒称已联网。真实来源保存在记录详情，答案层仍只显示最终答案。", "muted", True))
        layout.addWidget(frame)
        layout.addStretch()

    def _build_appearance(self):
        layout = self._page("DISPLAY / 03", "把答案放在顺手的位置。", "答案层不抢占当前任务焦点。编辑边框只用于定位与缩放，不影响后台范围。")
        frame, section = card("文字与背景")
        form = QFormLayout()
        form.setSpacing(12)
        self.font_combo = QFontComboBox()
        self.font_combo.setCurrentFont(QFont(self.settings.font_family))
        self.font_spin = spin(6, 72, self.settings.font_size, " pt")
        self.text_color_button = button("", lambda: self.pick_color("text_color"))
        self.bg_color_button = button("", lambda: self.pick_color("background_color"))
        self.opacity_spin = spin(0, 100, self.settings.opacity, " %")
        form.addRow("字体", self.font_combo)
        form.addRow("字号", self.font_spin)
        form.addRow("文字颜色", self.text_color_button)
        form.addRow("背景不透明度", self.opacity_spin)
        form.addRow("背景颜色", self.bg_color_button)
        section.addLayout(form)
        section.addWidget(button("重新放置答案框", lambda: self.select_region("answer"), "primary"))
        section.addWidget(label("答案区域太小会产生滚动条。最小化时鼠标可穿透答案；需要查看长答案时恢复设置窗口，或在答题记录中查看全文。", "muted", True))
        layout.addWidget(frame)
        frame, section = card("本地记录")
        self.history_limit_spin = spin(10, 2000, self.settings.history_limit, " 条")
        form = QFormLayout()
        form.addRow("最多保留", self.history_limit_spin)
        section.addLayout(form)
        section.addWidget(label("超过数量时删除最旧的记录与截图；你也可以随时单独删除或全部清空。", "muted", True))
        layout.addWidget(frame)
        layout.addStretch()
        self._color_buttons()
        for control in (self.font_spin, self.opacity_spin):
            control.valueChanged.connect(self._preview_style)
        self.font_combo.currentFontChanged.connect(self._preview_style)

    def _build_history(self):
        layout = self._page("HISTORY / 04", "留下一份练习记录。", "题目截图和最终答案仅保存在本机。思考过程不显示、不保存。")
        row = QHBoxLayout()
        self.history_search = QLineEdit()
        self.history_search.setPlaceholderText("搜索题目或答案")
        self.history_search.setAccessibleName("搜索答题记录")
        self.history_search.textChanged.connect(self.refresh_history)
        row.addWidget(self.history_search, 1)
        row.addWidget(button("刷新", self.refresh_history))
        row.addWidget(button("全部清空", self.clear_history, "danger"))
        layout.addLayout(row)
        self.history_list = QListWidget()
        self.history_list.setIconSize(QSize(88, 62))
        self.history_list.setMinimumHeight(185)
        self.history_list.currentItemChanged.connect(self._history_selected)
        layout.addWidget(self.history_list, 1)
        self.history_count = label("暂无记录", "muted")
        layout.addWidget(self.history_count)
        details = QHBoxLayout()
        self.history_image = QLabel("选择一条记录查看截图")
        self.history_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.history_image.setFixedSize(260, 180)
        self.history_image.setStyleSheet("background:#E8EDE6; border-radius:9px; color:#60746D;")
        self.history_text = QTextEdit()
        self.history_text.setReadOnly(True)
        self.history_text.setMinimumHeight(180)
        details.addWidget(self.history_image)
        details.addWidget(self.history_text, 1)
        layout.addLayout(details)
        actions = QHBoxLayout()
        actions.addWidget(button("查看原图与完整答案", self.open_history))
        actions.addWidget(button("删除选中记录", self.delete_history, "danger"))
        actions.addStretch()
        layout.addLayout(actions)

    def _build_help(self):
        layout = self._page("GUIDE / 05", "先看清边界，再开始。", "自动识题是辅助工具，不代替核对，也不保证模型答案绝对正确。")
        sections = [
            ("开始使用", "先保存官方 API Key，再框选题目范围和答案位置。点击开始后，本地检测到稳定题目才会上传截图。自动模式会搜索所选显示器，复杂图形题建议改用手动。"),
            ("窗口行为", "编辑状态可拖动顶部标题、拖右下角缩放。设置窗口最小化后，题目边框和答案编辑标题隐藏，答案保留并鼠标穿透。独立置顶适用于普通桌面窗口，不保证覆盖安全桌面或独占全屏。"),
            ("隐私与费用", "全屏扫描可能读到个人信息；尽量使用手动范围。题目截图发送至 https://api.deepseek.com，产生你账户的 API 费用。程序会遮罩自身答案和可见设置窗口；不提供录屏规避或监考绕过。"),
            ("不确定与失败", "看不清、缺少题干或不确定时显示无法确定。API Key、余额、限流、超时与截断会给出提示，点击重新识题可重试。重复静止画面不会持续消耗 API 额度。"),
            ("本地数据位置", str(self.root) + "\nsettings.json：公开设置；api-key.dpapi：加密密钥；history.sqlite3 与 screenshots：记录；diagnostic.log：不含截图、答案或密钥的故障类型。删除历史会同时删除对应截图。"),
        ]
        for title, text in sections:
            frame, section = card(title, text)
            layout.addWidget(frame)
        layout.addStretch()

    def _navigate(self, index):
        self.stack.setCurrentIndex(index)
        for i, nav in enumerate(self.nav_buttons):
            nav.setChecked(i == index)
        if index == 3:
            self.refresh_history()

    def _rect_text(self):
        rect = self.settings.capture_rect
        return f"题目范围：({rect.x}, {rect.y}) · {rect.width} × {rect.height} 像素" if rect else "尚未框选题目范围"

    def _collect_settings(self) -> Settings:
        result = deepcopy(self.settings)
        result.mode = self.mode_combo.currentData()
        result.monitor_index = self.monitor_combo.currentData()
        result.effort = self.effort_combo.currentData()
        result.reasoning_words = self.words_spin.value()
        result.max_tokens = self.tokens_spin.value()
        result.timeout_seconds = self.timeout_spin.value()
        result.scan_interval_ms = self.scan_spin.value()
        result.stable_ms = self.stable_spin.value()
        result.auto_refresh_ms = self.auto_refresh_spin.value()
        result.auto_stable_ms = self.auto_stable_spin.value()
        result.auto_lock_timeout_ms = self.auto_timeout_spin.value()
        result.font_family = self.font_combo.currentFont().family()
        result.font_size = self.font_spin.value()
        result.opacity = self.opacity_spin.value()
        result.show_capture = self.capture_check.isChecked()
        result.show_answer = self.answer_check.isChecked()
        result.history_limit = self.history_limit_spin.value()
        result.web_search = self.search_check.isChecked()
        result.validate()
        return result

    def save_settings(self, checked=False, notify=True) -> bool:
        try:
            updated = self._collect_settings()
            self.settings_store.save(updated)
            self.settings = updated
            self.engine.configure(updated, self.key)
            self._sync_overlays()
            self.rect_label.setText(self._rect_text())
            if notify:
                self._status("设置已保存" + (" · 等待新题目" if self.engine.running.is_set() else " · 尚未采集"))
            return True
        except Exception as exc:
            message = str(exc) if isinstance(exc, ValueError) else "保存失败，请检查数据目录权限或磁盘空间"
            self.error(message)
            return False

    def _mode_changed(self, *args):
        self.monitor_combo.setEnabled(self.mode_combo.currentData() == "auto")
        if hasattr(self, "effort_combo"):
            self.save_settings(notify=False)

    def _visibility_changed(self, *args):
        self.settings.show_capture = self.capture_check.isChecked()
        self.settings.show_answer = self.answer_check.isChecked()
        self._sync_overlays()
        try:
            self.settings_store.save(self.settings)
        except OSError:
            self.error("无法保存显示开关，请检查磁盘权限")

    def _preview_style(self, *args):
        current = deepcopy(self.settings)
        current.font_family = self.font_combo.currentFont().family()
        current.font_size = self.font_spin.value()
        current.opacity = self.opacity_spin.value()
        self.answer_overlay.apply_settings(current)

    def _color_buttons(self):
        self.text_color_button.setText(self.settings.text_color + " · 选择")
        self.bg_color_button.setText(self.settings.background_color + " · 选择")

    def pick_color(self, field):
        chosen = QColorDialog.getColor(QColor(getattr(self.settings, field)), self, "选择颜色")
        if chosen.isValid():
            setattr(self.settings, field, chosen.name())
            self._color_buttons()
            self._preview_style()

    def save_key(self, checked=False) -> bool:
        new_key = self.key_input.text().strip()
        if not new_key and self.key:
            return True
        try:
            self.key_store.save(new_key)
            self.key = new_key
            self.key_input.clear()
            self.key_input.setPlaceholderText("已有密钥，留空保留")
            self.connection_label.setText("密钥已加密保存")
            self.engine.configure(self.settings, self.key)
            return True
        except Exception as exc:
            self.error(str(exc) if isinstance(exc, ValueError) else "密钥保存失败，请检查 Windows 用户和磁盘权限")
            return False

    def forget_key(self):
        if QMessageBox.question(self, "忘记密钥", "删除本机保存的 API Key，并暂停识题？") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.engine.pause()
            self.key_store.clear()
            self.key = ""
            self.key_input.clear()
            self.key_input.setPlaceholderText("在此输入官方 API Key")
            self.connection_label.setText("已删除密钥")
            self.engine.configure(self.settings, "")
        except OSError:
            self.error("删除密钥失败，请检查文件权限")

    def test_connection(self):
        if self.test_thread and self.test_thread.is_alive():
            return
        if self.key_input.text() and not self.save_key():
            return
        if not self.key:
            self.error("请先保存 API Key")
            return
        self.test_button.setEnabled(False)
        self.connection_label.setText("正在测试官方连接…")
        settings, key = deepcopy(self.settings), self.key
        self.test_cancel.clear()
        def task():
            try:
                message = DeepSeekClient(settings, key).test_connection(self.test_cancel)
                self.connection_finished.emit(True, message)
            except ApiError as exc:
                self.connection_finished.emit(False, str(exc))
        self.test_thread = threading.Thread(target=task, daemon=True)
        self.test_thread.start()

    def _connection_result(self, success, message):
        if not self.closing:
            self.test_button.setEnabled(True)
            self.connection_label.setText(message)

    def toggle_running(self):
        if self.engine.running.is_set():
            self.engine.pause()
            return
        if not self.save_settings(notify=False):
            return
        if not self.key:
            self._navigate(1)
            self.error("请先在此页保存自己的官方 API Key")
            return
        if self.settings.web_search and not self.settings.search_privacy_accepted:
            answer = QMessageBox.question(self, "开启网页搜索", "将题目 OCR 文字发送至 Bing，读取相关公开网页作为参考。\n网页可能包含不准确信息；检索失败会明确提示。\n\n同意并启用？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.settings.search_privacy_accepted = True
            if not self.save_settings(notify=False):
                return
        if not self.settings.privacy_accepted:
            answer = QMessageBox.question(self, "开启截图识题", "题目截图将发送至 DeepSeek 官方 API，并产生你的账户费用。\n自动全屏搜索可能包含私人信息，建议只框选题目。\n仅用于允许 AI 辅助的练习；模型可能回答错误。\n\n你是否同意并开始？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            self.settings.privacy_accepted = True
            if not self.save_settings(notify=False):
                return
        try:
            self.engine.start()
        except (ValueError, OSError) as exc:
            self.error(str(exc))

    def retry(self):
        if not self.engine.running.is_set():
            self.toggle_running()
        else:
            if self.save_settings(notify=False):
                self._status("重新检测当前题目")

    def _status(self, text):
        self.status_label.setText(text+(' · '+self.hotkey_warning if self.hotkey_warning else ''))

    def _hotkey_unavailable(self,text):
        self.hotkey_warning=text
        self.output_lock_button.setToolTip(text)
        self._status('空格监听暂不可用')

    def _answer(self, text):
        if self.engine.output_locked:return
        self.answer_overlay.set_answer(text)
        self.preview_answer.setText(text if len(text) <= 500 else text[:500] + "\n…全文见答案框或答题记录")

    def _running(self, running):
        self.start_button.setText("暂停识题" if running else "开始识题")
        self.hotkey_warning=''
        self.space_watcher.set_enabled(running)
        self.output_lock_button.setEnabled(running)
        if not running:self._output_lock_changed(False)

    def _output_lock_changed(self,locked):
        self.output_lock_button.setText('解锁输出 · 空格' if locked else '锁定输出 · 空格')
        self._sync_lock_indicator()
        self._update_exclusions()

    def _sync_lock_indicator(self):
        self.lock_indicator.anchor(self.settings.answer_rect)
        if self.engine.running.is_set() and self.engine.output_locked:
            self.lock_indicator.show();self.lock_indicator.raise_()
        else:self.lock_indicator.hide()

    def _restore_visible_coordinates(self):
        screens = monitors()
        if not any(screen.contains(self.settings.answer_rect) for screen in screens):
            primary = screens[0]
            self.settings.answer_rect = Rect(primary.x + 40, primary.y + 100, min(460, primary.width - 80), min(230, primary.height - 150))
        if self.settings.capture_rect and not any(s.contains(self.settings.capture_rect) for s in screens):
            self.settings.capture_rect = None
        self.monitor_combo.setEnabled(self.settings.mode == "auto")

    def _sync_overlays(self):
        editing = not self.isMinimized() and self.isVisible()
        self.answer_overlay.set_physical_rect(self.settings.answer_rect)
        self.answer_overlay.apply_settings(self.settings)
        self.answer_overlay.set_editing(editing)
        if self.settings.show_answer:
            self.answer_overlay.show()
        else:
            self.answer_overlay.hide()
        self.capture_overlay.set_editing(True)
        if self.settings.capture_rect:
            self.capture_overlay.set_physical_rect(self.settings.capture_rect)
        if editing and self.settings.show_capture and self.settings.capture_rect:
            self.capture_overlay.show()
        else:
            self.capture_overlay.hide()
        self._sync_lock_indicator()
        self._update_exclusions()

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self._sync_overlays)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.WindowStateChange:
            QTimer.singleShot(0, self._sync_overlays)

    def _capture_moved(self, rect):
        self.settings.capture_rect = rect
        self.save_settings(notify=False)

    def _answer_moved(self, rect):
        self.settings.answer_rect = rect
        self._sync_lock_indicator()
        try:
            self.settings_store.save(self._collect_settings())
        except Exception:
            self.error("无法保存答案位置")

    def _auto_region(self, rect):
        if not self.engine.running.is_set() or self.settings.mode != "auto":
            return
        self.settings.capture_rect = rect
        self.capture_overlay.set_physical_rect(rect)
        self.rect_label.setText(self._rect_text())
        self._sync_overlays()

    def select_region(self, kind):
        if self.selector is not None:
            return
        self.was_running_before_selection = self.engine.running.is_set()
        self.engine.pause()
        self.capture_overlay.hide()
        self.answer_overlay.hide()
        self.hide()
        self.selector = RegionSelector()
        self.selector.selected.connect(lambda rect: self._selected_region(kind, rect))
        self.selector.cancelled.connect(self._cancel_selection)
        self.selector.open()

    def _selected_region(self, kind, rect):
        self.selector = None
        if kind == "capture":
            self.settings.capture_rect = rect
            self.mode_combo.setCurrentIndex(0)
        else:
            # 答案文本区至少 100x70 逻辑像素，并回写实际尺寸，避免显示/设置不一致。
            self.answer_overlay.set_physical_rect(rect)
            self.settings.answer_rect = self.answer_overlay.physical_rect()
        self.showNormal()
        self.activateWindow()
        self.save_settings(notify=False)
        self._resume_after_selection()

    def _cancel_selection(self):
        self.selector = None
        self.showNormal()
        self.activateWindow()
        self._sync_overlays()
        self._resume_after_selection()

    def _resume_after_selection(self):
        if self.was_running_before_selection:
            try:
                self.engine.start()
            except (ValueError, OSError) as exc:
                self.error(str(exc))

    def _update_exclusions(self):
        excluded = []
        # 所有可见应用窗口（包括历史/颜色/消息对话框）都不参与识题。
        for widget in QApplication.topLevelWidgets():
            if widget is self.capture_overlay or not widget.isVisible() or widget.isMinimized():
                continue
            rect = window_rect(int(widget.winId()))
            if rect:
                excluded.append(rect)
        if self.capture_overlay.isVisible():
            excluded.extend(self.capture_overlay.exclusion_rectangles())
        self.engine.set_exclusions(excluded)

    def refresh_history(self, *args):
        if not hasattr(self, "history_list") or self.closing:
            return
        selected = self.history_list.currentItem()
        selected_id = selected.data(Qt.ItemDataRole.UserRole)["id"] if selected else None
        self.history_list.blockSignals(True)
        self.history_list.clear()
        try:
            records = self.history.list(self.history_search.text())
            for record in records:
                created = datetime.fromisoformat(record["created"]).astimezone().strftime("%m-%d %H:%M:%S")
                kind_label=' · 实操代码（未运行）' if record.get('answer_kind')=='practice_code' else (' · 必要函数' if record.get('answer_kind')=='functions' else '')
                text = f"{created}   ·   {record['elapsed']:.1f}s{kind_label}\n{record['question'][:90] or '已识别题目'}\n{record['answer'][:100]}"
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, record)
                from PySide6.QtGui import QIcon
                item.setIcon(QIcon(str(self.history.image_path(record))))
                self.history_list.addItem(item)
                if record["id"] == selected_id:
                    self.history_list.setCurrentItem(item)
            self.history_count.setText(f"{len(records)} 条记录 · 支持单独删除截图与答案")
        except Exception:
            self.history_count.setText("记录读取失败，请检查本地数据目录")
        self.history_list.blockSignals(False)
        self._history_selected(self.history_list.currentItem(), None)

    def _history_selected(self, item, previous):
        if not item:
            self.history_image.clear()
            self.history_image.setText("选择一条记录查看截图")
            self.history_text.clear()
            return
        record = item.data(Qt.ItemDataRole.UserRole)
        image = QPixmap(str(self.history.image_path(record)))
        self.history_image.setPixmap(image.scaled(self.history_image.size(), Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        self.history_text.setPlainText("题目：" + record["question"] + ("\n\n实操代码（未运行，非确定答案）：\n" if record.get('answer_kind')=='practice_code' else ("\n\n必要函数（非确定答案）：\n" if record.get('answer_kind')=='functions' else "\n\n答案：\n")) + record["answer"] + self._source_text(record))

    @staticmethod
    def _source_text(record):
        try:
            sources = json.loads(record.get("sources_json", "[]"))
        except (ValueError, TypeError):
            sources = []
        return "\n\n实际网页检索来源：\n" + "\n".join(source.get("title", "") + "\n" + source.get("url", "") for source in sources) if sources else ""

    def open_history(self):
        item = self.history_list.currentItem()
        if not item:
            return
        record = item.data(Qt.ItemDataRole.UserRole)
        dialog = QDialog(self)
        dialog.setWindowTitle("题目截图与完整答案")
        dialog.resize(920, 710)
        layout = QVBoxLayout(dialog)
        image = QPixmap(str(self.history.image_path(record)))
        viewer = QLabel()
        viewer.setPixmap(image)
        scroll = QScrollArea()
        scroll.setWidget(viewer)
        layout.addWidget(scroll, 3)
        answer = QTextEdit()
        answer.setReadOnly(True)
        answer.setPlainText(record["question"] + ("\n\n实操代码（未运行，非确定答案）：\n" if record.get('answer_kind')=='practice_code' else ("\n\n必要函数（非确定答案）：\n" if record.get('answer_kind')=='functions' else "\n\n")) + record["answer"] + self._source_text(record))
        layout.addWidget(answer, 2)
        controls = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        controls.rejected.connect(dialog.reject)
        layout.addWidget(controls)
        dialog.exec()

    def delete_history(self):
        item = self.history_list.currentItem()
        if not item:
            return
        try:
            self.history.delete(item.data(Qt.ItemDataRole.UserRole)["id"])
            self.refresh_history()
        except OSError:
            self.error("无法删除截图，请检查文件权限或关闭占用图片的程序")

    def clear_history(self):
        if QMessageBox.question(self, "清空记录", "永久删除所有本地题目截图与答案？此操作无法撤销。", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            self.history.clear()
            self.refresh_history()
        except OSError:
            self.error("部分截图无法删除，请检查文件权限后重试")

    def _setup_tray(self):
        self.tray = QSystemTrayIcon(app_icon(), self)
        self.tray.setToolTip("Auto Answer · 双击恢复设置")
        menu = QMenu()
        show_action = QAction("打开设置", self)
        show_action.triggered.connect(self._restore)
        pause_action = QAction("暂停识题", self)
        pause_action.triggered.connect(self.engine.pause)
        quit_action = QAction("退出程序", self)
        quit_action.triggered.connect(self.close)
        menu.addAction(show_action)
        menu.addAction(pause_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda reason: self._restore() if reason == QSystemTrayIcon.ActivationReason.DoubleClick else None)
        self.tray.show()

    def _restore(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._sync_overlays()

    def error(self, message):
        QMessageBox.warning(self, "Auto Answer", message)

    def closeEvent(self, event: QCloseEvent):
        if self.closing:
            event.accept()
            return
        self.closing = True
        self.exclusion_timer.stop()
        self.space_watcher.close()
        self.lock_indicator.close()
        self.test_cancel.set()
        try:
            self.settings_store.save(self._collect_settings())
        except Exception:
            logging.error("settings_on_exit_failed")
        self.engine.close()
        if self.selector:
            self.selector._dispose()
        self.capture_overlay.close()
        self.answer_overlay.close()
        self.tray.hide()
        self.history.close()
        event.accept()
        QApplication.quit()
