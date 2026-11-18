"""
llmkit —— 本地大模型接入工具包

零第三方依赖，只做五件事：
1. 读 GGUF 文件信息（多少参数、什么量化、上下文多长、有没有对话模板）
2. 扫本机已有的模型（各处散落的 .gguf，可选 Ollama 已安装的）
3. 用项目自带的 llama.cpp 动态库**进程内直接加载** .gguf —— 不装 Ollama、
   不需要 llama-server.exe、不联网；Ollama 只是个可选项
4. 用统一的客户端发请求，连接超时和读取超时分开管
5. 可选地一键下载随产品提供的推荐润色模型（唯一需要联网的动作），
   下载完推理仍然全程离线

界面和听写客户端都走这一套，保证「测试通过 = 实际能用」。
"""

from __future__ import annotations

from .constants import APIConfig
from .gguf import GgufInfo, read_info, looks_like_gguf
from .http_client import (LLMConnectError, LLMHTTPError, LLMStatusError,
                          LLMTimeoutError, OllamaClient, OpenAICompatClient,
                          make_client)
from .registry import (BUNDLED_CHAT_MODEL, LocalModel, app_root,
                       bundled_chat_model, default_scan_dirs, discover,
                       ensure_serving, find_ollama_exe, import_gguf,
                       is_serving, list_installed, norm_path, same_path,
                       resolve_model_path, sanitize_name, scan_gguf)
from .connector import default_settings
from . import connector, server, native, model_download

__all__ = [
    'APIConfig', 'default_settings',
    'GgufInfo', 'read_info', 'looks_like_gguf',
    'LLMConnectError', 'LLMHTTPError', 'LLMStatusError', 'LLMTimeoutError',
    'OllamaClient', 'OpenAICompatClient', 'make_client',
    'BUNDLED_CHAT_MODEL', 'bundled_chat_model',
    'LocalModel', 'app_root', 'default_scan_dirs', 'discover',
    'norm_path', 'same_path', 'resolve_model_path',
    'ensure_serving', 'find_ollama_exe', 'import_gguf', 'is_serving',
    'list_installed', 'sanitize_name', 'scan_gguf',
    'connector', 'server', 'native', 'model_download',
]
