# 打包指南

## 前置条件

本项目的打包环境为 conda 的 `oknikke` 环境（Python 3.12），使用该环境的 python 和 pip 执行打包命令。

```bash
pip install -r requirements.txt
pip install pyinstaller
```

## 打包步骤

### 1. 内联依赖

将 ok-script 和 pyappify 运行时库内联到项目中：

```bash
python -m ok.update.inline_ok_requirements
```

### 2. 执行 PyInstaller 打包

```bash
pyinstaller --onefile --noconsole --uac-admin --noupx --noconfirm ^
  --runtime-tmpdir "C:\Temp\ok_kes" ^
  --name "ok-kes-win32-portable-v2.0.4" ^
  --icon icons/icon.ico ^
  --add-data "i18n;i18n" ^
  --add-data "ok_tasks;ok_tasks" ^
  --add-data ".venv\Lib\site-packages\opencc\clib;opencc\clib" ^
  --add-data ".venv\Lib\site-packages\opencc\clib\share\opencc;opencc\lib\share\opencc" ^
  --hidden-import ok_tasks.SortieMode ^
  --hidden-import ok_tasks.ChaosMode ^
  --hidden-import utils_sortie ^
  --hidden-import utils_chaos ^
  --hidden-import src.globals ^
  --hidden-import src.tasks.MyOneTimeTask ^
  --hidden-import src.ui.RealtimeLogTab ^
  --hidden-import src.ui.realtime_log_core ^
  --hidden-import onnxocr ^
  --hidden-import onnxocr_ppocrv5 ^
  --exclude-module PySide6.translations ^
  --collect-all onnxocr ^
  --collect-all onnxruntime ^
  --collect-all openvino ^
  --collect-all pyappify ^
  main.py
```

> 与旧版命令的差异（均为必须项）：
> - `--collect-all openvino`：否则缺 `openvino_onnx_frontend.dll` / `openvino_intel_cpu_plugin.dll`，OCR 初始化直接失败。
> - `--hidden-import src.ui.RealtimeLogTab` / `realtime_log_core`：`custom_tabs` 用字符串动态实例化，PyInstaller 静态扫不到，不写就会运行时 `ModuleNotFoundError`。
> - `--add-data assets;assets` 已删除：`assets` 实际位于 `ok_tasks/assets`，由 `--add-data ok_tasks;ok_tasks` 一并打入。

### 3. 清理临时文件

```bash
rmdir /s /q build
del "ok-kes-win32-portable-v2.0.4.spec"
```

### 4. 产物

位于 `dist\ok-kes-win32-portable-v2.0.4.exe`，约 216 MB。

### 5. 归档自检（发包前必做）

用 PyInstaller 自带的 `CArchiveReader` 读回 exe，与稳定的旧版本基线做差集，确认没有静默丢文件：

```python
from PyInstaller.archive.readers import CArchiveReader
a = CArchiveReader(r"dist\ok-kes-win32-portable-v2.0.4.exe")
toc = set(a.toc.keys())                                  # DLL / PYD / 资源清单
pyz = set(a.open_embedded_archive("PYZ.pyz").toc.keys())  # 冻结的 .py 模块清单
```

要点：
- 归档名是 `PYZ.pyz`，不是 `PYZ-00.pyz`（后者只是 build 目录里的中间文件）。
- 预期基线量级：`.dll` 92、`.pyd` 73、PYZ 模块 1635，缺失应为 0。
- `ok_tasks.*` 不进 PYZ 属正常：它以 `--add-data` 数据目录形式解压到 `_MEIPASS`，运行时从文件系统导入。
- 必查 `openvino\libs\*.dll`、`ok_tasks\assets\images\engraving_*.png`（刻印刷取硬依赖）、`opencc\**\t2s.json`。

## 关键说明

> **⚠️ 注意**：`build_exe.bat` 必须使用 **GBK/ANSI 编码**保存，CMD 才能正确解析中文文件名。如果用 UTF-8 编码，打包出来的 exe 名称会显示为乱码。

| 参数 | 作用 |
|---|---|
| `--onefile` | 单文件 exe，用户下载即用 |
| `--noconsole` | 无控制台窗口 |
| `--uac-admin` | 请求管理员权限 |
| `--add-data ok_tasks;ok_tasks` | 打包任务文件，运行时通过 `os.chdir(sys._MEIPASS)` 找到 |
| `--hidden-import src.globals` | 动态引用的全局对象模块 |
| `--collect-all onnxruntime` | 包含 ONNX Runtime 运行库和 CPU 执行提供程序 |
| `--collect-all onnxocr` | 包含 OCR 模型文件（.onnx）和代码 |
| `--runtime-tmpdir` | 指定纯英文临时解压路径，避免中文用户名导致 OpenCC C 库 fopen 失败 |
| `--add-data opencc\clib;opencc\clib` | 打包 OpenCC 绑定的动态库和字典文件 |
| `--add-data opencc\lib\share\opencc` | 额外映射字典目录到 lib 路径，解决中文路径下 C 库查找 `t2s.json` 失败 |
| `--exclude-module PySide6.translations` | 排除 PySide6 翻译文件（.qm），避免某些系统上解压 CRC 校验失败 |

## 代码层面的必要修改

打包前需要确保以下修改已存在：

### `main.py`
```python
import sys
import os

if __name__ == '__main__':
    if getattr(sys, 'frozen', False):
        exe_dir = os.path.dirname(sys.executable)
        os.chdir(sys._MEIPASS)  # 切换到临时解压目录，使 ok_tasks 等数据文件可被找到
        config["config_folder"] = os.path.join(exe_dir, "configs")  # 配置文件保存在 exe 目录下，避免被临时目录清除
```

### `ok/gui/MainWindow.py`
- 顶部添加 `import sys`

### `ok/gui/tasks/TaskManger.py`
`find_and_instantiate_class` 方法添加编码回退逻辑，解决 GBK/ANSI 编码文件导致 `utf-8' codec can't decode byte` 报错：
```python
def find_and_instantiate_class(self, file_path, base_class):
    try:
        with open(file_path, 'r', encoding='utf-8') as file:
            tree = ast.parse(file.read(), filename=file_path)
    except UnicodeDecodeError:
        with open(file_path, 'r', encoding=sys.getfilesystemencoding()) as file:
            tree = ast.parse(file.read(), filename=file_path)
```
