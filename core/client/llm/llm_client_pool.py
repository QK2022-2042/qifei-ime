"""
LLM 客户端池

职责：
1. 按 provider / 地址缓存客户端实例（连接复用，少一次 TCP 握手）
2. 统一处理超时：连接超时短、读取超时长，本地模型冷加载不会被误杀
3. 不再依赖 openai / ollama SDK —— 用标准库实现，离线包不用再装可选依赖
"""
from typing import Any, Dict, Optional

from .llm_constants import APIConfig
from llmkit.http_client import make_client


class ClientPool:
    """LLM 客户端池"""

    def __init__(self):
        self._clients: Dict[str, Any] = {}

    def get_client(self, provider: str, api_url: str = '', api_key: str = '',
                   timeout: Optional[float] = None,
                   connect_timeout: Optional[float] = None,
                   keep_alive: Any = None) -> Any:
        """获取 LLM 客户端（带缓存）

        Args:
            provider: 'ollama' / 'local' / 'openai' 等
            api_url: API 地址，留空用默认值
            api_key: API Key
            timeout: 读取超时（秒），None 用 provider 默认值
            connect_timeout: 连接超时（秒），跟随界面「大模型」页的设置
            keep_alive: Ollama 模型常驻时长，-1 表示常驻

        Returns:
            具备 ollama 或 openai 风格接口的客户端对象
        """
        provider = (provider or 'ollama').lower()
        final_url = api_url or APIConfig.DEFAULT_API_URLS.get(provider, '')
        final_key = api_key or APIConfig.DEFAULT_API_KEYS.get(provider, '')

        read_timeout = float(
            timeout or APIConfig.DEFAULT_TIMEOUTS.get(provider,
                                                      APIConfig.DEFAULT_TIMEOUT)
        )
        # 不再写死默认值：界面里调的「连接超时」要能真正生效
        connect_timeout = float(
            connect_timeout or APIConfig.DEFAULT_CONNECT_TIMEOUT
        )

        # 两个超时都进缓存键：改了设置要拿到新客户端，不能复用旧超时的实例
        cache_key = (f'{provider}|{final_url}|{connect_timeout}|'
                     f'{read_timeout}|{keep_alive}')

        if cache_key not in self._clients:
            self._clients[cache_key] = make_client(
                provider=provider,
                api_url=final_url,
                api_key=final_key,
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
                keep_alive=keep_alive,
            )

        return self._clients[cache_key]

    def clear(self):
        """清空客户端缓存"""
        self._clients.clear()
