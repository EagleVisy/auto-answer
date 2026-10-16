from __future__ import annotations

import ctypes
from ctypes import wintypes
import io
import json
from pathlib import Path
import threading
import time

from PIL import Image, ImageDraw, ImageFont
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from .capture import grab, monitors, png_bytes, window_rect
from .detection import OcrReader, locate_question
from .models import AnswerResult, Rect
from .overlays import logical_to_physical, physical_to_logical
from .storage import KeyStore


def run_self_test(window, report: Path, screenshot: Path | None) -> int:
    checks = []
    def check(name, function):
        started = time.monotonic()
        try:
            details = function()
            checks.append({"name": name, "passed": True, "seconds": round(time.monotonic() - started, 3), "details": details})
        except Exception as exc:
            checks.append({"name": name, "passed": False, "seconds": round(time.monotonic() - started, 3), "error_type": type(exc).__name__, "error": str(exc)[:300]})
    def assert_that(condition, text):
        if not condition:
            raise AssertionError(text)
    def ui_pages():
        for index in range(5):
            window._navigate(index)
            QApplication.processEvents()
            assert_that(window.stack.currentIndex() == index, "导航页面未切换")
        window._navigate(0)
        window._answer("B")
        QApplication.processEvents()
        if screenshot:
            screenshot.parent.mkdir(parents=True, exist_ok=True)
            assert_that(window.grab().save(str(screenshot)), "界面截图保存失败")
        return "设置页面可切换、答案显示正常"
    check("settings_pages_and_answer", ui_pages)

    def overlay_lifecycle():
        screen = monitors()[0]
        window.settings.capture_rect = Rect(screen.x + 100, screen.y + 160, min(700, screen.width - 200), min(330, screen.height - 250))
        window._sync_overlays()
        QApplication.processEvents()
        assert_that(window.capture_overlay.isVisible(), "编辑模式未显示采集边框")
        assert_that(window.answer_overlay.editing, "答案框未进入编辑模式")
        # 失焦后置顶标记应仍保留；不以 grab 排除保护伪装成隐身。
        assert_that(bool(window.answer_overlay.windowFlags() & Qt.WindowType.WindowStaysOnTopHint), "答案框未置顶")
        window.showMinimized()
        QTest.qWait(150)
        QApplication.processEvents()
        assert_that(not window.capture_overlay.isVisible(), "最小化后采集边框未隐藏")
        assert_that(window.answer_overlay.isVisible(), "最小化后答案被隐藏")
        assert_that(not window.answer_overlay.editing, "最小化后答案编辑边框仍启用")
        assert_that(window.answer_overlay.text.toPlainText() == "B", "最小化后答案内容丢失")
        user32 = ctypes.WinDLL("user32")
        user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
        style = user32.GetWindowLongPtrW(wintypes.HWND(int(window.answer_overlay.winId())), -20)
        assert_that(bool(style & 0x8), "原生 topmost 标记缺失")
        assert_that(bool(style & 0x20), "最小化后未启用鼠标穿透")
        window.showNormal()
        QTest.qWait(100)
        assert_that(window.answer_overlay.editing and window.capture_overlay.isVisible(), "恢复窗口后编辑框未恢复")
        return "真实 Win32 置顶/鼠标穿透、最小化隐藏边框且保留 B、恢复编辑均通过"
    check("overlay_minimize_restore_native_flags", overlay_lifecycle)

    def coords():
        rect = window.answer_overlay.physical_rect()
        roundtrip = logical_to_physical(physical_to_logical(rect))
        assert_that(abs(rect.x - roundtrip.x) <= 2 and abs(rect.width - roundtrip.width) <= 2, "DPI 坐标往返不匹配")
        original = window_rect(int(window.answer_overlay.winId()))
        assert_that(original is not None and abs(original.width - rect.width) <= 3, "Win32 物理坐标不匹配")
        return {"physical": rect.__dict__, "win32": original.__dict__, "monitors": [m.__dict__ for m in monitors()]}
    check("physical_dpi_coordinates", coords)

    def font_and_geometry():
        old = window.font_spin.value()
        assert_that(window.font_spin.minimum()==6,'字号下限应为6')
        for size in (6,32):
            window.font_spin.setValue(size)
            QApplication.processEvents()
            assert_that(window.answer_overlay.text.font().pointSize()==size,'字号预览未应用')
        assert_that(window.save_settings(notify=False),'小字号设置保存失败')
        window.font_spin.setValue(6)
        assert_that(window.save_settings(notify=False) and window.settings_store.load().font_size==6,'6号保存重读失败')
        window.font_spin.setValue(old)
        rect = window.answer_overlay.physical_rect()
        window._answer_moved(rect)
        assert_that(window.settings_store.load().answer_rect == rect, "答案位置未保存")
        return "字体预览与位置保存通过"
    check("font_preview_and_position_persistence", font_and_geometry)

    def zero_background_opacity():
        from PySide6.QtGui import QImage
        original = window.opacity_spin.value()
        window.opacity_spin.setValue(0)
        assert_that(window.opacity_spin.minimum() == 0, "设置下限不是0")
        assert_that(window.save_settings(notify=False), "0%设置保存失败")
        assert_that(window.settings_store.load().opacity == 0, "0%设置重新加载失败")
        overlay = window.answer_overlay
        overlay.set_answer("答案：B")
        overlay.set_editing(False)
        QApplication.processEvents()
        image = QImage(overlay.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        overlay.render(image)
        assert_that(image.pixelColor(4, 45).alpha() == 0, "0%背景仍不透明")
        assert_that(any(image.pixelColor(x, y).alpha() > 100 for y in range(10, min(75, image.height())) for x in range(10, min(210, image.width()))), "背景透明时文字未绘制")
        window.opacity_spin.setValue(original)
        window.save_settings(notify=False)
        return "背景0%绘制alpha=0，答案文字仍绘制，设置可保存并重新加载"
    check("zero_background_opacity_keeps_answer", zero_background_opacity)

    sample = Image.new("RGB", (1000, 480), "white")
    font_path = Path("C:/Windows/Fonts/msyh.ttc")
    font = ImageFont.truetype(str(font_path), 32)
    draw = ImageDraw.Draw(sample)
    for index, text in enumerate(["1. 下列计算结果正确的是？", "A. 1 + 1 = 3", "B. 1 + 1 = 2", "C. 2 + 2 = 5", "D. 3 + 3 = 7"]):
        draw.text((75, 60 + index * 65), text, fill="#111111", font=font)

    def ocr():
        reader = OcrReader()
        boxes = reader.read(sample)
        text = "\n".join(box.text for box in boxes)
        assert_that("正确" in text and "1" in text, "中文题干识别失败")
        rect = locate_question(boxes, sample.width, sample.height)
        assert_that(rect is not None, "OCR 题目未能定位")
        return {"recognized": text, "located": rect.__dict__}
    check("bundled_ocr_chinese_question_and_location", ocr)

    def ticking_countdown_detection():
        from .dynamic_regions import timer_masks, mask_status_regions
        from .detection import StableGate
        reader = OcrReader()
        base = Image.new("RGB", (1000, 480), "white")
        draw = ImageDraw.Draw(base)
        draw.text((30, 20), "剩余时间：1时57分22秒", fill="black", font=font)
        draw.text((30, 160), "1、判断题：1 + 1 = 2。（ ）", fill="black", font=font)
        draw.text((30, 290), "A. 正确     B. 错误", fill="black", font=font)
        boxes = reader.read(base)
        masks = timer_masks(boxes, base.width, base.height)
        assert_that(len(masks) == 1, "真实OCR未识别顶部倒计时")
        gate = StableGate(1.0)
        stable = False
        for index in range(5):
            frame = base.copy()
            drawer = ImageDraw.Draw(frame)
            drawer.rectangle((25, 10, 420, 70), fill="white")
            drawer.text((30, 20), f"剩余时间：1时57分{22-index}秒", fill="black", font=font)
            changed, stable = gate.observe(mask_status_regions(frame, masks), 10 + index * .7)
        assert_that(stable, "仅倒计时跳动仍不能达到稳定")
        return "真实OCR顶部倒计时遮罩使固定题目正常稳定，题干像素保留；未答截图中的真实题"
    check("ticking_countdown_does_not_block_stability", ticking_countdown_detection)

    def structured_cluttered_question_and_clocks():
        from .dynamic_regions import CountdownTracker, timer_masks, mask_status_regions
        from .detection import StableGate
        reader = OcrReader()
        canvas = Image.new('RGB', (1920, 1080), 'white')
        drawer = ImageDraw.Draw(canvas)
        small = ImageFont.truetype(str(font_path), 24)
        for index in range(15):
            drawer.text((30, 70 + index * 48), '正确学习 阅读解答 题目通知', font=small, fill='black')
        drawer.text((620, 60), '01:57:22', font=font, fill='black')
        drawer.text((620, 240), '12、（单选题）下列结论正确的是（ ）', font=font, fill='black')
        for index, line in enumerate(['A. 第一项', 'B. 第二项', 'C. 第三项', 'D. 第四项']):
            drawer.text((650, 410 + index * 150), line, font=font, fill='black')
        drawer.text((620, 1000), '下一题', font=small, fill='black')
        boxes = reader.read_screen(canvas)
        rect = locate_question(boxes, canvas.width, canvas.height)
        assert_that(rect is not None and rect.x > 500, '误定位侧栏')
        selected = [box.text for box in boxes if rect.contains(box.rect)]
        assert_that(rect.y < 260 and rect.y + rect.height > 890, '题干或末尾选项缺失')
        assert_that(any('第四项' in text for text in selected), '未包含D选项')
        masks = timer_masks(boxes, canvas.width, canvas.height)
        assert_that(bool(masks), '纯数字时钟未识别')
        gate = StableGate(1)
        stable = False
        for index in range(4):
            frame = canvas.copy()
            draw = ImageDraw.Draw(frame)
            draw.rectangle((610, 50, 950, 115), fill='white')
            draw.text((620, 60), f'01:57:{22-index}', font=font, fill='black')
            _, stable = gate.observe(mask_status_regions(frame, masks), 10 + index * .7)
        assert_that(stable, '纯数字时钟仍阻止稳定')
        tracker = CountdownTracker()
        from .detection import TextBox
        question = TextBox(Rect(100, 260, 600, 30), '1、（判断题）以下结论（ ）', .99)
        for index, value in enumerate([120, 119, 118]):
            bare = TextBox(Rect(100, 40, 90, 30), str(value), .99)
            bare_masks = tracker.update([bare, question], 1000, 600, 10 + index)
        assert_that(bool(bare_masks), '裸数字连续递减未识别')
        return {'roi': rect.__dict__, 'selected': selected, 'checks': '杂乱侧栏不选，A-D完整，纯时钟/裸数字可稳定'}
    check('structured_cluttered_locator_and_numeric_clock', structured_cluttered_question_and_clocks)

    def auto_timeout_and_complete_visual():
        from .auto_locator import LocationLock, LocationProposal, propose_location
        from .detection import TextBox
        # 运行实际打包的设置控件、完整图表定位和超时状态机。
        assert_that(window.auto_timeout_spin.value()==window.settings.auto_lock_timeout_ms, '定位超时控件与设置不同步')
        collected=window._collect_settings()
        assert_that(collected.auto_refresh_ms==window.auto_refresh_spin.value(), '快速刷新设置未收集')
        canvas=Image.new('RGB',(1920,1080),'#cccccc')
        ImageDraw.Draw(canvas).rectangle((1370,340,1700,600),fill='blue')
        texts=[TextBox(Rect(500,200,600,30),'1、（单选题）下列哪个结论成立（ ）',.99)]
        texts += [TextBox(Rect(520,350+index*130,260,30),f'{letter}. 选项',.99) for index,letter in enumerate('ABCD')]
        proposal=propose_location(texts,canvas)
        assert_that(proposal is not None and proposal.complete,'完整选项题未确认')
        assert_that(proposal.rect.contains(Rect(1370,340,330,260)),'无关键词图表被裁掉')
        lock=LocationLock(650,1000)
        assert_that(not lock.observe(proposal,10),'首次候选不可立即提交')
        moved=LocationProposal(Rect(proposal.rect.x+24,proposal.rect.y,proposal.rect.width-24,proposal.rect.height),proposal.identity,True,'',4)
        assert_that(not lock.observe(moved,10.5),'运动候选应等待')
        assert_that(lock.observe(proposal,11.1) and '超时' in lock.reason,'定位超时未锁定')
        return {'timeout_ms':collected.auto_lock_timeout_ms,'refresh_ms':collected.auto_refresh_ms,'visual_rect':proposal.rect.__dict__,'checks':'完整非白底图表保留，先候选后稳定/超时锁定'}
    check('auto_fast_locator_timeout_and_visual_bounds', auto_timeout_and_complete_visual)

    def bounded_banner_and_judgement():
        from .auto_locator import propose_location, probe_rect, steady_proposal, detection_frame
        from .detection import TextBox, StableGate
        canvas=Image.new('RGB',(1920,1080),'white')
        ImageDraw.Draw(canvas).rectangle((180,40,1740,340),fill='#447799')
        rows=[TextBox(Rect(600,520,740,30),'1、（判断题）以下结论成立（ ）',.99),TextBox(Rect(620,650,170,30),'正确',.99),TextBox(Rect(620,810,170,30),'错误',.99)]
        proposal=propose_location(rows,canvas)
        assert_that(proposal is not None and proposal.complete,'无AB的判断选项未完整识别')
        assert_that(proposal.rect.y>480 and proposal.rect.height<400,'横幅被并入题框')
        assert_that(proposal.rect.contains(rows[-1].rect),'末选项未包含')
        assert_that(probe_rect(proposal,canvas).height>proposal.rect.height,'补读窗口未与显示框分离')
        next_proposal=propose_location(rows,canvas)
        assert_that(steady_proposal(proposal,next_proposal).rect==proposal.rect,'同题边界不稳')
        plain=Image.new('RGB',(800,380),'white');framed=plain.copy()
        ImageDraw.Draw(framed).rectangle((0,0,799,379),outline='blue',width=5)
        gate=StableGate(.3);gate.observe(detection_frame(plain,[]),10)
        changed,stable=gate.observe(detection_frame(framed,[]),10.5)
        assert_that(not changed and stable,'显示边框参与稳定检测')
        return {'rect':proposal.rect.__dict__,'checks':'题干和两语义选项完整，横幅不并入，探测/显示独立，边框无反馈'}
    check('bounded_banner_judgement_and_overlay_feedback', bounded_banner_and_judgement)

    def radio_options_and_function_guidance():
        from .detection import TextBox
        from .auto_locator import propose_location
        from .practice import needs_external_data
        canvas=Image.new('RGB',(1200,850),'white');draw=ImageDraw.Draw(canvas)
        rows=[TextBox(Rect(100,100,800,30),'1、（单选题）以下哪个结论成立（ ）',.99)]
        for index in range(4):
            y=260+index*130
            draw.ellipse((125,y,149,y+24),outline='#d6d6df',width=1)
            rows.append(TextBox(Rect(180,y-2,650,30),f'第{index+1}项独立正文',.99))
        proposal=propose_location(rows,canvas)
        assert_that(proposal.complete and proposal.option_count==4,'可见控件漏字母未恢复ABCD')
        assert_that(proposal.rect.contains(rows[-1].rect),'末项未完整包含')
        assert_that(needs_external_data('基于demo.xlsx数据集进行回归拟合选择效果图'),'缺文件实操未检测')
        result=AnswerResult.from_dict({'question':'模拟实操','answer':'pd.read_csv(...)\nmodel.fit()','is_question':True,'certain':False,'answer_kind':'functions'})
        assert_that(result.display_answer=='pd.read_csv(...)\nmodel.fit()' and not result.certain,'函数提示被当确定答案或附加用途')
        identifier=window.history.add(png_bytes(canvas),result,.1,200)
        record=next(row for row in window.history.list() if row['id']==identifier)
        assert_that(record['answer_kind']=='functions' and not record['certain'],'函数历史类型未保存')
        window.history.delete(identifier)
        return {'rect':proposal.rect.__dict__,'checks':'4可见控件恢复漏字母，末项完整；缺文件仅函数且不确定，历史保留类型'}
    check('visible_radio_choice_and_functions_only', radio_options_and_function_guidance)

    def history():
        identifier = window.history.add(png_bytes(sample), AnswerResult("下列计算结果正确的是？", "B", True, True), .125, 200)
        window.refresh_history()
        assert_that(window.history_list.count() >= 1, "历史 UI 未刷新")
        for index in range(window.history_list.count()):
            item = window.history_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole)["id"] == identifier:
                window.history_list.setCurrentItem(item)
                break
        QApplication.processEvents()
        assert_that("B" in window.history_text.toPlainText(), "历史答案未显示")
        assert_that(window.history_image.pixmap() is not None, "历史截图未显示")
        window.delete_history()
        assert_that(not any(r["id"] == identifier for r in window.history.list()), "历史未删除")
        assert_that(not (window.history.images / (identifier + ".png")).exists(), "历史截图实体未删除")
        return "截图、答案、历史 UI 展示与实体删除通过"
    check("history_ui_image_answer_delete", history)

    def epoch_gui():
        # 检查 UI 接收端，不唤醒真实截图 worker。
        original_wait = window.engine.running.wait
        window.engine.running.wait = lambda timeout=None: False
        window.engine.running.set()
        version, cancel = window.engine.epoch.token()
        window.engine.epoch.invalidate()
        window._answer("新题等待中")
        window.engine._accept_result(version, AnswerResult("旧题", "OLD", True, True), png_bytes(sample), .2)
        assert_that(window.answer_overlay.text.toPlainText() == "新题等待中", "旧题覆盖新题")
        window.engine.pause()
        window.engine.running.wait = original_wait
        assert_that(not window.engine.running.is_set(), "暂停未生效")
        return "UI 接收端拒绝过期答案；暂停生效"
    check("late_answer_rejected_at_ui_boundary", epoch_gui)

    def output_space_lock_and_indicator():
        from .space_key import SpaceKeyWatcher
        from .function_guidance import fallback_functions
        from .practice import needs_external_data
        original_wait=window.engine.running.wait
        window.engine.running.wait=lambda timeout=None:False
        try:
            window.engine.running.set();window._running(True)
            window._answer('LOCKED-CONTENT')
            version,_=window.engine.epoch.token()
            assert_that(window.engine.toggle_output_lock(),'输出未锁定')
            QApplication.processEvents()
            window._answer('MUST-NOT-CHANGE')
            window.engine._accept_result(version,AnswerResult('旧题','OLD',True,True),png_bytes(sample),.1)
            assert_that(window.answer_overlay.text.toPlainText()=='LOCKED-CONTENT','锁定输出被覆盖')
            window.showMinimized();QApplication.processEvents();window._sync_overlays()
            assert_that(window.lock_indicator.isVisible(),'最小化小锁隐藏')
            assert_that(window.lock_indicator.size().width()==12 and window.lock_indicator.size().height()==14,'小锁尺寸未缩小')
            window.settings.show_answer=False;window._sync_overlays();QApplication.processEvents()
            assert_that(window.lock_indicator.isVisible(),'隐藏答案内容小锁隐藏')
            assert_that(window.engine.toggle_output_lock() is False and not window.lock_indicator.isVisible(),'解锁标记未消失')
            window.settings.show_answer=True;window.showNormal();window._sync_overlays()
            down={'value':False};hits=[]
            watcher=SpaceKeyWatcher(state_reader=lambda:down['value']);watcher.pressed.connect(lambda:hits.append(1))
            try:
                watcher.set_enabled(True);down['value']=True
                for index in range(8):watcher.poll()
                assert_that(len(hits)==1,'长按空格连切')
            finally:watcher.close()
            assert_that(needs_external_data('1、（单选题）对温度进行ARMA拟合，选择拟合效果图'),'裁剪实操题漏判')
            assert_that('statsmodels.tsa.arima.model.ARIMA' in fallback_functions('对温度进行ARMA拟合，选择拟合效果图'),'必要函数兜底为空')
            return '锁定拒旧输出、最小化/隐藏框小锁可见、空格边沿、裁剪实操函数路径通过；不代表跨应用实键已验证'
        finally:
            window.engine.pause();window.engine.running.wait=original_wait
    check('space_output_lock_indicator_and_practical_recovery', output_space_lock_and_indicator)

    def native_listener_lifecycle_and_max_material():
        from .native_space import NativeSpaceListener
        from .auto_locator import propose_location
        from .detection import TextBox
        from .function_guidance import fallback_functions
        errors=[];listener=NativeSpaceListener(lambda:None,errors.append)
        try:
            listener.start()
            assert_that(listener.ready.wait(2) and listener.installed,'Win32后台空格监听安装失败: '+';'.join(errors))
            assert_that(listener.thread_id>0 and listener.thread.is_alive(),'独立消息循环未启动')
        finally:listener.close()
        assert_that(not listener.installed and not listener.thread.is_alive(),'后台监听卸载未退出')
        canvas=Image.new('RGB',(1600,1000),'white')
        rows=[TextBox(Rect(350,140,1000,30),'每道题独立操作，不继承其他题目修改。',.99),TextBox(Rect(350,180,1000,30),'导入对象命名为demo，后续均使用它。',.99),TextBox(Rect(350,220,200,30),'demo.xlsx',.99),TextBox(Rect(350,330,1000,30),'1、（单选题）通过maxO函数分析demo.xlsx，该列输出是（）',.99)]
        rows += [TextBox(Rect(380,470+index*110,250,30),f'{letter}. 数值',.99) for index,letter in enumerate('ABCD')]
        proposal=propose_location(rows,canvas)
        assert_that(proposal.functions_only and proposal.rect.y<140,'max实操材料未纳入题干')
        assert_that('pd.Series.max' in fallback_functions('\n'.join(row.text for row in rows)),'中文maxO必要函数兜底失败')
        return 'Win32独立线程安装/消息循环/卸载通过；max材料和函数路径通过；跨应用实际按空格未验收'
    check('native_space_lifecycle_and_max_material', native_listener_lifecycle_and_max_material)

    def complete_multichoice_and_minimal_code():
        from .auto_locator import propose_location
        from .detection import TextBox
        from .practice_code import normalize_practice_code,fallback_practice_code
        from .pipeline import AnswerPipeline
        from .models import Settings
        canvas=Image.new('RGB',(1300,1000),'white')
        from PIL import ImageDraw
        ImageDraw.Draw(canvas).rectangle((0,978,1299,999),fill='#505050')
        rows=[TextBox(Rect(350,80,650,30),'1、（多选题）以下命题（）',.99)]+[TextBox(Rect(380,180+i*110,300,30),f'{c}. 选项正文',.99) for i,c in enumerate('ABCDE')]
        assert_that(propose_location(rows,canvas).complete,'远处系统横栏误判多选截断')
        code=normalize_practice_code('import pandas as pd # 导入\ndf = pd.read_excel("demo.xlsx")\nprint(df.mean(numeric_only=True)) # 计算')
        assert_that('#' not in code and 'print(df.mean(numeric_only=True))' in code,'实操代码未去注释')
        assert_that('print(df["气压"].max())' in fallback_practice_code('分析demo.xlsx，计算“气压”列最大值'),'必要列代码回退失败')
        class MockCodeClient:
            def __init__(self,*args):pass
            def answer(self,*args):return AnswerResult('模拟实操',code,False,True,answer_kind='practice_code')
        result=AnswerPipeline(Settings(),'offline-placeholder',MockCodeClient).answer(b'x',threading.Event(),'基于demo.xlsx计算均值')
        assert_that(result.answer_kind=='practice_code' and result.display_answer==code and not result.certain,'新代码协议被旧函数校验丢弃')
        window._answer(code);assert_that(window.answer_overlay.text.toPlainText()==code,'答案框代码显示不一致')
        identifier=window.history.add(png_bytes(sample),result,.1,200);window.refresh_history()
        for index in range(window.history_list.count()):
            item=window.history_list.item(index)
            if item.data(Qt.ItemDataRole.UserRole)['id']==identifier:window.history_list.setCurrentItem(item);break
        QApplication.processEvents()
        assert_that('未运行' in window.history_text.toPlainText() and code in window.history_text.toPlainText(),'代码历史标签或正文未保存')
        window.history.delete(identifier);window.refresh_history()
        return '多选底栏误报、精简Python去注释/协议/答案框/未运行历史均通过；不执行代码'
    check('complete_multichoice_and_minimal_python_code',complete_multichoice_and_minimal_code)

    def key():
        store = KeyStore(window.root / "self-test-key")
        store.path.parent.mkdir(parents=True, exist_ok=True)
        store.save("self-test-placeholder-only")
        assert_that(store.load() == "self-test-placeholder-only", "DPAPI 往返失败")
        assert_that(b"self-test-placeholder" not in store.path.read_bytes(), "密钥明文入文件")
        store.clear()
        return "Windows DPAPI 离线占位密钥加密往返通过，未读取真实密钥"
    check("dpapi_placeholder_roundtrip", key)

    def offline_api():
        import httpx
        from .api import DeepSeekClient
        from .models import Settings
        content = {"is_question": True, "certain": True, "question": "离线接口验证", "answer": "B"}
        blocks = [{"choices": [{"delta": {"reasoning_content": "不显示推理"}}]},
                  {"choices": [{"delta": {"content": json.dumps(content, ensure_ascii=False)}}]},
                  {"choices": [{"delta": {}, "finish_reason": "stop"}]}]
        text = "\n\n".join("data: " + json.dumps(block, ensure_ascii=False) for block in blocks) + "\n\ndata: [DONE]\n\n"
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text=text))
        result = DeepSeekClient(Settings(), "selftest-not-real-key", transport).answer(b"test", threading.Event())
        assert_that(result.answer == "B", "异步 SSE 最终内容未正确解析")
        return "真实应用异步 HTTP/SSE 依赖可用；MockTransport 无外部请求"
    check("packaged_async_offline_sse", offline_api)

    def search_dependencies():
        from .search import make_query, SearchSource, SearchBundle
        query = make_query("单项选择题\n预备党员的预备期为（）。\nA.半年\nB.一年")
        assert_that(query == "预备党员", "分词实体查询或字典数据缺失")
        bundle = SearchBundle([SearchSource("离线资料", "https://example.com", "仅摘要")], .1)
        assert_that("不可信" in bundle.context(), "检索资料未标不可信")
        window.search_check.setChecked(True)
        assert_that(window._collect_settings().web_search, "搜索UI开关未保存")
        window.search_check.setChecked(False)
        return "打包分词字典、搜索UI与来源字段可用；不发真实检索请求"
    check("packaged_search_dependencies_and_ui", search_dependencies)

    def screenshot_capture():
        rectangle = monitors()[0]
        capture = grab(Rect(rectangle.x, rectangle.y, min(300, rectangle.width), min(200, rectangle.height)), [])
        assert_that(capture.size == (min(300, rectangle.width), min(200, rectangle.height)), "截图尺寸错误")
        return "桌面物理截图成功；未上传"
    check("actual_desktop_capture", screenshot_capture)

    report.parent.mkdir(parents=True, exist_ok=True)
    result = {"all_passed": all(c["passed"] for c in checks), "checks": checks,
              "real_api_tested": False, "purpose": "离线桌面/打包运行验证，不代表官方 API 正确率或实际延迟"}
    report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if result["all_passed"] else 2
