# 第三方组件说明

本项目未修改依赖包源码。发布目录内的动态库和 OCR 模型用于运行软件，第三方名称和许可证归各自权利人所有。

主要组件：

- PySide6 / Shiboken / Qt：LGPL-3.0 等适用的组件许可证。采用动态库方式，未使用静态链接。用户可依相应许可证替换 `_internal/PySide6` 中兼容的运行库；修改后仍需符合其 ABI。源代码及构建信息：https://code.qt.io/cgit/pyside/pyside-setup.git/ 与 https://www.qt.io/licensing/ 。
- RapidOCR：Apache-2.0；https://github.com/RapidAI/RapidOCR 。使用其随包中文/英文 OCR 模型。
- PaddleOCR 模型：Apache-2.0；https://github.com/PaddlePaddle/PaddleOCR 。模型来自 RapidOCR 依赖包中的 PP-OCRv3 检测/识别与角度分类文件。
- ONNX Runtime：MIT；https://github.com/microsoft/onnxruntime 。
- Pillow：HPND；https://github.com/python-pillow/Pillow 。
- httpx / httpcore：BSD-3-Clause；https://github.com/encode/httpx 与 https://github.com/encode/httpcore 。
- NumPy：BSD-3-Clause；https://numpy.org/ 。
- OpenCV、Shapely、PyYAML、PyClipper、AnyIO 及其依赖：以各安装包附带的许可证为准。
- PyInstaller：GPL-2.0-or-later 带 bootloader 例外；https://pyinstaller.org/ 。

本地安装包附带的完整许可证和包元信息由 `scripts/package_release.py` 收集至发布目录 `THIRD_PARTY_LICENSES/`。应保留该目录和本文件。不把本文的概述视为对全部组件许可证的替代。

DeepSeek API 是外部服务，使用条件、费用和数据处理以官方条款为准；https://platform.deepseek.com/ 。
