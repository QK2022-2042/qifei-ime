# coding: utf-8
"""
跨平台按键模拟

Windows 继续用 `keyboard` 库（项目原有行为，不改变 Windows 侧任何逻辑）；
macOS / Linux 用 pynput，避免 `keyboard` 库在 macOS 上依赖 Quartz/AppKit
且需要 root 权限的问题。

对外接口保持与原来的 `keyboard.press_and_release` / `keyboard.write` 等价。
"""

from __future__ import annotations

import sys
from typing import List

_IS_WINDOWS = sys.platform == 'win32'

if _IS_WINDOWS:
    import keyboard as _keyboard  # type: ignore
    _pynput = None
else:
    _keyboard = None
    from pynput import keyboard as _pynput  # type: ignore

# 修饰键别名 -> pynput Key 名
_MODIFIERS = {
    'ctrl': 'ctrl', 'control': 'ctrl',
    'alt': 'alt', 'option': 'alt',
    'shift': 'shift',
    'cmd': 'cmd', 'command': 'cmd', 'win': 'cmd', 'super': 'cmd',
}

# 普通特殊键别名 -> pynput Key 名
_SPECIALS = {
    'enter': 'enter', 'return': 'enter',
    'esc': 'esc', 'escape': 'esc',
    'tab': 'tab', 'space': 'space',
    'backspace': 'backspace', 'delete': 'delete',
    'up': 'up', 'down': 'down', 'left': 'left', 'right': 'right',
    'home': 'home', 'end': 'end',
    'page_up': 'page_up', 'page_down': 'page_down',
    'caps_lock': 'caps_lock',
}


def _resolve_pynput_key(name: str):
    """把 'ctrl' / 'enter' / 'a' 这样的名字解析成 pynput 按键对象"""
    name = name.strip().lower()
    canonical = _MODIFIERS.get(name) or _SPECIALS.get(name)
    if canonical:
        return getattr(_pynput.Key, canonical)
    if len(name) == 1:
        return _pynput.KeyCode.from_char(name)
    return getattr(_pynput.Key, name)


def press_and_release(combo: str) -> None:
    """按一下组合键，例如 'enter'、'ctrl+c'、'cmd+v'"""
    if _keyboard is not None:
        _keyboard.press_and_release(combo)
        return

    controller = _pynput.Controller()
    keys = [_resolve_pynput_key(part) for part in combo.split('+') if part]
    if not keys:
        return
    for key in keys:
        controller.press(key)
    for key in reversed(keys):
        controller.release(key)


def write_text(text: str) -> None:
    """逐字写入文本（仅用于不支持粘贴兜底的场景）"""
    if _keyboard is not None:
        _keyboard.write(text)
        return
    _pynput.Controller().type(text)


def pressed_key_names() -> List[str]:
    """返回当前按下的键名（仅 Windows 可用，其它平台返回空列表）"""
    if _keyboard is None:
        return []
    try:
        return [str(key) for key in _keyboard._pressed_events.keys()]
    except Exception:
        return []
