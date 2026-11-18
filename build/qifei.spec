# -*- mode: python ; coding: utf-8 -*-
"""
启飞输入法 · PyInstaller 打包配置

一次产出三个可执行文件，放进同一个目录：
    QifeiIME.exe     图形界面（设置中心 / 首次引导 / 悬浮条 / 托盘）
    QifeiClient.exe  听写客户端（快捷键 + 麦克风 + 上屏，控制台隐藏运行）
    QifeiServer.exe  识别服务端（加载模型，控制台隐藏运行）

用法：
    pyinstaller build/qifei.spec                 # 全部
    set QIFEI_BUILD_TARGET=gui & pyinstaller build/qifei.spec   # 只打界面

模型不打进安装包：语音模型由界面下载到 models 目录；LLM 润色模型
（qwen2.5-1.5b-instruct-q4_k_m.gguf，约 1GB）也不进 datas，
由「大模型」页的「下载推荐润色模型」按钮或离线拷贝补到 models/llm/。
这样主包只有一百多 MB，模型可以随时换引擎、换路径、单独分发。
直连 llama.cpp 的动态库是例外，必须打进包，否则又要求用户装东西。
"""

import os
import sys

from PyInstaller.config import CONF
from PyInstaller.utils.hooks import (collect_data_files, collect_dynamic_libs,
                                     collect_submodules)

# PyInstaller 里 SPECPATH 就是 spec 文件所在目录（build/），项目根目录是它的上一级
SPEC_DIR = os.path.abspath(SPECPATH)
ROOT = os.path.dirname(SPEC_DIR)
TARGET = os.environ.get('QIFEI_BUILD_TARGET', 'all').lower()

# PyInstaller 把 spec 里所有 Analysis 的产物都放在 CONF['workpath']（默认
# work/qifei/）。build_main.py 构造 base_library.zip 时写死了「先删再建」
# （"Always remove the file before the build"），于是 all 目标的第二、三个
# Analysis 会 os.remove() 前面留下的 base_library.zip。在禁止删除的受管控
# 环境里，完整构建必然在这里失败，而单目标构建正常。给每个目标分一个独立
# 工作子目录，从根上避免覆盖删除；TOC 里的路径是绝对路径，COLLECT 不受影响。
BASE_WORK = CONF['workpath']

ICON = os.path.join(ROOT, 'assets', 'icon.ico')
if not os.path.exists(ICON):
    ICON = None

# 随包分发的数据：用户可编辑的配置、热词、LLM 角色
COMMON_DATAS = [
    (os.path.join(ROOT, 'assets'), 'assets'),
    (os.path.join(ROOT, 'hot.txt'), '.'),
    (os.path.join(ROOT, 'hot-rule.txt'), '.'),
    (os.path.join(ROOT, 'hot-server.txt'), '.'),
    (os.path.join(ROOT, 'LLM'), 'LLM'),
]

# 直连 llama.cpp 用的动态库。打包后 llmkit.native.dll_dir() 会去
# sys._MEIPASS/core/server/engines/llama/bin 找它们；不放进包的话
# GUI / 客户端就只能回落到外部 llama-server.exe，等于又要求用户装东西。
LLAMA_BIN = os.path.join(ROOT, 'core', 'server', 'engines', 'llama', 'bin')
LLAMA_DEST = 'core/server/engines/llama/bin'

# 逐文件收集，别把工具和说明文件也塞进分发包：
#   llama-quantize.exe  离线量化工具，运行时不调用
#   ggml-rpc.dll        远程后端，本地进程内推理用不到
#   *.txt               这个目录的说明文件
# ggml-vulkan.dll 约 58MB，默认保留：gpu_layers > 0 时要用，ASR 侧也可能
# 依赖它。只发纯 CPU 包时设 QIFEI_LLAMA_MINIMAL=1 剔除，实测省约 58MB，
# 精简后的 DLL 集合已用真实 .gguf 验证过能加载并生成。
LLAMA_MINIMAL = os.environ.get('QIFEI_LLAMA_MINIMAL', '').strip() == '1'
LLAMA_SKIP = {'llama-quantize.exe', 'ggml-rpc.dll'}
if LLAMA_MINIMAL:
    LLAMA_SKIP.add('ggml-vulkan.dll')


