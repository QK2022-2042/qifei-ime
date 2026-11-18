"""
LLM 连接器

把「用哪个后端、连哪个地址、用哪个模型」这一堆判断集中到一处，
界面（设置页）和运行时（听写客户端）都走同一套逻辑，
避免出现「界面上测试通过、实际听写却连不上」的情况。

后端有三种：
    local   —— 默认。进程内直连 llama.cpp，用程序自带的动态库加载 .gguf，
               不装 Ollama、不需要 llama-server.exe、不联网。
    ollama  —— 可选项，仅当用户本来就装了 Ollama
    openai  —— 任意 OpenAI 兼容的远程服务（DeepSeek / Moonshot / 智谱…）
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .constants import APIConfig
from .http_client import (LLMConnectError, LLMHTTPError, LLMStatusError,
                          LLMTimeoutError, make_client)
from . import server as llm_local_server
from . import registry as llm_model_registry


@dataclass
class Backend:
    """一次调用所需的全部连接信息"""
    kind: str = 'local'             # local | ollama | openai，默认直连 llama.cpp
    base_url: str = ''
    model: str = ''
    api_key: str = ''
    connect_timeout: float = APIConfig.DEFAULT_CONNECT_TIMEOUT
    read_timeout: float = APIConfig.DEFAULT_READ_TIMEOUT
    keep_alive: Any = APIConfig.DEFAULT_KEEP_ALIVE
    model_path: str = ''            # local 后端需要
    exe: str = ''                   # ollama.exe / llama-server.exe 路径
    context: int = 4096
    gpu_layers: int = 0
    n_threads: int = 0
    engine_mode: str = 'auto'       # auto | native | server

    @property
    def is_local(self) -> bool:
        return self.kind in ('ollama', 'local')

    def describe(self) -> str:
        if self.kind == 'local':
            return f'直连 llama.cpp · {self.model or Path(self.model_path).name or "未选择模型"}'
        if self.kind == 'ollama':
            return f'Ollama（可选）· {self.model}'
        return f'远程 API · {self.model} · {self.base_url}'


# ======================================================================
# 由配置构造
# ======================================================================
def default_settings() -> dict:
    """config.json 里还没有 llm 段时的默认设置

    老版本用户升级后可能从没打开过「大模型」页，这时界面默认值还没写进磁盘。
    客户端必须和界面用同一套默认值，否则又会偷偷走回 Ollama。
    另外：如果 models/llm/ 下放着随产品下载的推荐润色模型，这里会直接把它
    设为默认，客户端不用等用户先打开设置页就能预热和使用。
    """
    model_path = llm_model_registry.bundled_chat_model()
    return {
        'backend': 'local',
        'provider': 'local',
        'api_url': '',
        'api_key': '',
        'model': Path(model_path).name if model_path else '',
        'model_path': model_path,
        'unify_roles': True,
        'llama_port': APIConfig.LLAMA_PORT,
        'context': 4096,
        'gpu_layers': 0,
        'n_threads': 0,
        'engine_mode': 'auto',
        'connect_timeout': APIConfig.DEFAULT_CONNECT_TIMEOUT,
        'read_timeout': APIConfig.DEFAULT_READ_TIMEOUT,
        'keep_alive': APIConfig.DEFAULT_KEEP_ALIVE,
        'warmup_on_start': True,
    }


def from_settings(settings: Optional[dict] = None,
                  model: str = '') -> Backend:
    """从 config.json 的 llm 段构造"""
    data = dict(settings or default_settings())
    backend = str(data.get('backend') or 'local').lower()
    provider = str(data.get('provider') or 'local').lower()
    api_url = str(data.get('api_url') or '')
    api_key = str(data.get('api_key') or '')
    chosen = model or str(data.get('model') or '')

    connect_timeout = float(data.get('connect_timeout') or APIConfig.DEFAULT_CONNECT_TIMEOUT)
    read_timeout = float(data.get('read_timeout') or APIConfig.DEFAULT_READ_TIMEOUT)
    keep_alive = data.get('keep_alive', APIConfig.DEFAULT_KEEP_ALIVE)
    context = int(data.get('context') or 4096)

    if backend == 'auto':
        backend = _pick_auto(data)

    if backend == 'local':
        port = int(data.get('llama_port') or llm_local_server.DEFAULT_PORT)
        return Backend(
            kind='local',
            base_url=llm_local_server.base_url(port),
            model=chosen or _stem(data.get('model_path')),
            api_key='local',
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            keep_alive=None,
            model_path=_resolved_path(data),
            exe=str(data.get('llama_exe') or ''),
            context=context,
            gpu_layers=int(data.get('gpu_layers') or 0),
            n_threads=int(data.get('n_threads') or 0),
            engine_mode=str(data.get('engine_mode') or 'auto'),
        )

    if backend == 'openai':
        url = api_url or APIConfig.DEFAULT_API_URLS.get(provider, '')
        key = api_key or APIConfig.DEFAULT_API_KEYS.get(provider, '')
        return Backend(
            kind='openai',
            base_url=url,
            model=chosen,
            api_key=key,
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            keep_alive=None,
            context=context,
        )

    return Backend(
        kind='ollama',
        base_url=api_url or APIConfig.DEFAULT_API_URLS.get('ollama', llm_model_registry.DEFAULT_HOST),
        model=chosen,
        api_key='ollama',
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
        keep_alive=keep_alive,
        exe=str(data.get('ollama_exe') or ''),
        context=context,
    )


def from_role(role_config, settings: Optional[dict] = None) -> Backend:
    """从角色配置构造（运行时用）

    超时以 config.json 的 llm 段为准，和界面「大模型」页保持一致。
    角色文件里的 timeout 只作为 read_timeout 的兜底，绝不能拿来当
    connect_timeout —— 否则服务没起时要等 600 秒才报错。
    """
    provider = str(getattr(role_config, 'provider', 'local') or 'local').lower()
    backend_hint = str(getattr(role_config, 'backend', '') or '').lower()
    role_timeout = float(getattr(role_config, 'timeout', 0) or 0)
    keep_alive = getattr(role_config, 'keep_alive', None)
    api_url = str(getattr(role_config, 'api_url', '') or '')
    model = str(getattr(role_config, 'model', '') or '')

    data = dict(settings or default_settings())
    connect_timeout = float(data.get('connect_timeout') or APIConfig.DEFAULT_CONNECT_TIMEOUT)
    read_timeout = float(data.get('read_timeout') or role_timeout
                         or APIConfig.DEFAULT_READ_TIMEOUT)

    if provider == 'local' or backend_hint == 'local':
        port = int(data.get('llama_port') or llm_local_server.DEFAULT_PORT)
        return Backend(
            kind='local',
            base_url=api_url or llm_local_server.base_url(port),
            model=model or _stem(data.get('model_path')),
            api_key='local',
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            model_path=_resolved_path(data),
            exe=str(data.get('llama_exe') or ''),
            context=int(data.get('context') or 4096),
            gpu_layers=int(data.get('gpu_layers') or 0),
            n_threads=int(data.get('n_threads') or 0),
            engine_mode=str(data.get('engine_mode') or 'auto'),
        )

    if provider == 'ollama':
        return Backend(
            kind='ollama',
            base_url=api_url or APIConfig.DEFAULT_API_URLS.get('ollama', llm_model_registry.DEFAULT_HOST),
            model=model,
            api_key='ollama',
            connect_timeout=connect_timeout,
            read_timeout=read_timeout,
            keep_alive=keep_alive if keep_alive is not None else APIConfig.DEFAULT_KEEP_ALIVE,
            exe=str(data.get('ollama_exe') or ''),
        )

    return Backend(
        kind='openai',
        base_url=api_url or APIConfig.DEFAULT_API_URLS.get(provider, ''),
        model=model,
        api_key=str(getattr(role_config, 'api_key', '') or '') or APIConfig.DEFAULT_API_KEYS.get(provider, ''),
        connect_timeout=connect_timeout,
        read_timeout=read_timeout,
    )


def _pick_auto(data: dict) -> str:
    """自动挑选后端：默认直连 llama.cpp，不再主动往 Ollama 上靠

    只有「用户已经装了并跑着 Ollama，且明确写了模型名」时才用 Ollama。
    """
    if data.get('model_path'):
        return 'local'
    host = str(data.get('api_url') or llm_model_registry.DEFAULT_HOST)
    if str(data.get('model') or '') and llm_model_registry.is_serving(host):
        return 'ollama'
    return 'local'


def _stem(path: Optional[str]) -> str:
    if not path:
        return ''
    from pathlib import Path
    return Path(str(path)).stem


def _resolved_path(data: dict) -> str:
    """把配置里的 model_path 解析成这台机器上真实可用的路径。

    听写客户端直接读 config.json，不经过 qifei.Store，所以 Store 里的
    自愈写回覆盖不到它（尤其是 GUI 从来没被打开过的新机器）。这里再兜
    一次：路径有效原样返回，失效则按文件名重定位或退回随包内置模型。
    有效路径只是一次 is_file() 检查，没有额外 IO 开销。
    """
    configured = str(data.get('model_path') or '')
    resolved, _note = llm_model_registry.resolve_model_path(
        configured, data.get('model_dirs') or [])
    return resolved


# ======================================================================
# 就绪检查
# ======================================================================
def ensure(backend: Backend, wait_seconds: float = 30.0) -> Tuple[bool, str]:
    """确保后端可用：Ollama 拉起服务，llama.cpp 加载模型"""
    if backend.kind == 'local':
        llm_local_server.configure(
            gpu_layers=backend.gpu_layers,
            n_threads=backend.n_threads,
            engine_mode=backend.engine_mode,
        )
        server = llm_local_server.get_server(
            backend.exe, _port_of(backend.base_url))
        return server.ensure(
            backend.model_path,
            context=backend.context,
            gpu_layers=backend.gpu_layers,
            n_threads=backend.n_threads,
            wait_seconds=max(wait_seconds, 120.0),
        )

    if backend.kind == 'ollama':
        return llm_model_registry.ensure_serving(
            backend.base_url, backend.exe, wait_seconds=wait_seconds)

    return True, '远程服务无需启动'


def _port_of(url: str) -> int:
    from urllib.parse import urlsplit
    try:
        return urlsplit(url).port or llm_local_server.DEFAULT_PORT
    except Exception:
        return llm_local_server.DEFAULT_PORT


# ======================================================================
# 探测与测试
# ======================================================================
def probe(backend: Backend, timeout: float = 3.0) -> Dict[str, Any]:
    """只探测连通性与模型列表，不发对话"""
    result: Dict[str, Any] = {
        'ok': False, 'message': '', 'models': [], 'latency_ms': 0,
        'kind': backend.kind,
    }
    start = time.perf_counter()
    try:
        if backend.kind == 'ollama':
            client = make_client('ollama', backend.base_url,
                                 connect_timeout=timeout, read_timeout=timeout)
            version = client.version(timeout=timeout)
            models = client.tags(timeout=timeout)
            result['models'] = [str(m.get('name') or '') for m in models]
            result['version'] = version
            result['message'] = f'Ollama {version}，已安装 {len(models)} 个模型'
        elif backend.kind == 'local':
            ok, message = llm_local_server.get_server(
                backend.exe, _port_of(backend.base_url)).status()
            result['ok'] = ok
            result['message'] = message
            result['latency_ms'] = int((time.perf_counter() - start) * 1000)
            return result
        else:
            client = make_client('openai', backend.base_url, backend.api_key,
                                 connect_timeout=timeout, read_timeout=timeout)
            models = client.models(timeout=timeout)
            result['models'] = [str(m.get('id') or '') for m in models]
            result['message'] = f'接口可用，可用模型 {len(models)} 个'
        result['ok'] = True
    except LLMStatusError as exc:
        result['message'] = str(exc)
    except (LLMConnectError, LLMTimeoutError) as exc:
        result['message'] = str(exc)
    except LLMHTTPError as exc:
        result['message'] = str(exc)
    except Exception as exc:
        result['message'] = f'{type(exc).__name__}: {exc}'
    result['latency_ms'] = int((time.perf_counter() - start) * 1000)
    return result


def chat_test(backend: Backend, prompt: str = '你好，请只回复「好」',
              max_tokens: int = 24) -> Dict[str, Any]:
    """真发一轮对话，测首字延迟与总耗时"""
    result: Dict[str, Any] = {
        'ok': False, 'message': '', 'text': '',
        'first_token_ms': 0, 'total_ms': 0,
    }
    if not backend.model and backend.kind != 'local':
        result['message'] = '还没有选择模型'
        return result

    start = time.perf_counter()
    first_at = 0.0
    text = ''
    try:
        client = make_client(
            backend.kind if backend.kind != 'local' else 'openai',
            backend.base_url, backend.api_key,
            connect_timeout=backend.connect_timeout,
            read_timeout=backend.read_timeout,
            keep_alive=backend.keep_alive,
        )
        messages = [{'role': 'user', 'content': prompt}]

        if backend.kind == 'ollama':
            stream = client.chat(model=backend.model, messages=messages,
                                 stream=True,
                                 options={'temperature': 0.2,
                                          'num_predict': max_tokens})
        else:
            stream = client.chat.completions.create(
                model=backend.model or 'local', messages=messages,
                stream=True, temperature=0.2, max_tokens=max_tokens,
            )

        for chunk in stream:
            piece = _chunk_text(chunk)
            if piece:
                if not first_at:
                    first_at = time.perf_counter()
                text += piece
                if len(text) > 200:
                    break

        result['ok'] = True
        result['text'] = text.strip()
        result['message'] = '连接正常'
    except LLMStatusError as exc:
        if exc.model_missing:
            if backend.kind == 'local':
                result['message'] = (f'本地模型「{backend.model}」没有加载成功。'
                                     '请到「大模型」页在「模型文件」下拉里'
                                     '选一个 .gguf —— 直连 llama.cpp，'
                                     '不用装 Ollama，也不用联网下载。')
            else:
                # v2.14：远程 API / Ollama 后端下 llm.model 是服务方模型名，
                # 老实现一律把用户往本地 gguf 文件上引，方向完全相反。
                result['message'] = (
                    f'服务方模型「{backend.model}」不可用。'
                    '请到「大模型」页的「服务方模型名」输入框'
                    '填写服务商提供的模型名（例如 deepseek-chat），'
                    '并确认服务地址 / API Key 正确。')
        else:
            result['message'] = str(exc)
    except (LLMConnectError, LLMTimeoutError) as exc:
        result['message'] = str(exc)
    except LLMHTTPError as exc:
        result['message'] = str(exc)
    except Exception as exc:
        result['message'] = f'{type(exc).__name__}: {exc}'

    total = time.perf_counter() - start
    result['first_token_ms'] = int((first_at - start) * 1000) if first_at else 0
    result['total_ms'] = int(total * 1000)
    return result


def _chunk_text(chunk) -> str:
    message = getattr(chunk, 'message', None)
    if message is not None:
        return getattr(message, 'content', '') or ''
    choices = getattr(chunk, 'choices', None) or []
    if choices:
        delta = getattr(choices[0], 'delta', None)
        if delta is not None:
            return getattr(delta, 'content', '') or ''
    return ''


def warmup(backend: Backend) -> Tuple[bool, str]:
    """预热：把模型加载进内存，正式听写时首字就快"""
    if backend.kind == 'local':
        return llm_local_server.get_server(
            backend.exe, _port_of(backend.base_url)).warmup()
    if backend.kind == 'ollama':
        if not backend.model:
            return False, '没有选择模型'
        try:
            # make_client 的第一个参数是 provider，不是 URL（以前传错了，预热必失败）。
            # 预热本身就是在等模型加载，read_timeout 必须给足。
            client = make_client('ollama', backend.base_url,
                                 connect_timeout=APIConfig.DEFAULT_CONNECT_TIMEOUT,
                                 read_timeout=backend.read_timeout)
            for _ in client.chat(model=backend.model,
                                 messages=[{'role': 'user', 'content': 'hi'}],
                                 stream=False,
                                 options={'num_predict': 1}):
                break
            return True, '预热完成'
        except Exception as exc:
            return False, f'预热失败：{exc}'
    return True, '远程服务无需预热'
