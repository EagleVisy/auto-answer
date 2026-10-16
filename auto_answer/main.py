from __future__ import annotations

import argparse
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import sys

from .capture import enable_dpi_awareness


def main() -> int:
    parser = argparse.ArgumentParser(description="Auto Answer 桌面练习答题助手")
    parser.add_argument("--self-test", type=Path, metavar="REPORT", help="运行离线桌面与 OCR 自检并写入 JSON 报告")
    parser.add_argument("--screenshot", type=Path, metavar="PNG", help="自检时保存设置窗口截图")
    arguments = parser.parse_args()
    enable_dpi_awareness()
    from PySide6.QtCore import QLockFile, QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox
    from .storage import data_directory
    root = data_directory()
    handler = RotatingFileHandler(root / "diagnostic.log", maxBytes=200000, backupCount=2, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger().addHandler(handler)
    def exception_handler(exception_type, value, traceback):
        # 不输出 exception 内容或局部变量，避免 API 内容/密钥被记入日志。
        logging.error("unhandled_exception: %s", exception_type.__name__)
        if QApplication.instance():
            QMessageBox.warning(None, "Auto Answer", "发生未预期错误。请暂停任务并重新启动；诊断日志已记录故障类型。")
    sys.excepthook = exception_handler
    app = QApplication(sys.argv[:1])
    app.setApplicationName("Auto Answer")
    app.setOrganizationName("AutoAnswer")
    lock = QLockFile(str(root / "instance.lock"))
    if not lock.tryLock(100):
        QMessageBox.information(None, "Auto Answer", "这个数据目录已有一个程序实例。请从任务栏或托盘打开设置。")
        return 1
    from .ui import MainWindow
    window = MainWindow(root)
    window.show()
    if arguments.self_test:
        from .selftest import run_self_test
        def test():
            code = run_self_test(window, arguments.self_test, arguments.screenshot)
            window.close()
            # closeEvent 的 quit 会将事件循环返回值重置为 0；在下一个事件轮次退出。
            app.exit(code)
        QTimer.singleShot(400, test)
    try:
        return app.exec()
    finally:
        lock.unlock()


if __name__ == "__main__":
    raise SystemExit(main())
