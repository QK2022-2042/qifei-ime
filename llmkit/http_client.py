"""
纯标准库 LLM HTTP 客户端

为什么不用 openai / ollama 官方 SDK：
1. 它们是可选依赖，离线包里经常没装，一装就报 ModuleNotFoundError；
2. SDK 的超时是「整条请求」的总超时，本地模型冷加载动辄几十秒，
   只要总超时设小了必然失败，设大了网络异常又要等很久；
3. SDK 报错类型五花八门，很难给出「模型没装」这种精准提示。

这里直接基于 http.client 实现，做到：
- 连接超时与读取超时分开：连不上 3 秒内就报错，读取可以等 10 分钟；
- 本地地址（127.0.0.1 / localhost）强制不走系统代理，
  避免企业代理把回环地址也拦下来；
- 流式响应按行解析，Ollama 的 NDJSON 和 OpenAI 兼容的 SSE 都支持；
- 返回对象用 SimpleNamespace 模拟 SDK 的属性结构，
  上层 llm_processor 不用改一行代码。
"""

from __future__ import annotations

import http.client
import json
import socket
import ssl
from types import SimpleNamespace
from typing import Any, Dict, Iterable, Iterator, List, Optional
from urllib.parse import urlsplit

LOCAL_HOSTS = {'127.0.0.1', 'localhost', '::1', '0.0.0.0'}


# ======================================================================
# 异常
# ======================================================================
class LLMHTTPError(Exception):
    """LLM HTTP 调用异常基类"""

    def __init__(self, message: str, status: int = 0, body: str = '',
                 url: str = ''):
        self.status = status
        self.body = body
        self.url = url
        super().__init__(message)


class LLMConnectError(LLMHTTPError):
    """连不上服务（进程没起、端口不通）"""


class LLMTimeoutError(LLMHTTPError):
    """连接或读取超时"""


class LLMStatusError(LLMHTTPError):
    """服务端返回了非 2xx"""

    @property
    def model_missing(self) -> bool:
        """是否属于「模型不存在」"""
        if self.status not in (404, 400):
            return False
        text = (self.body or '').lower()
        return ('not found' in text) or ('no such model' in text) \
            or ('model' in text and 'not' in text)


# ======================================================================
# 底层连接
# ======================================================================
def _split_url(url: str):
    parts = urlsplit(url)
    scheme = (parts.scheme or 'http').lower()
    host = parts.hostname or '127.0.0.1'
    port = parts.port or (443 if scheme == 'https' else 80)
    path = parts.path or '/'
    if parts.query:
        path = f'{path}?{parts.query}'
    return scheme, host, port, path


def _connect(url: str, connect_timeout: float, read_timeout: float):
    """建立连接：connect_timeout 管握手，read_timeout 管之后每一次读"""
    scheme, host, port, path = _split_url(url)
    try:
        if scheme == 'https':
            conn = http.client.HTTPSConnection(
                host, port, timeout=connect_timeout,
                context=ssl.create_default_context(),
            )
        else:
            conn = http.client.HTTPConnection(host, port, timeout=connect_timeout)
        conn.connect()
    except socket.timeout as exc:
        raise LLMTimeoutError(
            f'连接 {host}:{port} 超时（{connect_timeout:.0f} 秒）',
            url=url) from exc
    except (ConnectionRefusedError, ConnectionResetError) as exc:
        raise LLMConnectError(
            f'无法连接 {host}:{port}，服务可能没有启动',
            url=url) from exc
    except OSError as exc:
        raise LLMConnectError(f'无法连接 {host}:{port}：{exc}', url=url) from exc

    # 握手成功后放宽到读取超时，本地模型加载多久都不会被掐断
    try:
        if conn.sock is not None:
            conn.sock.settimeout(read_timeout)
    except OSError:
        pass
    return conn, path


def _iter_lines(url: str, payload: Optional[dict], headers: Dict[str, str],
                connect_timeout: float, read_timeout: float,
                method: str = 'POST') -> Iterator[str]:
    """发起请求并逐行产出响应体（流式）"""
    body = None
    head = {'Accept': 'application/json', 'Connection': 'close'}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        head['Content-Type'] = 'application/json'
    head.update(headers or {})

    conn, path = _connect(url, connect_timeout, read_timeout)
    try:
        conn.request(method, path, body=body, headers=head)
        try:
            response = conn.getresponse()
        except socket.timeout as exc:
            raise LLMTimeoutError(
                f'等待 {url} 响应超时（{read_timeout:.0f} 秒）', url=url) from exc

        if response.status >= 400:
            raw = response.read()
            text = raw.decode('utf-8', 'replace')
            raise LLMStatusError(
                _explain_status(response.status, text),
                status=response.status, body=text, url=url,
            )

        while True:
            try:
                line = response.readline()
            except socket.timeout as exc:
                raise LLMTimeoutError(
                    f'读取 {url} 超时（{read_timeout:.0f} 秒内没有新数据）',
                    url=url) from exc
            if not line:
                break
            yield line.decode('utf-8', 'replace').rstrip('\r\n')
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _explain_status(status: int, text: str) -> str:
    body = (text or '').strip()
    if len(body) > 300:
        body = body[:300] + '…'
    if status == 404:
        return f'模型或接口不存在（404）：{body}'
    if status == 401:
        return f'认证失败（401），请检查 API Key：{body}'
    if status == 403:
        return f'没有访问权限（403）：{body}'
    if status == 429:
        return f'请求过于频繁（429）：{body}'
    return f'服务返回 {status}：{body}'


