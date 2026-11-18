"""
LLM 连接相关的默认值

集中放在这里，界面、客户端、自检脚本都读同一份，不会各写一套。
"""

from __future__ import annotations

from typing import Dict


class APIConfig:
    """API 提供商配置"""

    # 默认 API 地址
    DEFAULT_API_URLS: Dict[str, str] = {
        'ollama': 'http://127.0.0.1:11434',
        'local': 'http://127.0.0.1:11500/v1',     # 内置 llama.cpp
        'lmstudio': 'http://127.0.0.1:1234/v1',
        'openai': 'https://api.openai.com/v1',
        'deepseek': 'https://api.deepseek.com/v1',
        'moonshot': 'https://api.moonshot.cn/v1',
        'zhipu': 'https://open.bigmodel.cn/api/paas/v4',
        'volcengine': 'https://ark.cn-beijing.volces.com/api/v3',
        'cerebras': 'https://api.cerebras.ai/v1',
    }

    DEFAULT_API_KEYS: Dict[str, str] = {
        'ollama': 'ollama',
        'local': 'local',
        'lmstudio': 'lmstudio',
        'openai': '',
        'deepseek': '',
        'moonshot': '',
        'zhipu': '',
        'cerebras': '',
    }

    # ── 超时策略 ──────────────────────────────────────────────
    # 老版本把超时写死 2 秒，本地模型光加载权重就要几十秒，
    # 必然 100% 失败，这是「经常超时」的根因。
    #
    # 现在拆成两个：
    #   connect_timeout —— 连不上就赶紧报错（服务没起、端口错）
    #   read_timeout    —— 连上之后愿意等多久，本地模型给足 10 分钟
    DEFAULT_CONNECT_TIMEOUT = 5.0
    DEFAULT_READ_TIMEOUT = 600.0

    DEFAULT_TIMEOUTS: Dict[str, float] = {
        'ollama': 600.0,
        'local': 600.0,
        'lmstudio': 300.0,
        'openai': 180.0,
        'deepseek': 180.0,
        'moonshot': 180.0,
        'zhipu': 180.0,
        'cerebras': 180.0,
        'claude': 180.0,
        'gemini': 180.0,
    }

    DEFAULT_TIMEOUT = 180.0

    # 本地模型常驻内存，避免每次说话都重新加载（-1 表示永不卸载）
    DEFAULT_KEEP_ALIVE = -1

    # 冷启动失败后自动重试次数（本地模型首次加载超时很常见）
    COLD_START_RETRIES = 1
    COLD_START_RETRY_DELAY = 2.0

    # 内置 llama.cpp 默认端口
    LLAMA_PORT = 11500
