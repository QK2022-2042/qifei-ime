# coding: utf-8
"""
配置覆盖层

GUI（启飞输入法）把用户设置写成 JSON，本模块负责读取该 JSON，
再由 config_client / config_server 在导入时把值覆盖到配置类上。

设计原则：
1. 只用标准库，避免导入 core 包内的其它模块，防止循环导入。
2. JSON 缺失、损坏时静默回退到代码里的默认值，保证命令行运行不受影响。
3. 只覆盖配置类里已存在的属性，避免拼写错误引入未知字段。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict

# 应用标识（改名时只需改这里和 qifei/brand.py）
APP_ID = os.environ.get('QIFEI_APP_ID', 'QifeiIME')

# 环境变量可直接指定配置文件，便于便携运行与调试
ENV_CONFIG = 'QIFEI_CONFIG'


def config_dir() -> Path:
    """配置目录：Windows 用 %APPDATA%\\<APP_ID>，macOS 用 ~/Library/Application Support/<APP_ID>"""
    override = os.environ.get(ENV_CONFIG)
    if override:
        return Path(override).expanduser().resolve().parent
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / APP_ID
    appdata = os.environ.get('APPDATA')
    base = Path(appdata) if appdata else Path.home()
    return base / APP_ID


def config_path() -> Path:
    """配置文件完整路径"""
    override = os.environ.get(ENV_CONFIG)
    if override:
        return Path(override).expanduser().resolve()
    return config_dir() / 'config.json'


def load_overrides() -> Dict[str, Any]:
    """读取配置覆盖，失败时返回空字典"""
    path = config_path()
    try:
        if not path.exists():
            return {}
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_overrides(data: Dict[str, Any]) -> Path:
    """原子写入配置覆盖，返回写入路径"""
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.tmp')
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)
    return path


def apply_to(cls: type, values: Dict[str, Any]) -> None:
    """
    把字典中的值覆盖到配置类上

    只处理类里已经定义的属性，跳过以 _ 开头的内部字段。
    """
    if not isinstance(values, dict):
        return
    for key, value in values.items():
        if not isinstance(key, str) or key.startswith('_'):
            continue
        if not hasattr(cls, key):
            continue
        # 类型基本校验：列表字段必须仍然是列表，避免 GUI 写入脏数据导致运行时崩溃
        current = getattr(cls, key)
        if isinstance(current, list) and not isinstance(value, list):
            continue
        if isinstance(current, bool) and not isinstance(value, bool):
            continue
        setattr(cls, key, value)
