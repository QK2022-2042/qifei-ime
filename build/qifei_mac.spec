# -*- mode: python ; coding: utf-8 -*-
"""启飞输入法 · macOS 打包配置（必须在 macOS 主机上运行）

与 Windows 版 `qifei.spec` 的关键区别：

* **不内置任何大模型**：不带 .gguf、不带 SenseVoice ONNX 权重。用户按
  「macOS 构建与安装说明」自行把模型放进 .app 里，或用「大模型」页选文件。
  这就是 macOS 分发版「不需要大模型」的含义 —— 包体只含程序本体。
* **不带 Windows DLL**：Windows 的 core/server/engines/llama/bin/*.dll 在这里
  完全无用。直连 llama.cpp 需要 macOS 的 libllama.dylib / libggml*.dylib，
  由 build_mac.sh 放到 runtime/llama/ 后由本 spec 收集。
* 三个 Analysis 仍然各用独立工作子目录，避免 PyInstaller 反复删
  base_library.zip（这个坑在 Windows 版已经踩过并修好）。
* 产物是一个 onedir 目录 dist/QifeiIME-mac/，由 build_mac.sh 组装成
  「启飞输入法.app」并打成 .dmg。PyInstaller 不支持 Windows → macOS
  交叉编译，所以本 spec 在 Windows 上只能做语法检查，不能出包。

用法（在 macOS 上）：
    bash build/build_mac.sh
"""
from __future__ import annotations

import os

from PyInstaller.config import CONF
from PyInstaller.utils.hooks import (collect_data_files, collect_dynamic_libs,
                                     collect_submodules)

SPEC_DIR = os.path.abspath(SPECPATH)
ROOT = os.path.dirname(SPEC_DIR)
TARGET = os.environ.get('QIFEI_BUILD_TARGET', 'all').lower()
BASE_WORK = CONF['workpath']


def _read_version() -> str:
    """从 qifei/brand.py 读版本号，写进 Info.plist"""
    try:
        with open(os.path.join(ROOT, 'qifei', 'brand.py'), encoding='utf-8') as fh:
            for line in fh:
                if line.startswith('VERSION'):
                    return line.split('=', 1)[1].strip().strip('\'"')
    except OSError:
        pass
    return '0.0.0'


VERSION = _read_version()

# 由 build_mac.sh 用 iconutil 从 assets/icon.png 生成；缺失时不指定图标，
# 不影响构建，只是 Finder 里显示默认图标。
ICON = os.path.join(ROOT, 'assets', 'icon.icns')
if not os.path.exists(ICON):
    ICON = None

# 用户可编辑的配置与角色文件。刻意不含 models/ 下任何内容。
COMMON_DATAS = [
    (os.path.join(ROOT, 'assets'), 'assets'),
    (os.path.join(ROOT, 'hot.txt'), '.'),
    (os.path.join(ROOT, 'hot-rule.txt'), '.'),
    (os.path.join(ROOT, 'hot-server.txt'), '.'),
    (os.path.join(ROOT, 'LLM'), 'LLM'),
]

# macOS 的 llama.cpp 动态库。llmkit.native.dll_dir() 会去
# runtime/llama/ 找；没有这些 dylib 时大模型页仍可打开，但本地推理不可用，
# 用户会被明确提示而不是静默失败。绝不收集 Windows 的 .dll。
LLAMA_DIR = os.path.join(ROOT, 'runtime', 'llama')
LLAMA_DEST = 'runtime/llama'


def _llama_datas():
    if not os.path.isdir(LLAMA_DIR):
        return []
    out = []
    for name in sorted(os.listdir(LLAMA_DIR)):
        src = os.path.join(LLAMA_DIR, name)
        if not os.path.isfile(src):
            continue
        low = name.lower()
        if not (low.endswith('.dylib') or '.dylib.' in low):
            continue
        out.append((src, LLAMA_DEST))
    return out


LLAMA_DATAS = _llama_datas()

COMMON_EXCLUDES = [
    'IPython', 'matplotlib', 'tkinter', 'PyQt5', 'PyQt6', 'wx',
    'pandas', 'scipy', 'torch', 'funasr', 'tests', 'notebook',
    # Windows 专属后端，macOS 上打到包里也没用
    'pynput.keyboard._win32', 'pynput.mouse._win32', 'pystray._win32',
    'winreg', 'pywin32', 'comtypes',
]

GUI_HIDDEN = [
    'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'PySide6.QtNetwork',
    'sounddevice', 'numpy',
    'llmkit', 'llmkit.registry', 'llmkit.server', 'llmkit.connector',
    'llmkit.http_client', 'llmkit.gguf', 'llmkit.native',
    'llmkit.model_download',
]

CLIENT_HIDDEN = [
    'pynput.keyboard._darwin', 'pynput.mouse._darwin',
    'pystray._darwin', 'pyclip', 'keyboard', 'sounddevice',
    'srt', 'pypinyin', 'watchdog.observers', 'websockets.legacy.client',
    'llmkit', 'llmkit.registry', 'llmkit.server', 'llmkit.connector',
    'llmkit.http_client', 'llmkit.gguf', 'llmkit.native',
    'llmkit.model_download',
]

