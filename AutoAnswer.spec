# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules
import rapidocr_onnxruntime

root = Path(SPECPATH)
ocr_root = Path(rapidocr_onnxruntime.__file__).parent
ocr_data, ocr_binaries, ocr_hidden = collect_all('rapidocr_onnxruntime')
ort_data, ort_binaries, ort_hidden = collect_all('onnxruntime')
jieba_data = collect_data_files('jieba')
# RapidOCR 1.2.3 使用无包前缀动态导入，必须同时收集三个顶级模块。
ocr_modules = []
for module in ('ch_ppocr_v3_det', 'ch_ppocr_v3_rec', 'ch_ppocr_v2_cls'):
    ocr_modules += collect_submodules(module)

analysis = Analysis(
    [str(root / 'run.py')],
    pathex=[str(root), str(ocr_root)],
    binaries=ocr_binaries + ort_binaries,
    datas=ocr_data + ort_data + jieba_data,
    hiddenimports=ocr_hidden + ort_hidden + ocr_modules,
    hookspath=[], runtime_hooks=[],
    excludes=['PySide6.QtWebEngineCore', 'PySide6.QtWebEngineWidgets', 'PySide6.QtPdf',
              'PySide6.QtQml', 'PySide6.QtQuick', 'matplotlib', 'tkinter', 'pytest'],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='AutoAnswer', debug=False,
          bootloader_ignore_signals=False, strip=False, upx=False, console=False,
          disable_windowed_traceback=False, icon=str(root / 'assets' / 'app.ico'))
collection = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='AutoAnswer')