# ======================================================================
# Ollama 客户端（原生 /api/chat）
# ======================================================================
class OllamaClient:
    """Ollama 原生接口的最小实现，属性结构与 ollama SDK 对齐"""

    def __init__(self, host: str = 'http://127.0.0.1:11434',
                 connect_timeout: float = 5.0,
                 read_timeout: float = 600.0,
                 keep_alive: Any = None):
        self.host = (host or 'http://127.0.0.1:11434').rstrip('/')
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.keep_alive = keep_alive

    # ---------- 基础请求 ----------

    def _json(self, method: str, path: str, payload: Optional[dict] = None,
              timeout: Optional[float] = None) -> dict:
        url = self.host + path
        read_timeout = timeout or self.read_timeout
        chunks: List[str] = []
        for line in _iter_lines(url, payload, {}, self.connect_timeout,
                                read_timeout, method=method):
            chunks.append(line)
        text = '\n'.join(chunks).strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # 极少数版本会一次性返回完整 JSON
            return {}

    # ---------- 模型管理 ----------

    def tags(self, timeout: float = 5.0) -> List[dict]:
        """已安装的模型列表"""
        data = self._json('GET', '/api/tags', timeout=timeout)
        return data.get('models') or []

    def version(self, timeout: float = 5.0) -> str:
        data = self._json('GET', '/api/version', timeout=timeout)
        return str(data.get('version') or '')

    def running(self, timeout: float = 5.0) -> List[dict]:
        """已加载进显存的模型"""
        data = self._json('GET', '/api/ps', timeout=timeout)
        return data.get('models') or []

    def show(self, model: str, timeout: float = 30.0) -> dict:
        return self._json('POST', '/api/show', {'model': model}, timeout=timeout)

    # ---------- 对话 ----------

    def chat(self, model: str, messages: List[Dict[str, str]],
             stream: bool = True, options: Optional[dict] = None,
             think: Optional[bool] = None, keep_alive: Any = None,
             **kwargs) -> Iterator[SimpleNamespace]:
        payload: Dict[str, Any] = {
            'model': model,
            'messages': messages,
            'stream': bool(stream),
        }
        if options:
            payload['options'] = options
        if think is not None:
            payload['think'] = bool(think)

        alive = keep_alive if keep_alive is not None else self.keep_alive
        if alive is not None:
            payload['keep_alive'] = alive

        url = self.host + '/api/chat'
        if not stream:
            data = self._json('POST', '/api/chat', payload)
            yield self._to_chunk(data)
            return

        for line in _iter_lines(url, payload, {}, self.connect_timeout,
                                self.read_timeout):
            if not line.strip():
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue
            if data.get('error'):
                raise LLMStatusError(str(data['error']), body=str(data['error']),
                                     url=url)
            yield self._to_chunk(data)
            if data.get('done'):
                return

    @staticmethod
    def _to_chunk(data: dict) -> SimpleNamespace:
        message = data.get('message') or {}
        return SimpleNamespace(
            message=SimpleNamespace(
                content=message.get('content') or '',
                thinking=message.get('thinking') or '',
                role=message.get('role') or 'assistant',
            ),
            done=bool(data.get('done')),
            eval_count=int(data.get('eval_count') or 0),
            prompt_eval_count=int(data.get('prompt_eval_count') or 0),
            total_duration=int(data.get('total_duration') or 0),
        )


# ======================================================================
# OpenAI 兼容客户端（/v1/chat/completions）
# ======================================================================
class _Completions:
    def __init__(self, owner: 'OpenAICompatClient'):
        self._owner = owner

    def create(self, model: str, messages: List[Dict[str, str]],
               stream: bool = True, temperature: float = 0.7,
               top_p: float = 0.9, max_tokens: Optional[int] = None,
               stop: Optional[List[str]] = None,
               extra_body: Optional[dict] = None,
               **kwargs) -> Iterator[SimpleNamespace]:
        return self._owner._chat(
            model=model, messages=messages, stream=stream,
            temperature=temperature, top_p=top_p, max_tokens=max_tokens,
            stop=stop, extra_body=extra_body, **kwargs,
        )


