# coding: utf-8
"""开机自启：Windows 写 HKCU 的 Run 键，macOS 写 LaunchAgent，均无需管理员权限"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from . import paths

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'
VALUE_NAME = 'QifeiIME'
LAUNCH_AGENT_LABEL = 'cn.qifei.ime'

_PLIST_TEMPLATE = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
{args}
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""


def _command() -> str:
    if paths.is_frozen():
        return f'"{Path(sys.executable)}" --tray'
    entry = paths.app_root() / 'qifei_ime.py'
    pythonw = Path(sys.executable).with_name('pythonw.exe')
    exe = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{exe}" "{entry}" --tray'


# ---------- macOS LaunchAgent ----------

def _launch_agent_path() -> Path:
    return Path.home() / 'Library' / 'LaunchAgents' / f'{LAUNCH_AGENT_LABEL}.plist'


def _darwin_program_args() -> list[str]:
    if paths.is_frozen():
        return [str(Path(sys.executable)), '--tray']
    entry = paths.app_root() / 'qifei_ime.py'
    return [sys.executable, str(entry), '--tray']


def _darwin_is_enabled() -> bool:
    return _launch_agent_path().exists()


def _darwin_set_enabled(enabled: bool) -> bool:
    plist_path = _launch_agent_path()
    try:
        if enabled:
            plist_path.parent.mkdir(parents=True, exist_ok=True)
            args = '\n'.join(
                f'        <string>{arg}</string>' for arg in _darwin_program_args()
            )
            plist_path.write_text(
                _PLIST_TEMPLATE.format(label=LAUNCH_AGENT_LABEL, args=args),
                encoding='utf-8',
            )
            subprocess.run(['launchctl', 'load', '-w', str(plist_path)],
                           capture_output=True, timeout=10)
        else:
            if plist_path.exists():
                subprocess.run(['launchctl', 'unload', '-w', str(plist_path)],
                               capture_output=True, timeout=10)
                plist_path.unlink()
        return True
    except Exception:
        return False


# ---------- 公共接口 ----------

def is_enabled() -> bool:
    if sys.platform == 'darwin':
        return _darwin_is_enabled()
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(value)
    except Exception:
        return False


def set_enabled(enabled: bool) -> bool:
    if sys.platform == 'darwin':
        return _darwin_set_enabled(enabled)
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True
    except Exception:
        return False
