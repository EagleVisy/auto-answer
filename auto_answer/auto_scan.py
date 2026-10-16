"""自动全屏快速候选反馈→补全→范围稳定/超时锁定→答题。"""
from __future__ import annotations

import threading
import time

from .auto_locator import LocationLock, LocationProposal, propose_location, close_rect, probe_rect, steady_proposal, detection_frame
from .capture import grab, monitors, png_bytes
from .detection import StableGate, TextBox, normalized_text, same_frame, feature
from .dynamic_regions import CountdownTracker, mask_status_regions, timer_status_line
from .models import Rect
from .question_layout import is_question_start


def _translate(boxes, rect):
    return [TextBox(Rect(box.rect.x+rect.x,box.rect.y+rect.y,box.rect.width,box.rect.height),box.text,box.confidence) for box in boxes]


def run_auto_session(engine, reader, settings, key, serial, grab_fn=grab, monitors_fn=monitors):
    locator=LocationLock(settings.auto_stable_ms,settings.auto_lock_timeout_ms)
    proposal=None
    last_emitted=None
    submitted=False
    submitted_identity=''
    gate=StableGate(settings.stable_ms/1000)
    countdown=CountdownTracker()
    masks=[]
    failures=0
    last_ocr=0.0
    last_candidate_identity=''
    boundary_boxes=[]
    timeout_locked=False
    last_difference=None
    request_revision=engine.request_revision

    def alive():
        return not engine.shutdown.is_set() and engine.running.is_set() and engine.serial==serial

    def emit_status(status,answer=''):
        version,_=engine.epoch.token()
        engine._status_event.emit(version,status,answer)

    while alive():
        if request_revision!=engine.request_revision:
            request_revision=engine.request_revision
            submitted=False
            gate=StableGate(settings.stable_ms/1000)
            locator=LocationLock(settings.auto_stable_ms,settings.auto_lock_timeout_ms)
            if proposal:locator.observe(proposal)
            timeout_locked=False;last_difference=None
        started=time.monotonic()
        frame_revision=engine.request_revision
        with engine.lock:
            if not alive():break
            exclusions=list(engine.exclusions)
        screens=monitors_fn()
        if settings.monitor_index>=len(screens):
            emit_status('显示器已断开，请重新选择','识题暂不可用');return
        screen=screens[settings.monitor_index]
        with engine.lock:
            if not alive():break
            full=grab_fn(screen,exclusions)
        if not alive():break

        if proposal is None:
            # 首次仅一次粗读，候选框立刻显示，不等待精读+六块补扫完成。
            boxes=reader.read(full)
            if not alive():break
            local=propose_location(boxes,full)
            if local is None:
                failures+=1
                emit_status('快速搜索中 · 尚未发现可信完整题目','等待题目')
                # 小字候选定期有限精读，不让每轮扫描都执行昂贵回填。
                if failures>=2 and hasattr(reader,'read_screen'):
                    boxes=reader.read_screen(full)
                    if not alive():break
                    local=propose_location(boxes,full)
                    failures=0
                if local is None:
                    engine.shutdown.wait(settings.auto_refresh_ms/1000);continue
            proposal=local
            boundary_boxes=boxes
            timeout_locked=False
            last_difference=None
            last_candidate_identity=''
            masks=[]
            locator.observe(local)
            gate=StableGate(settings.stable_ms/1000)
            if not countdown.samples:
                countdown=CountdownTracker()
            submitted=False
            failures=0
            global_rect=Rect(screen.x+proposal.rect.x,screen.y+proposal.rect.y,proposal.rect.width,proposal.rect.height)
            engine.region_changed.emit(global_rect)
            last_emitted=proposal.rect
            emit_status('已找到候选范围 · 正在快速补全并确认边界','定位题目中')
            # 下一次只读候选范围，而非重复全屏。
            engine.shutdown.wait(settings.auto_refresh_ms/1000);continue

        rect=proposal.rect
        crop=full.crop((rect.x,rect.y,rect.x+rect.width,rect.y+rect.height))
        previous_feature=gate.last
        changed,_=gate.observe(detection_frame(crop,masks))
        if not submitted and previous_feature is not None and previous_feature.shape==gate.last.shape:
            import numpy as np
            diff=np.abs(previous_feature.astype(np.int16)-gate.last.astype(np.int16))>=26
            last_difference=diff if last_difference is None or last_difference.shape!=diff.shape else last_difference | diff
        now=time.monotonic()
        need_ocr=not submitted or changed
        if submitted and not changed:
            # 静止同题不需要周期OCR推翻已提交的完整结构，避免偶发空读重答。
            engine.shutdown.wait(settings.auto_refresh_ms/1000)
            continue
        if submitted and changed and timeout_locked:
            # 超时锁定已知持续动画：只允许先前重复变化的像素继续动，新增区域仍立即换题。
            import numpy as np
            current=feature(detection_frame(crop,masks))
            diff=np.abs(current.astype(np.int16)-gate.submitted_frame.astype(np.int16))>=26 if gate.submitted_frame is not None and current.shape==gate.submitted_frame.shape else None
            if diff is not None and last_difference is not None and diff.shape==last_difference.shape and not np.any(diff & ~last_difference):
                gate.last=current
                engine.shutdown.wait(settings.auto_refresh_ms/1000);continue
        if submitted and changed:
            # 真正换题从整屏重找，避免新题比旧题高/宽而被旧框截断；先拒绝旧响应。
            engine.epoch.invalidate();submitted=False;proposal=None
            locator=LocationLock(settings.auto_stable_ms,settings.auto_lock_timeout_ms)
            gate=StableGate(settings.stable_ms/1000)
            emit_status('检测到题目画面变化 · 重新快速全屏定位','定位题目中')
            continue
        if need_ocr:
            if submitted and changed:
                # OCR可能耗时，先拒绝旧结果，再判断是不是题目变化。
                engine.epoch.invalidate()
            probe=probe_rect(proposal,full)
            probe_image=full.crop((probe.x,probe.y,probe.x+probe.width,probe.y+probe.height))
            scan_revision=engine.request_revision
            local_boxes=reader.read(probe_image)
            last_ocr=time.monotonic()
            if not alive():break
            if engine.request_revision!=scan_revision:
                continue  # 锁→解锁期间的旧OCR不能作为当前题发请求。
            boxes=_translate(local_boxes,probe)
            # 附带粗读中范围外的操作栏/题材边界，精读不再反复失去下边界。
            outside=[box for box in boundary_boxes if not probe.intersection(box.rect) and not is_question_start(box.text)]
            from .question_layout import option_label
            # 同一画面可保留首次粗读的可靠选项锚点，细读漏单字母不等于题目缺项。
            preserved=[]
            if (not changed or previous_feature is None) and proposal.complete:
                preserved=[box for box in boundary_boxes if option_label(box.text) and proposal.rect.contains(box.rect)]
            refreshed=propose_location(boxes+outside+preserved,full)
            if refreshed is None:
                failures+=1
                if failures>=2:
                    engine.epoch.invalidate()
                    proposal=None;locator=LocationLock(settings.auto_stable_ms,settings.auto_lock_timeout_ms)
                    emit_status('原范围已无题目 · 重新快速搜索','等待题目')
                engine.shutdown.wait(settings.auto_refresh_ms/1000);continue
            failures=0
            # 粗/细OCR标题拆框不是换题；同个候选保留首次预算和身份，真实内容另行比较。
            overlap=refreshed.rect.intersection(proposal.rect)
            same_candidate=overlap is not None and overlap.width*overlap.height/min(refreshed.rect.width*refreshed.rect.height,proposal.rect.width*proposal.rect.height)>.65
            if same_candidate:
                refreshed=LocationProposal(refreshed.rect,proposal.identity,refreshed.complete,refreshed.reason,refreshed.option_count,refreshed.needs_tail_probe,refreshed.functions_only)
            else:
                engine.epoch.invalidate();submitted=False
                locator=LocationLock(settings.auto_stable_ms,settings.auto_lock_timeout_ms)
            refreshed=steady_proposal(proposal,refreshed)
            if submitted or close_rect(refreshed.rect,proposal.rect):
                refreshed=LocationProposal(proposal.rect,refreshed.identity,refreshed.complete,refreshed.reason,refreshed.option_count,refreshed.needs_tail_probe,refreshed.functions_only)
            if not close_rect(refreshed.rect,proposal.rect):
                gate=StableGate(settings.stable_ms/1000)
                masks=[]
                last_difference=None
            proposal=refreshed
            if last_emitted is None or not close_rect(proposal.rect,last_emitted):
                engine.region_changed.emit(Rect(screen.x+proposal.rect.x,screen.y+proposal.rect.y,proposal.rect.width,proposal.rect.height))
                last_emitted=proposal.rect
            # 重新采集：本轮已移动显示框，其排除条需在下一帧一致，不能拿旧框像素做提交基准。
            rect=proposal.rect
            with engine.lock:
                if not alive():break
                exclusions_now=list(engine.exclusions)
            if exclusions_now!=exclusions:
                engine.shutdown.wait(settings.auto_refresh_ms/1000)
                continue
            crop=full.crop((rect.x,rect.y,rect.x+rect.width,rect.y+rect.height))
            local_boxes=[TextBox(Rect(box.rect.x-rect.x,box.rect.y-rect.y,box.rect.width,box.rect.height),box.text,box.confidence) for box in boxes if rect.contains(box.rect)]
            detected_masks=countdown.update(local_boxes,crop.width,crop.height)
            if len(detected_masks)==len(masks) and all(close_rect(a,b,8) for a,b in zip(detected_masks,masks)):
                detected_masks=masks
            masks=detected_masks
            cleaned=detection_frame(crop,masks)
            identity=normalized_text([box for box in local_boxes if not timer_status_line(box) and not any(mask.contains(box.rect) for mask in masks)])
            # 同一原像素图的OCR偶发字间距/拆框不能造成重复请求，像素仍是权威。
            pixel_identical=same_frame(gate.submitted_frame,feature(cleaned))
            if submitted:
                if pixel_identical:
                    engine.shutdown.wait(settings.auto_refresh_ms/1000);continue
                if identity!=submitted_identity:
                    engine.epoch.invalidate();submitted=False
                    locator=LocationLock(settings.auto_stable_ms,settings.auto_lock_timeout_ms)
                    gate=StableGate(settings.stable_ms/1000)
                    emit_status('检测到题目文字变化 · 正在重新定位','定位题目中')
                elif not changed:
                    engine.shutdown.wait(settings.auto_refresh_ms/1000);continue
                elif same_frame(gate.submitted_frame, feature(cleaned)):
                    # 确认排除计时后像素同题，不重启。
                    engine.shutdown.wait(settings.auto_refresh_ms/1000);continue
                else:
                    # OCR相同仍可能是公式/图像变动，不能吞掉单像素数字换题。
                    engine.epoch.invalidate();submitted=False
                    locator=LocationLock(settings.auto_stable_ms,settings.auto_lock_timeout_ms)
                    gate=StableGate(settings.stable_ms/1000)
                    emit_status('检测到题目画面变化 · 重新确认范围','定位题目中')
            # 尚未锁定时，像素内容变化也参与稳定；超时预算仍从首次候选计算。
            if changed or (last_candidate_identity and last_candidate_identity!=identity):
                locator.stable_since=time.monotonic()
            last_candidate_identity=identity
            ready=locator.observe(proposal)
            # 裸数字有待确认的跨帧计时证据时，给它至少两次递减机会，避免650ms先提交。
            timer_pending=bool(countdown.samples) and not countdown.confirmed
            if timer_pending and time.monotonic()-(locator.first or time.monotonic())<min(settings.auto_lock_timeout_ms/1000,3.0):
                ready=False
            if not ready:
                elapsed=time.monotonic()-(locator.first or time.monotonic())
                status=f'正在快速定位 · {elapsed:.1f}s / {settings.auto_lock_timeout_ms/1000:.1f}s · {proposal.reason}'
                if elapsed>=settings.auto_lock_timeout_ms/1000 and not proposal.complete:
                    status='定位超时但题目/选项未确认完整 · 请滚动显示全文或手动框选'
                answer_text='选项未完整显示或识别，请滚动显示全文或手动框选' if elapsed>=settings.auto_lock_timeout_ms/1000 and not proposal.complete else '定位题目中'
                emit_status(status,answer_text)
            else:
                with engine.lock:
                    if not alive():break
                    if engine.request_revision!=frame_revision:
                        continue
                    if engine.output_locked:
                        engine.shutdown.wait(settings.auto_refresh_ms/1000)
                        continue
                    if len(engine.request_threads)>=2:
                        engine.shutdown.wait(.1);continue
                    engine.epoch.invalidate();version,cancel=engine.epoch.token()
                    submitted=True;submitted_identity=identity
                    timeout_locked='超时' in locator.reason
                    if timeout_locked and last_difference is not None and last_difference.shape==feature(cleaned).shape:
                        # 已知动画只能排除小面积非文字区域；题干/选项数字始终敏感。
                        import numpy as np
                        permitted=last_difference.copy()
                        for box in local_boxes:
                            x=max(0,box.rect.x-5);y=max(0,box.rect.y-5)
                            right=min(crop.width,box.rect.x+box.rect.width+5);bottom=min(crop.height,box.rect.y+box.rect.height+5)
                            permitted[y:bottom,x:right]=False
                        last_difference=permitted if np.count_nonzero(permitted)<permitted.size*.05 else None
                    gate.observe(cleaned);gate.mark_submitted(identity)
                    engine._status_event.emit(version,locator.reason+' · 正在请求AI','正在思考')
                    worker=threading.Thread(target=engine._request,args=(settings,key,png_bytes(cleaned),version,cancel,'\n'.join(box.text for box in local_boxes if not timer_status_line(box))),daemon=True)
                    engine.request_threads.add(worker);worker.start()
        interval=settings.auto_refresh_ms/1000
        engine.shutdown.wait(max(.01,interval-(time.monotonic()-started)))
