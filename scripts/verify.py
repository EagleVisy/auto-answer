"""运行回归并保存可核查的文本与 JSON 证据。"""
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from auto_answer.capture import enable_dpi_awareness
    enable_dpi_awareness()
    build = ROOT / "build"
    build.mkdir(exist_ok=True)
    started = time.monotonic()
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    with (build / "test-results.txt").open("w", encoding="utf-8") as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    report = {"passed": result.wasSuccessful(), "tests": result.testsRun, "failures": len(result.failures),
              "errors": len(result.errors), "skipped": len(result.skipped),
              "seconds": round(time.monotonic() - started, 3), "real_api_tested": False}
    (build / "test-summary.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
