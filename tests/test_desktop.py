from pathlib import Path
import tempfile
import unittest

from PIL import Image
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from auto_answer.models import Rect
from auto_answer.overlays import CaptureOverlay, RegionSelector
from auto_answer.capture import monitors


class DesktopInteractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_capture_geometry_and_title_outside_area(self):
        overlay = CaptureOverlay()
        try:
            rect = Rect(300, 300, 600, 360)
            overlay.set_physical_rect(rect)
            overlay.show()
            self.app.processEvents()
            actual = overlay.physical_rect()
            self.assertLessEqual(abs(actual.x - rect.x), 2)
            self.assertLessEqual(abs(actual.y - rect.y), 2)
            strips = overlay.exclusion_rectangles()
            self.assertLessEqual(strips[0].y + strips[0].height, rect.y + 3)
            self.assertFalse(strips[0].contains(Rect(rect.x + 100, rect.y + 50, 50, 50)))
            committed = []
            overlay.geometry_committed.connect(committed.append)
            start = QPoint(35, 15)
            QTest.mousePress(overlay, Qt.MouseButton.LeftButton, pos=start)
            QTest.mouseMove(overlay, QPoint(75, 45))
            QTest.mouseRelease(overlay, Qt.MouseButton.LeftButton, pos=QPoint(75, 45))
            self.assertTrue(committed)
            self.assertGreater(committed[0].x, rect.x)
        finally:
            overlay.close()

    def test_selector_drag_and_escape(self):
        selector = RegionSelector()
        selected = []
        selector.selected.connect(selected.append)
        selector.open()
        self.app.processEvents()
        screen = selector.windows[0]
        QTest.mousePress(screen, Qt.MouseButton.LeftButton, pos=QPoint(150, 180))
        QTest.mouseMove(screen, QPoint(450, 390))
        QTest.mouseRelease(screen, Qt.MouseButton.LeftButton, pos=QPoint(450, 390))
        self.app.processEvents()
        self.assertEqual(len(selected), 1)
        self.assertGreater(selected[0].width, 300)
        selector = RegionSelector()
        cancelled = []
        selector.cancelled.connect(lambda: cancelled.append(True))
        selector.open()
        self.app.processEvents()
        QTest.keyClick(selector.windows[0], Qt.Key.Key_Escape)
        self.app.processEvents()
        self.assertEqual(cancelled, [True])


if __name__ == "__main__":
    unittest.main()
