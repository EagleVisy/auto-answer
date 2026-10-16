from __future__ import annotations

import asyncio
from dataclasses import replace
import threading
import time

from .api import ApiError, Cancelled, DeepSeekClient, _RequestLoop
from .models import Settings
from .search import SearchError, WebSearch


class AnswerPipeline:
    def __init__(self, settings: Settings, key: str, client_factory=DeepSeekClient, search_factory=WebSearch):
        self.settings, self.key = settings, key
        self.client_factory, self.search_factory = client_factory, search_factory
        self.metrics = {}

    def answer(self, image: bytes, cancel: threading.Event, query: str = ""):
        started = time.monotonic()
        deadline = started + self.settings.timeout_seconds
        from .practice import needs_external_data
        functions_only=needs_external_data(query)
        bundle = None
        self.sources = []
        if self.settings.web_search and not functions_only:
            try:
                with asyncio.Runner(loop_factory=_RequestLoop) as runner:
                    async def search():
                        try:
                            return await self.search_factory().search(query, cancel, deadline)
                        finally:
                            loop = asyncio.get_running_loop()
                            if isinstance(loop, _RequestLoop):
                                loop.abort_connections()
                            await asyncio.sleep(.05)
                    bundle = runner.run(search())
            except SearchError as exc:
                self.metrics["search_seconds"] = time.monotonic() - started
                self.metrics["failed_stage"] = "search"
                if cancel.is_set():
                    raise Cancelled("请求已取消") from None
                raise ApiError(str(exc)) from None
            self.metrics["search_seconds"] = bundle.elapsed
            self.sources = bundle.records()
        if cancel.is_set():
            raise Cancelled("请求已取消")
        client = self.client_factory(self.settings, self.key)
        if functions_only:client.functions_only=True
        model_started = time.monotonic()
        try:
            if bundle is None:
                # 保留旧客户端接口，方便旧回归和离线自检复用。
                result = client.answer(image, cancel)
            else:
                result = client.answer(image, cancel, web_context=bundle.context(), deadline=deadline)
        except ApiError:
            self.metrics["model_seconds"] = time.monotonic() - model_started
            self.metrics["pipeline_seconds"] = time.monotonic() - started
            self.metrics["failed_stage"] = "model"
            raise
        if functions_only:
            from .models import AnswerResult
            from .practice_code import normalize_practice_code, fallback_practice_code
            code=''
            if result.answer.strip():
                try:code=normalize_practice_code(result.answer)
                except ValueError:pass
            if not code:
                code=fallback_practice_code(query)
                self.metrics['practice_code_fallback']=bool(code)
            result=AnswerResult(result.question or '实操代码',code,False,True,answer_kind='practice_code') if code else AnswerResult(result.question,'',False,result.is_question)
            self.metrics['answer_mode']='practice_code'
        self.metrics["model_seconds"] = time.monotonic() - model_started
        self.metrics["pipeline_seconds"] = time.monotonic() - started
        self.metrics["search_enabled"] = self.settings.web_search and not functions_only
        self.metrics["sources_count"] = len(bundle.sources) if bundle else 0
        usage = getattr(client, "metrics", {}).get("usage")
        if usage:
            self.metrics["usage"] = usage
        return replace(result, sources=tuple(bundle.records()) if bundle else (), timings=dict(self.metrics))