class OpenAICompatClient:
    """OpenAI 兼容接口（DeepSeek / Moonshot / 智谱 / llama-server / LM Studio）"""

    def __init__(self, base_url: str = 'http://127.0.0.1:1234/v1',
                 api_key: str = '', connect_timeout: float = 5.0,
                 read_timeout: float = 600.0):
        self.base_url = (base_url or 'http://127.0.0.1:1234/v1').rstrip('/')
        self.api_key = api_key or ''
        self.connect_timeout = connect_timeout
        self.read_timeout = read_timeout
        self.chat = SimpleNamespace(completions=_Completions(self))

    # ---------- 模型列表 ----------

    def models(self, timeout: float = 5.0) -> List[dict]:
        url = self.base_url + '/models'
        lines = list(_iter_lines(url, None, self._headers(), self.connect_timeout,
                                 timeout, method='GET'))
        text = '\n'.join(lines).strip()
        if not text:
            return []
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return []
        return data.get('data') or []

    def _headers(self) -> Dict[str, str]:
        headers = {}
        if self.api_key:
            headers['Authorization'] = f'Bearer {self.api_key}'
        return headers

    # ---------- 对话 ----------

    def _chat(self, model: str, messages: List[Dict[str, str]],
              stream: bool = True, temperature: float = 0.7,
              top_p: float = 0.9, max_tokens: Optional[int] = None,
              stop: Optional[List[str]] = None,
              extra_body: Optional[dict] = None,
              **kwargs) -> Iterator[SimpleNamespace]:
        payload: Dict[str, Any] = {
            'model': model,
            'messages': messages,
            'temperature': temperature,
            'top_p': top_p,
            'stream': bool(stream),
        }
        if max_tokens:
            payload['max_tokens'] = int(max_tokens)
        if stop:
            payload['stop'] = stop
        if extra_body:
            payload.update(extra_body)
        # 允许透传其它 OpenAI 参数，但别把内部字段带出去
        for key, value in kwargs.items():
            if key in ('think', 'options', 'keep_alive'):
                continue
            payload[key] = value

        url = self.base_url + '/chat/completions'
        headers = self._headers()

        if not stream:
            text = '\n'.join(_iter_lines(url, payload, headers,
                                         self.connect_timeout, self.read_timeout))
            data = json.loads(text or '{}')
            yield self._full_chunk(data)
            return

        for line in _iter_lines(url, payload, headers,
                                self.connect_timeout, self.read_timeout):
            if not line:
                continue
            if line.startswith(':'):
                continue                      # SSE 心跳
            if not line.startswith('data:'):
                continue
            data_text = line[5:].strip()
            if not data_text or data_text == '[DONE]':
                return
            try:
                data = json.loads(data_text)
            except json.JSONDecodeError:
                continue
            if data.get('error'):
                message = data['error']
                if isinstance(message, dict):
                    message = message.get('message') or json.dumps(message, ensure_ascii=False)
                raise LLMStatusError(str(message), body=str(message), url=url)
            yield self._delta_chunk(data)

    @staticmethod
    def _as_usage(raw):
        """usage 要暴露成属性（运行时用 getattr(chunk.usage, 'completion_tokens')）"""
        if isinstance(raw, dict):
            return SimpleNamespace(**raw)
        return raw

    @classmethod
    def _delta_chunk(cls, data: dict) -> SimpleNamespace:
        choices = data.get('choices') or []
        content = ''
        if choices:
            delta = choices[0].get('delta') or {}
            content = delta.get('content') or ''
        return SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content=content, role='assistant'),
                finish_reason=(choices[0].get('finish_reason') if choices else None),
            )],
            usage=cls._as_usage(data.get('usage')),
        )

    @classmethod
    def _full_chunk(cls, data: dict) -> SimpleNamespace:
        choices = data.get('choices') or []
        content = ''
        if choices:
            content = (choices[0].get('message') or {}).get('content') or ''
        return SimpleNamespace(
            choices=[SimpleNamespace(
                delta=SimpleNamespace(content=content, role='assistant'),
                finish_reason=(choices[0].get('finish_reason') if choices else None),
            )],
            usage=cls._as_usage(data.get('usage')),
        )


# ======================================================================
# 工厂
# ======================================================================
def make_client(provider: str, api_url: str = '', api_key: str = '',
                connect_timeout: float = 5.0, read_timeout: float = 600.0,
                keep_alive: Any = None):
    """按 provider 造客户端

    provider:
        'ollama'         -> Ollama 原生接口
        'local'          -> 内置 llama.cpp（OpenAI 兼容）
        其它              -> OpenAI 兼容接口
    """
    provider = (provider or 'ollama').lower()
    if provider == 'ollama':
        return OllamaClient(api_url, connect_timeout, read_timeout, keep_alive)
    return OpenAICompatClient(api_url, api_key, connect_timeout, read_timeout)


def is_local_url(url: str) -> bool:
    try:
        _, host, _, _ = _split_url(url)
    except Exception:
        return False
    return host in LOCAL_HOSTS