CLIENT_EXCLUDES = COMMON_EXCLUDES + [
    'PySide6', 'sherpa_onnx', 'gguf', 'soundfile', 'onnxruntime',
]

SERVER_HIDDEN = [
    'sherpa_onnx', 'soundfile', 'pypinyin',
    'websockets.legacy.server', 'sounddevice',
    # 自定义 SenseVoice ONNX 引擎由 factory 动态导入，静态分析扫不到
    'onnxruntime', 'sentencepiece',
] + collect_submodules('core.server.engines.sensevoice_onnx')

SERVER_EXCLUDES = COMMON_EXCLUDES + [
    'PySide6', 'pynput', 'pyclip', 'keyboard', 'openai', 'httpx',
]

SERVER_BINARIES = (collect_dynamic_libs('sherpa_onnx') +
                   collect_dynamic_libs('onnxruntime'))
SERVER_DATAS = (COMMON_DATAS + collect_data_files('sherpa_onnx') +
                collect_data_files('onnxruntime'))

tocs = []


def _build(name, script, hidden, excludes, datas, console, binaries=None):
    work = os.path.join(BASE_WORK, 'target-mac-' + name)
    os.makedirs(work, exist_ok=True)
    CONF['workpath'] = work
    if os.environ.get('QIFEI_CONSOLE'):
        console = True
    analysis = Analysis(
        [os.path.join(ROOT, script)],
        pathex=[ROOT],
        binaries=binaries or [],
        datas=datas,
        hiddenimports=hidden,
        hookspath=[],
        hooksconfig={},
        runtime_hooks=[],
        excludes=excludes,
        noarchive=False,
    )
    pyz = PYZ(analysis.pure, analysis.zipped_data)
    exe = EXE(
        pyz,
        analysis.scripts,
        [],
        exclude_binaries=True,
        name=name,
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=console,
        disable_windowed_traceback=False,
        icon=ICON,
    )
    tocs.append(exe)
    tocs.append([e for e in analysis.binaries if not _skip_entry(e)])
    tocs.append([e for e in analysis.datas if not _skip_entry(e)])
    return analysis, exe


def _skip_entry(entry) -> bool:
    """剔除第三方包自带的、macOS 上没用的文件。

    onnxruntime 带几个测试用 .onnx，sounddevice 的 wheel 里是各平台
    PortAudio 二进制（Windows 的 .dll）。它们不影响运行，但会被
    build_mac.sh 的「包内不得含模型/DLL」断言拦下。必须在收集阶段就
    剔除：BUNDLE 签名之后再删文件会破坏 CodeResources，让 TCC 授权失效。
    """
    dest = str(entry[0]).replace('\\', '/').lower()
    if dest.endswith('.onnx') and 'onnxruntime/datasets' in dest:
        return True
    if dest.endswith('.dll') and '_sounddevice_data' in dest:
        return True
    return False


if TARGET in ('all', 'gui'):
    _build('QifeiIME', 'qifei_ime.py', GUI_HIDDEN, COMMON_EXCLUDES,
           COMMON_DATAS + LLAMA_DATAS, False)

if TARGET in ('all', 'client'):
    _build('QifeiClient', 'qifei_client.py', CLIENT_HIDDEN, CLIENT_EXCLUDES,
           COMMON_DATAS + LLAMA_DATAS, True)

if TARGET in ('all', 'server'):
    _build('QifeiServer', 'qifei_server.py', SERVER_HIDDEN, SERVER_EXCLUDES,
           SERVER_DATAS, True, SERVER_BINARIES)

# 直接由 PyInstaller 生成 .app，而不是自己拼目录。
#
# 原因：Apple 的代码签名规范要求 Contents/MacOS 只放代码，数据文件必须
# 放 Contents/Resources，再由 PyInstaller 交叉链接到 Contents/Frameworks。
# 手工把 _internal 整个塞进 Frameworks 会让 codesign 拒绝签名，而未签名的
# .app 在 TCC 眼里没有稳定身份，辅助功能/输入监控授权永远对不上 —— 表现
# 就是系统设置里开关开着，程序里 AXIsProcessTrusted() 仍然返回 False。
# BUNDLE 会完成目录分类、交叉链接，并用 codesign --deep 给整包做 ad-hoc
# 签名（Info.plist 里的 CFBundleIdentifier 就是签名标识）。
app = BUNDLE(
    *tocs,
    name='启飞输入法.app',
    icon=ICON,
    version=VERSION,
    bundle_identifier='cn.qifei.ime',
    info_plist={
        # 三个 EXE 里 server/client 是 console=True，BUNDLE 默认会写
        # LSBackgroundOnly=True，那会让 GUI 不显示 Dock 图标/窗口，这里覆盖。
        'LSBackgroundOnly': False,
        'NSHighResolutionCapable': True,
        'NSMicrophoneUsageDescription':
            '启飞输入法需要访问麦克风来进行离线语音识别，音频不会上传。',
        'NSAppleEventsUsageDescription':
            '启飞输入法需要把识别结果写入当前应用，请允许辅助功能权限。',
    },
)