def _llama_datas():
    if not os.path.isdir(LLAMA_BIN):
        return []
    out = []
    for name in sorted(os.listdir(LLAMA_BIN)):
        src = os.path.join(LLAMA_BIN, name)
        if not os.path.isfile(src) or name in LLAMA_SKIP:
            continue
        if name.lower().endswith('.txt'):
            continue
        out.append((src, LLAMA_DEST))
    return out


LLAMA_DATAS = _llama_datas()

COMMON_EXCLUDES = [
    'IPython', 'matplotlib', 'tkinter', 'PyQt5', 'PyQt6', 'wx',
    'pandas', 'scipy', 'torch', 'funasr', 'tests', 'notebook',
]

GUI_HIDDEN = [
    'PySide6.QtCore', 'PySide6.QtGui', 'PySide6.QtWidgets', 'PySide6.QtNetwork',
    'sounddevice', 'numpy',
    'llmkit', 'llmkit.registry', 'llmkit.server', 'llmkit.connector',
    'llmkit.http_client', 'llmkit.gguf', 'llmkit.native',
    'llmkit.model_download',
]

CLIENT_HIDDEN = [
    'pynput.keyboard._win32', 'pynput.mouse._win32',
    'pystray._win32', 'sounddevice', 'pyclip', 'keyboard',
    'srt', 'pypinyin', 'watchdog.observers', 'websockets.legacy.client',
    'llmkit', 'llmkit.registry', 'llmkit.server', 'llmkit.connector',
    'llmkit.http_client', 'llmkit.gguf', 'llmkit.native',
    'llmkit.model_download',
]

CLIENT_EXCLUDES = COMMON_EXCLUDES + [
    'PySide6', 'sherpa_onnx', 'gguf', 'soundfile', 'onnxruntime',
]

SERVER_HIDDEN = [
    'sherpa_onnx', 'soundfile', 'pypinyin', 'pystray._win32',
    'websockets.legacy.server', 'sounddevice',
    # SenseVoice 是自定义 ONNX 引擎，由 factory 动态导入：不显式列出，
    # PyInstaller 静态分析扫不到，运行时会报
    # ModuleNotFoundError: No module named 'onnxruntime'（真机实测踩到过）
    'onnxruntime', 'sentencepiece',
] + collect_submodules('core.server.engines.sensevoice_onnx')

SERVER_EXCLUDES = COMMON_EXCLUDES + [
    'PySide6', 'pynput', 'pyclip', 'keyboard', 'openai', 'httpx',
]

# onnxruntime 的 capi DLL 不在普通 hiddenimports 收集范围内，要一起带走
SERVER_BINARIES = (collect_dynamic_libs('sherpa_onnx') +
                   collect_dynamic_libs('onnxruntime'))
SERVER_DATAS = (COMMON_DATAS + collect_data_files('sherpa_onnx') +
                collect_data_files('onnxruntime'))

tocs = []


def _build(name, script, hidden, excludes, datas, console, binaries=None):
    # 每个目标独立工作目录：三个 Analysis 共用一个目录时，后两个会删掉
    # 前一个的 base_library.zip，在禁止删除的环境里完整构建会直接失败。
    work = os.path.join(BASE_WORK, 'target-' + name)
    os.makedirs(work, exist_ok=True)
    CONF['workpath'] = work
    # 排查用：设 QIFEI_CONSOLE=1 让窗口程序也带控制台，方便看原生层报错
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
    tocs.append(analysis.binaries)
    tocs.append(analysis.datas)
    return analysis, exe


if TARGET in ('all', 'gui'):
    _build('QifeiIME', 'qifei_ime.py', GUI_HIDDEN, COMMON_EXCLUDES,
           COMMON_DATAS + LLAMA_DATAS, False)

if TARGET in ('all', 'client'):
    _build('QifeiClient', 'qifei_client.py', CLIENT_HIDDEN, CLIENT_EXCLUDES,
           COMMON_DATAS + LLAMA_DATAS, True)

if TARGET in ('all', 'server'):
    _build('QifeiServer', 'qifei_server.py', SERVER_HIDDEN, SERVER_EXCLUDES,
           SERVER_DATAS, True, SERVER_BINARIES)

COLLECT(
    *tocs,
    strip=False,
    upx=False,
    name='QifeiIME',
)
