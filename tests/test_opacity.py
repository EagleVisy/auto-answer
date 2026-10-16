from pathlib import Path
import tempfile
import unittest

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter
from PySide6.QtWidgets import QApplication

from auto_answer.models import Settings
from auto_answer.overlays import AnswerOverlay
from auto_answer.storage import SettingsStore
from auto_answer.ui import MainWindow


class OpacityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_zero_and_full_opacity_validate_and_persist(self):
        with tempfile.TemporaryDirectory() as root:
            store = SettingsStore(Path(root))
            for opacity in (0, 1, 10, 88, 100):
                settings = Settings(opacity=opacity)
                settings.validate()
                store.save(settings)
                self.assertEqual(store.load().opacity, opacity)
        for invalid in (-1, 101):
            with self.assertRaises(ValueError):
                Settings(opacity=invalid).validate()

    def test_ui_zero_preview_save_and_reload(self):
        with tempfile.TemporaryDirectory() as root:
            window = MainWindow(Path(root))
            try:
                self.assertEqual(window.opacity_spin.minimum(), 0)
                window.opacity_spin.setValue(0)
                self.app.processEvents()
                self.assertEqual(window.answer_overlay.opacity, 0)
                self.assertTrue(window.save_settings(notify=False))
                self.assertEqual(window.settings_store.load().opacity, 0)
            finally:
                window.close()
            reopened = MainWindow(Path(root))
            try:
                self.assertEqual(reopened.opacity_spin.value(), 0)
                self.assertEqual(reopened.answer_overlay.opacity, 0)
            finally:
                reopened.close()

    def test_zero_background_keeps_text_rendered(self):
        overlay = AnswerOverlay()
        try:
            overlay.resize(420, 200)
            overlay.apply_settings(Settings(opacity=0, font_size=26))
            overlay.set_editing(False)
            overlay.set_answer('答案：B')
            overlay.show()
            self.app.processEvents()
            image = QImage(overlay.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(Qt.GlobalColor.transparent)
            overlay.render(image)
            self.assertEqual(image.pixelColor(4, 70).alpha(), 0)
            self.assertEqual(image.pixelColor(overlay.width() // 2, overlay.height() - 30).alpha(), 0)
            # 文字独立绘制，背景0%不能通过整窗opacity将文字一并隐藏。
            self.assertTrue(any(image.pixelColor(x, y).alpha() > 100
                                for y in range(10, 90) for x in range(10, 240)))
            self.assertEqual(overlay.text.toPlainText(), '答案：B')
        finally:
            overlay.close()


if __name__ == '__main__':
    unittest.main()
