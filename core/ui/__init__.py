"""UI 工具模块

提供 Toast 浮动消息通知和系统托盘功能。
该模块设计为 Client 和 Server 共用，日志记录器通过注入方式加载。
"""
import logging
from typing import Any

# ============================================================
# Logger 代理机制
# ============================================================

class _LoggerProxy:
    """
    日志代理类（利用 __getattr__ 动态转发）
    允许先导入 logger 对象，稍后再注入真正的实现。
    """
    def __init__(self):
        self._target = logging.getLogger('core.ui')  # 默认 logger

    def set_target(self, logger):
        """注入真正的 logger 实现"""
        self._target = logger

    def __getattr__(self, name):
        """将所有属性访问转发给真正的 logger"""
        return getattr(self._target, name)

# 1. 创建代理实例
logger = _LoggerProxy()

def set_ui_logger(real_logger):
    """设置 UI 模块使用的日志记录器"""
    logger.set_target(real_logger)

# ============================================================
# 导出组件
# ============================================================

from .tray import enable_min_to_tray, stop_tray

# Toast 依赖 tkinter。部分精简版 Python 环境没有 tkinter，
# 启飞输入法的悬浮条已经取代了 Toast，所以这里做降级处理：
# 缺少 tkinter 时退化为空实现，保证听写主流程照常运行。
try:
    from .toast import toast, toast_stream, ToastMessage, ToastMessageManager
except Exception as _toast_error:  # pragma: no cover - 取决于运行环境
    logger.debug(f'Toast 不可用（{_toast_error}），已降级为空实现')

    class ToastMessage:  # type: ignore[no-redef]
        """Toast 消息占位类型"""

    class ToastMessageManager:  # type: ignore[no-redef]
        """Toast 管理器占位类型"""

        @classmethod
        def instance(cls):
            return cls()

        def show(self, *args, **kwargs):
            return None

        def stop(self):
            return None

    def toast(*args, **kwargs):  # type: ignore[misc]
        return None

    def toast_stream(*args, **kwargs):  # type: ignore[misc]
        return None

__all__ = [
    'logger',
    'set_ui_logger',
    'toast',
    'toast_stream',
    'ToastMessage',
    'ToastMessageManager',
    'enable_min_to_tray',
    'stop_tray',
]
