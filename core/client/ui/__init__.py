# coding: utf-8
"""
客户端 UI 门面模块

该模块作为客户端访问 UI 功能的统一入口。
在导入此模块时，会自动将客户端的 logger 注入到通用 UI 模块中。
并重新导出常用的 UI 组件。
"""

from .. import logger
import core.ui

# 1. 注入 Client Logger 到通用 UI 模块
core.ui.set_ui_logger(logger)

# 2. 导出客户端特有的 UI 组件
from core.client.ui.tips import TipsDisplay

# 3. 重新导出通用 UI 组件 (Re-export)
# 这样客户端其他模块只需 from core.client.ui import ... 即可
from core.ui import (
    toast,
    toast_stream,
    ToastMessage,
    ToastMessageManager,
    ToastMessageManager,
    enable_min_to_tray,
    stop_tray,
)

# 4. 导出菜单处理器（供 Startup 使用）
#    这两个处理器依赖 tkinter 弹窗；精简 Python 环境没有 tkinter 时降级为空实现，
#    听写主流程不受影响（启飞输入法用 Qt 界面取代了这些弹窗）。
try:
    from core.ui.hotword_menu_handler import on_add_hotword
    from core.ui.context_menu_handler import on_edit_context
except Exception as _menu_error:  # pragma: no cover - 取决于运行环境
    logger.debug(f'托盘菜单弹窗不可用（{_menu_error}），已降级')

    def on_add_hotword(*args, **kwargs):  # type: ignore[misc]
        return None

    def on_edit_context(*args, **kwargs):  # type: ignore[misc]
        return None

__all__ = [
    'logger',
    'TipsDisplay',
    'toast',
    'toast_stream',
    'ToastMessage',
    'ToastMessageManager',
    'enable_min_to_tray',
    'stop_tray',
    'on_add_hotword',
    'on_edit_context',
]
