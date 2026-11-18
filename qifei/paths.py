# coding: utf-8
"""路径解析：同时兼容源码运行与 PyInstaller 打包运行"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from . import brand


def is_frozen() -> bool:
    """是否为打包后的可执行文件"""
    return bool(getattr(sys, 'frozen', False))


def app_root() -> Path:
    """
    应用根目录

    - 打包后：exe 所在目录（配置、模型、日志都在这里，便于便携）
    - 源码运行：项目根目录
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def resource(*parts: str) -> Path:
    """只读资源路径（打包后位于 _MEIPASS）"""
    base = getattr(sys, '_MEIPASS', None)
    root = Path(base) if base else app_root()
    return root.joinpath(*parts)


def data_dir() -> Path:
    """可写数据目录：Windows 用 %APPDATA%\\QifeiIME，macOS 用 ~/Library/Application Support/QifeiIME"""
    if sys.platform == 'darwin':
        path = Path.home() / 'Library' / 'Application Support' / brand.APP_ID
    else:
        appdata = os.environ.get('APPDATA')
        base = Path(appdata) if appdata else Path.home()
        path = base / brand.APP_ID
    path.mkdir(parents=True, exist_ok=True)
    return path


def log_dir() -> Path:
    path = data_dir() / 'logs'
    path.mkdir(parents=True, exist_ok=True)
    return path


def default_model_dir() -> Path:
    """默认模型目录：应用根目录下的 models"""
    return app_root() / 'models'


def asset(*parts: str) -> Path:
    """应用资源（图标、插画）

    macOS 打包后 assets/ 会被 PyInstaller 放进 Contents/Resources，并交叉
    链接到 Contents/Frameworks（= _MEIPASS）；exe 所在的 Contents/MacOS
    按签名规范不允许放数据文件，所以优先查 _MEIPASS。
    """
    packed = resource('assets', *parts)
    if packed.exists():
        return packed
    return app_root().joinpath('assets', *parts)


def icon_path() -> str:
    """应用图标（ico 优先，缺失时返回空串）"""
    for name in ('icon.ico', 'qifei.ico'):
        for base in (asset(name), resource('assets', name)):
            if base.exists():
                return str(base)
    return ''
