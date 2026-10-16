import socket
import threading
import time
import unittest
from unittest.mock import patch

import httpx

from auto_answer.api import ApiError, Cancelled, DeepSeekClient
from auto_answer.models import Settings


class NetworkCancellationTests(unittest.TestCase):
    def run_stalled_tls(self, cancel_after=None):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        release = threading.Event()
        accepted = []
        def server():
            connection, _ = listener.accept()
            accepted.append(True)
            with connection:
                release.wait(8)
        worker = threading.Thread(target=server, daemon=True)
        worker.start()
        cancel = threading.Event()
        if cancel_after:
            timer = threading.Timer(cancel_after, cancel.set)
            timer.start()
        started = time.monotonic()
        try:
            with patch("auto_answer.api.ENDPOINT", f"https://127.0.0.1:{port}/chat/completions"):
                transport = httpx.AsyncHTTPTransport(verify=False)
                with self.assertRaises(Cancelled if cancel_after else ApiError):
                    DeepSeekClient(Settings(timeout_seconds=5), "test-placeholder", transport).answer(b"test", cancel)
            self.assertTrue(accepted)
            return time.monotonic() - started
        finally:
            release.set()
            listener.close()
            worker.join(1)
            if cancel_after:
                timer.cancel()

    def test_tls_handshake_cancel_under_two_seconds(self):
        elapsed = self.run_stalled_tls(cancel_after=.4)
        self.assertLess(elapsed, 2, f"TLS 握手取消用了 {elapsed:.2f} 秒")

    def test_established_sse_without_tokens_is_cancelled(self):
        listener = socket.socket()
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        release = threading.Event()
        def server():
            connection, _ = listener.accept()
            with connection:
                connection.recv(65536)
                connection.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n")
                release.wait(6)
        worker = threading.Thread(target=server, daemon=True)
        worker.start()
        cancel = threading.Event()
        timer = threading.Timer(.5, cancel.set)
        timer.start()
        started = time.monotonic()
        try:
            with patch("auto_answer.api.ENDPOINT", f"http://127.0.0.1:{port}/chat/completions"):
                with self.assertRaises(Cancelled):
                    DeepSeekClient(Settings(timeout_seconds=5), "placeholder", httpx.AsyncHTTPTransport()).answer(b"test", cancel)
            self.assertLess(time.monotonic() - started, 2)
        finally:
            release.set()
            timer.cancel()
            listener.close()
            worker.join(1)

    def test_total_deadline_includes_tls(self):
        elapsed = self.run_stalled_tls()
        self.assertLess(elapsed, 6.5, f"5 秒预算实际用了 {elapsed:.2f} 秒")
        self.assertGreater(elapsed, 4.5)


if __name__ == "__main__":
    unittest.main()
