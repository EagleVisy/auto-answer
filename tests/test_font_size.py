from pathlib import Path
import tempfile, unittest
from PySide6.QtWidgets import QApplication
from auto_answer.models import Settings
from auto_answer.storage import SettingsStore
from auto_answer.ui import MainWindow


class FontSizeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_six_to_seventy_two_roundtrip_and_bounds(self):
        with tempfile.TemporaryDirectory() as folder:
            store=SettingsStore(Path(folder))
            for size in (6,7,9,12,22,72):
                settings=Settings(font_size=size);settings.validate();store.save(settings)
                self.assertEqual(store.load().font_size,size)
            self.assertEqual(Settings().font_size,22)
        for size in (0,5,73):
            with self.assertRaises(ValueError):Settings(font_size=size).validate()

    def test_six_point_preview_save_reopen(self):
        with tempfile.TemporaryDirectory() as folder:
            window=MainWindow(Path(folder))
            try:
                self.assertEqual(window.font_spin.minimum(),6)
                self.assertEqual(window.font_spin.maximum(),72)
                window.font_spin.setValue(6);self.app.processEvents()
                self.assertEqual(window.answer_overlay.text.font().pointSize(),6)
                self.assertTrue(window.save_settings(notify=False))
                self.assertEqual(window.settings_store.load().font_size,6)
            finally:window.close()
            reopened=MainWindow(Path(folder))
            try:
                self.assertEqual(reopened.font_spin.value(),6)
                self.assertEqual(reopened.answer_overlay.text.font().pointSize(),6)
            finally:reopened.close()


if __name__=='__main__':unittest.main()
