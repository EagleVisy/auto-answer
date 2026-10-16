# 构建

## 环境

Windows 64 位、Python 3.13。使用固定依赖创建独立环境：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
```

`requirements-lock.txt` 记录发布时的完整依赖版本，供审计和复现参考；不同 Windows/Python 版本是否兼容需另行验证。

## 验证与构建

```powershell
.\.venv\Scripts\python.exe scripts/verify.py
.\.venv\Scripts\python.exe scripts/build.py
```

程序输出到 `dist-v1.1/AutoAnswer/`。PyInstaller 构建包含 OCR 依赖及动态库，不读取用户设置目录，也不嵌入 API Key。

如需运行桌面自检，请指定独立数据目录，避免使用日常答题历史：

```powershell
$env:AUTO_ANSWER_DATA_DIR = "$PWD\build\selftest-data"
.\dist-v1.1\AutoAnswer\AutoAnswer.exe --self-test build\packaged-selftest.json
```

桌面自检会打开窗口并采集临时屏幕区域，不向真实 AI API 发送请求。运行时请关闭含私人内容的窗口。报告留在本机，不进入 Git。

## 发布包

```powershell
.\.venv\Scripts\python.exe scripts/package_release.py
```

打包脚本仅从运行程序目录取 exe 与 `_internal`，再加入公开文档、项目许可证和第三方许可证；不会复制本机评测报告、截图、OCR、用户设置或日常历史。ZIP 与 SHA256 清单位于 `release/`，不进入源码提交。

Windows 未签名程序可能触发信誉提示。构建或测试通过不代表真实 API 正确率、所有网页布局、多屏和跨应用空格行为已经验收。
