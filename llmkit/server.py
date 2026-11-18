"""
内置 llama.cpp 后端 —— 默认路径，零安装

用户说「不想装 Ollama，直接用 llama」：项目里已经带着 llama.cpp 的动态库，
所以这里优先在**进程内**用 ctypes 直接加载 .gguf，再挂一个只监听 127.0.0.1
的极简 OpenAI 兼容 HTTP 服务（标准库 http.server）。

对外仍然是 `http://127.0.0.1:11500/v1`：
    /health              -> 200
    /v1/models           -> OpenAI 风格模型列表
    /v1/chat/completions -> SSE 流式 / 完整 JSON

这样一来，上层 connector / http_client / LLMProcessor 完全不用区分是
「进程内 llama.cpp」还是「外部 llama-server.exe」，流式解析、超时分层、
冷启动重试、预热这些已经验证过的逻辑全部复用。

只有在动态库不可用（或加载失败）时，才回落到外部 llama-server.exe，
此时会给出明确的放置路径提示，不做静默降级。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from . import native as llm_native

DEFAULT_PORT = 11500
EXE_NAME = 'llama-server.exe' if os.name == 'nt' else 'llama-server'

DOWNLOAD_HINT = ('https://github.com/ggml-org/llama.cpp/releases '
                 '（下载 llama-*-bin-win-*-x64.zip，解压后把 llama-server.exe '
                 '放到程序目录的 runtime/llama/ 下）')

_CREATE_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0


def _app_root() -> Path:
    from .registry import app_root
    return app_root()


def find_server_exe(extra: str = '') -> str:
    """找外部 llama-server 可执行文件（仅在进程内引擎不可用时才需要）"""
    if extra and Path(extra).is_file():
        return str(Path(extra))

    root = _app_root()
    candidates = [
        root / 'runtime' / 'llama' / EXE_NAME,
        root / 'tools' / 'llama' / EXE_NAME,
        root / 'llama' / EXE_NAME,
        root / 'core' / 'server' / 'engines' / 'llama' / 'bin' / EXE_NAME,
    ]
    for path in candidates:
        try:
            if path.is_file():
                return str(path)
        except OSError:
            continue

    found = shutil.which('llama-server')
    return found or ''


def base_url(port: int = DEFAULT_PORT) -> str:
    return f'http://127.0.0.1:{port}/v1'


def is_serving(port: int = DEFAULT_PORT, timeout: float = 1.5) -> bool:
    """健康检查（进程内服务 / 外部 llama-server 都用这个端口）"""
    url = f'http://127.0.0.1:{port}/health'
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def loaded_model(port: int = DEFAULT_PORT, timeout: float = 2.0) -> str:
    """当前加载的模型文件名（通过 /v1/models 拿 id）"""
    url = f'http://127.0.0.1:{port}/v1/models'
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(url, timeout=timeout) as response:
            data = json.loads(response.read().decode('utf-8', 'replace'))
        items = data.get('data') or []
        if items:
            return str(items[0].get('id') or '')
    except Exception:
        pass
    return ''


# ======================================================================
# 极简 OpenAI 兼容服务：把 native 推理挂到 HTTP 上
# ======================================================================
class _Runtime:
    """挂在内嵌 HTTPServer 上的共享状态"""

    def __init__(self) -> None:
        self.engine = llm_native.get_engine()
        self.model_id = 'local'


class _NativeHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.0'

    @property
    def runtime(self) -> _Runtime:
        return self.server.runtime  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args) -> None:
        return  # 静默，别把每个请求打到 stderr

    # ---------- 响应工具 ----------
    def _json(self, payload: Dict[str, Any], status: int = 200) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Connection', 'close')
        self.end_headers()
        try:
            self.wfile.write(raw)
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _sse(self, payload: Dict[str, Any]) -> bool:
        raw = b'data: ' + json.dumps(payload, ensure_ascii=False).encode('utf-8') + b'\n\n'
        try:
            self.wfile.write(raw)
            self.wfile.flush()
            return True
        except (BrokenPipeError, ConnectionResetError, OSError):
            return False

    def _chunk(self, text: str = '', finish_reason=None,
               usage=None, model: str = '') -> Dict[str, Any]:
        return {
            'id': f'chatcmpl-{int(time.time() * 1000)}',
            'object': 'chat.completion.chunk',
            'created': int(time.time()),
            'model': model or self.runtime.model_id,
            'choices': [{
                'index': 0,
                'delta': {'content': text, 'role': 'assistant'} if text else {},
                'finish_reason': finish_reason,
            }],
            'usage': usage,
        }

    # ---------- GET ----------
    def do_GET(self) -> None:
        path = self.path.split('?', 1)[0].rstrip('/')
        if path in ('/health', '/v1/health'):
            self._json({'status': 'ok', 'engine': 'native-llama.cpp'})
            return
        if path in ('/v1/models', '/models'):
            engine = self.runtime.engine
            self._json({
                'object': 'list',
                'data': [{
                    'id': self.runtime.model_id,
                    'object': 'model',
                    'owned_by': 'llama.cpp',
                    'loaded': bool(engine.loaded),
                }] if engine.loaded else [],
            })
            return
        self._json({'error': {'message': f'未知接口 {path}'}}, 404)

    # ---------- POST ----------
    def do_POST(self) -> None:
        path = self.path.split('?', 1)[0].rstrip('/')
        if path not in ('/v1/chat/completions', '/chat/completions'):
            self._json({'error': {'message': f'未知接口 {path}'}}, 404)
            return

        try:
            length = int(self.headers.get('Content-Length') or 0)
            payload = json.loads(self.rfile.read(length).decode('utf-8', 'replace')) if length else {}
        except Exception as exc:
            self._json({'error': {'message': f'请求体解析失败：{exc}'}}, 400)
            return

        engine = self.runtime.engine
        if not engine.loaded:
            self._json({'error': {'message': '模型还没有加载'}}, 503)
            return

        messages = payload.get('messages') or []
        if isinstance(messages, dict):
            messages = [messages]
        stream = bool(payload.get('stream', True))
        max_tokens = int(payload.get('max_tokens') or 512)
        temperature = float(payload.get('temperature') if payload.get('temperature') is not None else 0.2)
        top_p = float(payload.get('top_p') if payload.get('top_p') is not None else 0.9)
        stop = payload.get('stop') or []
        if isinstance(stop, str):
            stop = [stop]

        gen = engine.generate(
            messages, max_tokens=max_tokens, temperature=temperature,
            top_p=top_p, stop=stop,
        )

        if not stream:
            text, tokens, error = '', 0, ''
            for item in gen:
                if item.get('done'):
                    text = str(item.get('text_full') or text)
                    tokens = int(item.get('tokens') or 0)
                    error = str(item.get('error') or '')
                    break
                text += str(item.get('text') or '')
            if error:
                self._json({'error': {'message': error}}, 500)
                return
            self._json({
                'id': f'chatcmpl-{int(time.time() * 1000)}',
                'object': 'chat.completion',
                'created': int(time.time()),
                'model': self.runtime.model_id,
                'choices': [{
                    'index': 0,
                    'message': {'role': 'assistant', 'content': text},
                    'finish_reason': 'stop',
                }],
                'usage': {'prompt_tokens': 0, 'completion_tokens': tokens,
                          'total_tokens': tokens},
            })
            return

        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.end_headers()

        tokens, error = 0, ''
        for item in gen:
            if item.get('done'):
                tokens = int(item.get('tokens') or 0)
                error = str(item.get('error') or '')
                break
            piece = str(item.get('text') or '')
            if piece and not self._sse(self._chunk(piece)):
                return          # 客户端断开，直接收工

        if error:
            self._sse({'error': {'message': error}})
        else:
            self._sse(self._chunk(
                finish_reason='stop',
                usage={'prompt_tokens': 0, 'completion_tokens': tokens,
                       'total_tokens': tokens}))
        try:
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass


class _LocalHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, runtime: _Runtime):
        self.runtime = runtime
        super().__init__(address, _NativeHandler)


# ======================================================================
# 受管的后端：进程内优先，外部 exe 兜底
# ======================================================================
class LlamaServer:
    """一个受管的 llama.cpp 后端（内嵌服务 或 外部子进程）"""

    def __init__(self, exe: str = '', port: int = DEFAULT_PORT):
        self.exe = exe
        self.port = port
        self.gpu_layers = 0
        self.n_threads = 0
        self.engine_mode = 'auto'       # auto | native | server
        self._process: Optional[subprocess.Popen] = None
        self._http: Optional[_LocalHTTPServer] = None
        self._http_thread: Optional[threading.Thread] = None
        self._model: str = ''
        self._engine_info = ''

    # ---------- 状态 ----------
    @property
    def running(self) -> bool:
        if self._http is not None:
            return True
        return self._process is not None and self._process.poll() is None

    @property
    def model(self) -> str:
        return self._model

    @property
    def engine(self) -> str:
        """当前实际在用的引擎，给界面显示"""
        if self._http is not None:
            return '进程内 llama.cpp（零安装）'
        if self._process is not None:
            return 'llama-server.exe'
        if is_serving(self.port):
            return f'外部服务（端口 {self.port}）'
        return self._engine_info or '未启动'

    def status(self) -> Tuple[bool, str]:
        if self._http is not None:
            return True, f'进程内 llama.cpp 运行中（{self._engine_info}）'
        if self.running:
            return True, f'llama-server 运行中（端口 {self.port}）'
        if is_serving(self.port):
            return True, f'检测到已在运行的 llama-server（端口 {self.port}）'
        ok, message = llm_native.available()
        return False, ('llama.cpp 未启动' if ok else f'内置 llama.cpp 不可用：{message}')

    # ---------- 启停 ----------
    def ensure(self, model_path: str, context: int = 4096,
               gpu_layers: int = 0, n_threads: int = 0,
               wait_seconds: float = 180.0) -> Tuple[bool, str]:
        """确保后端在用指定模型跑起来"""
        if not model_path:
            return False, '没有选择模型文件，请在「大模型」页导入 .gguf'
        path = Path(model_path)
        if not path.is_file():
            # 最后一道兜底：旧配置里的绝对路径在换电脑 / 换安装目录后失效。
            # GUI 和客户端已各自重定位过，这里防的是直接调 ensure() 的路径
            # （例如预热、命令行自检），不能让模型加载直接撞在旧路径上。
            from .registry import resolve_model_path
            resolved, _note = resolve_model_path(str(model_path))
            if not resolved:
                return False, (f'模型文件不存在：{model_path}\n'
                               '这份配置可能来自另一台电脑或另一个安装目录；'
                               '请在「大模型」页点「选择模型文件」重新选一次。')
            path = Path(resolved)

        self.gpu_layers = int(gpu_layers)
        self.n_threads = int(n_threads)

        # 已经加载同一个模型就直接复用
        if self.running and self._model == str(path):
            return True, f'已加载 {path.name}'
        if not self.running and is_serving(self.port):
            current = loaded_model(self.port)
            if current and current == path.name:
                self._model = str(path)
                self._engine_info = path.name
                return True, f'复用已运行的 {path.name}'

        # 安装包里没有 llama-server.exe。老配置若把 engine_mode 写成 server，
        # 会跳过进程内 native 引擎、直接去找外部 exe，在别的电脑上必然失败
        # （真机日志：「本地模型预热跳过：也没找到 llama-server.exe」）。
        # native 可用就自动回落并把这个决定写进返回消息，日志里能查到，
        # 不静默降级，也不改用户配置文件。
        fallback_note = ''
        if self.engine_mode == 'server' and not find_server_exe(self.exe):
            native_ok, _native_msg = llm_native.available()
            if native_ok:
                self.engine_mode = 'auto'
                fallback_note = ('配置指定外部 llama-server.exe 但未找到，'
                                 '已自动改用程序自带的进程内引擎')

        native_error = ''
        if self.engine_mode != 'server':
            ok, message = self._start_native(path, context)
            if ok:
                return True, (f'{message}（{fallback_note}）'
                              if fallback_note else message)
            native_error = message

        exe = find_server_exe(self.exe)
        if not exe:
            detail = f'进程内引擎：{native_error}；' if native_error else ''
            return False, (f'{detail}也没找到 {EXE_NAME}。'
                           f'程序自带 llama.cpp 动态库，正常情况下不需要额外安装；'
                           f'若确实缺失，可下载后放到 runtime/llama/：{DOWNLOAD_HINT}')
        self.exe = exe
        return self._start_subprocess(path, context, exe, wait_seconds)

    def _start_native(self, path: Path, context: int) -> Tuple[bool, str]:
        ok, message = llm_native.available()
        if not ok:
            return False, message
        if is_serving(self.port):
            # 端口被别的实例占了，别抢，直接复用
            return False, f'端口 {self.port} 已被占用'

        self._stop_subprocess()
        engine = llm_native.get_engine()
        loaded, why = engine.load(
            str(path), n_ctx=int(context or 4096),
            n_gpu_layers=self.gpu_layers, n_threads=self.n_threads)
        if not loaded:
            return False, why

        runtime = _Runtime()
        runtime.model_id = path.name
        try:
            http = _LocalHTTPServer(('127.0.0.1', self.port), runtime)
        except OSError as exc:
            engine.unload()
            return False, f'启动本地服务失败（端口 {self.port}）：{exc}'

        thread = threading.Thread(
            target=http.serve_forever, daemon=True,
            name='qifei-llama-http')
        thread.start()
        self._http = http
        self._http_thread = thread
        self._model = str(path)
        self._engine_info = f'{path.name} · ctx {engine.n_ctx}'

        deadline = time.time() + 15
        while time.time() < deadline:
            if is_serving(self.port, timeout=1.0):
                return True, f'已加载 {path.name}（进程内 llama.cpp，无需 Ollama）'
            time.sleep(0.2)
        return True, f'已加载 {path.name}（进程内 llama.cpp）'

    def _start_subprocess(self, path: Path, context: int, exe: str,
                          wait_seconds: float) -> Tuple[bool, str]:
        self._stop_native()
        self._stop_subprocess()
        command = [
            exe, '-m', str(path),
            '--host', '127.0.0.1',
            '--port', str(self.port),
            '-c', str(max(512, int(context or 4096))),
            '--no-webui',
        ]
        if self.gpu_layers:
            command += ['-ngl', str(self.gpu_layers)]
        try:
            self._process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                stdin=subprocess.DEVNULL, creationflags=_CREATE_FLAGS,
                cwd=str(path.parent), close_fds=True,
            )
        except Exception as exc:
            return False, f'启动 llama-server 失败：{exc}'
        self._model = str(path)
        self._engine_info = path.name

        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if self._process.poll() is not None:
                self._process = None
                return False, 'llama-server 启动后立即退出，可能显存不足或模型损坏'
            if is_serving(self.port):
                return True, f'已加载 {path.name}（llama-server.exe）'
            time.sleep(0.8)
        self._stop_subprocess()
        return False, f'llama-server {wait_seconds:.0f} 秒内没准备好，模型可能太大'

    def _stop_native(self) -> None:
        if self._http is not None:
            try:
                self._http.shutdown()
                self._http.server_close()
            except Exception:
                pass
            self._http = None
        self._http_thread = None
        try:
            llm_native.shutdown()
        except Exception:
            pass

    def _stop_subprocess(self) -> None:
        if self._process is None:
            return
        try:
            self._process.terminate()
            self._process.wait(timeout=8)
        except Exception:
            try:
                self._process.kill()
            except Exception:
                pass
        finally:
            self._process = None

    def stop(self) -> None:
        self._stop_native()
        self._stop_subprocess()
        self._model = ''
        self._engine_info = ''

    def warmup(self, timeout: float = 120.0) -> Tuple[bool, str]:
        """发一个极小请求，把权重真正推进内存 / 显存"""
        if not is_serving(self.port):
            return False, 'llama.cpp 未运行'
        payload = json.dumps({
            'model': loaded_model(self.port) or 'local',
            'messages': [{'role': 'user', 'content': 'hi'}],
            'max_tokens': 1,
            'stream': False,
        }).encode('utf-8')
        request = urllib.request.Request(
            f'http://127.0.0.1:{self.port}/v1/chat/completions',
            data=payload, headers={'Content-Type': 'application/json'},
        )
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(request, timeout=timeout) as response:
                response.read()
            return True, '预热完成'
        except Exception as exc:
            return False, f'预热失败：{exc}'


# 进程内单例：客户端生命周期里只维护一个后端
_server: Optional[LlamaServer] = None


def get_server(exe: str = '', port: int = DEFAULT_PORT) -> LlamaServer:
    global _server
    if _server is None:
        _server = LlamaServer(exe, port)
    else:
        if exe:
            _server.exe = exe
        _server.port = port
    return _server


def configure(gpu_layers: int = 0, n_threads: int = 0,
              engine_mode: str = 'auto') -> None:
    """把界面里的高级设置传给后端单例"""
    server = get_server()
    server.gpu_layers = int(gpu_layers or 0)
    server.n_threads = int(n_threads or 0)
    server.engine_mode = engine_mode or 'auto'


def shutdown() -> None:
    global _server
    if _server is not None:
        _server.stop()
        _server = None
    llm_native.shutdown()
