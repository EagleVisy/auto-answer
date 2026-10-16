"""独立解析代理，避免本机 NO_PROXY IPv6 值使 httpx 客户端构造崩溃。"""
import asyncio
import os
import socket
import threading
from urllib.parse import urlsplit


async def bounded_getaddrinfo(host, port, **kwargs):
    """独立daemon解析，取消无需等待asyncio默认线程池退出。"""
    loop = asyncio.get_running_loop()
    future = loop.create_future()
    def resolve():
        try:
            value, error = socket.getaddrinfo(host, port, **kwargs), None
        except OSError as exc:
            value, error = None, exc
        def deliver():
            if not future.done():
                if error:
                    future.set_exception(error)
                else:
                    future.set_result(value)
        try:
            loop.call_soon_threadsafe(deliver)
        except RuntimeError:
            pass
    threading.Thread(target=resolve, daemon=True).start()
    return await asyncio.wait_for(future, timeout=3)


def client_options() -> dict:
    result = {"trust_env": False}
    for name in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy"):
        value = os.environ.get(name, "").strip()
        if not value:
            continue
        try:
            parsed = urlsplit(value)
            if parsed.scheme not in {"http", "https"} or not parsed.hostname:
                continue
            _ = parsed.port
        except ValueError:
            continue
        result["proxy"] = value
        break
    return result
