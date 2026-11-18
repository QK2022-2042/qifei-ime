"""
LLM 模块

提供 LLM 相关的所有功能，包括角色管理、消息构建、上下文管理等。

注意：这里用惰性导入（PEP 562）。
设置界面只需要「扫模型 / 测连接」，没必要把整个听写客户端（pynput、
sounddevice、websocket…）跟着加载一遍，否则打开设置页就要等好几秒。
"""

from core import get_logger

logger = get_logger('client')

# 名字 -> (子模块, 属性名)
_LAZY = {
    'LLMHandler': ('llm_handler', 'LLMHandler'),
    'LLMResult': ('llm_handler', 'LLMResult'),
    'RoleConfig': ('llm_role_config', 'RoleConfig'),
    'RoleLoader': ('llm_role_loader', 'RoleLoader'),
    'ContextManager': ('llm_context', 'ContextManager'),
    'MessageBuilder': ('llm_message_builder', 'MessageBuilder'),
    'ClientPool': ('llm_client_pool', 'ClientPool'),
    'copy_to_clipboard': ('llm_clipboard', 'copy_to_clipboard'),
    'get_selected_text': ('llm_get_selection', 'get_selected_text'),
    'record_selection_usage': ('llm_get_selection', 'record_selection_usage'),
    'LLMFileWatcher': ('llm_watcher', 'LLMFileWatcher'),
    'StopMonitor': ('llm_stop_monitor', 'StopMonitor'),
    'handle_toast_mode': ('llm_output_toast', 'handle_toast_mode'),
    'handle_typing_mode': ('llm_output_typing', 'handle_typing_mode'),
}

__all__ = [
    'LLMHandler', 'LLMResult',
    'RoleConfig', 'RoleLoader',
    'ContextManager',
    'MessageBuilder', 'ClientPool',
    'get_selected_text', 'record_selection_usage', 'copy_to_clipboard',
    'LLMFileWatcher', 'StopMonitor',
    'handle_toast_mode', 'handle_typing_mode',
]


def __getattr__(name: str):
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f'module {__name__!r} has no attribute {name!r}')
    from importlib import import_module
    module = import_module(f'.{target[0]}', __name__)
    value = getattr(module, target[1])
    globals()[name] = value
    return value


def __dir__():
    return sorted(list(globals().keys()) + list(_LAZY.keys()))
