# coding: utf-8
"""跨平台打开文件 / 目录（替代 os.startfile）"""

from __future__ import annotations

import os
import subprocess
import sys


def open_path(path) -> None:
    """用系统默认方式打开文件或目录"""
    target = str(path)
    if sys.platform == 'win32':
        os.startfile(target)  # type: ignore[attr-defined]
    elif sys.platform == 'darwin':
        subprocess.Popen(['open', target])
    else:
        subprocess.Popen(['xdg-open', target])
