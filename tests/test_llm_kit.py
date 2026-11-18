# coding: utf-8
"""
llmkit 自检

不依赖 Ollama、不联网：用本地起的假服务模拟 Ollama 与 OpenAI 兼容接口，
验证「本地 gguf 导入 / 流式解析 / 模型缺失识别 / 超时行为 / 运行时对话」这些关键路径。

用法：
    python tests/test_llm_kit.py
"""

from __future__ import annotations

import ctypes
import json
import os
import struct
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import llmkit
from llmkit.http_client import (LLMStatusError, LLMTimeoutError, OllamaClient,
                                OpenAICompatClient)


# ======================================================================
# 假服务
# ======================================================================
class FakeHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    slow_seconds = 3.0
    # 已注册模型：导入用例会往里加名字，用来验证「导入后能被识别」
    installed = ['demo:latest', 'gemma3:4b']

    def log_message(self, *args):
        pass

    def _send_json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_stream(self, lines, content_type):
        self.send_response(200)
        self.send_header('Content-Type', content_type)
        self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()
        try:
            for line in lines:
                data = (line + '\n').encode('utf-8')
                self.wfile.write(f'{len(data):X}\r\n'.encode() + data + b'\r\n')
                self.wfile.flush()
            self.wfile.write(b'0\r\n\r\n')
        except OSError:
            # 客户端主动断开（超时用例会这样），属于预期
            pass

    def do_GET(self):
        if self.path == '/api/version':
            self._send_json({'version': '0.6.1-fake'})
        elif self.path == '/api/tags':
            self._send_json({'models': [
                {'name': name, 'size': 3 * 1024 ** 3}
                for name in self.installed
            ]})
        elif self.path == '/v1/models':
            self._send_json({'data': [{'id': 'local-demo'}]})
        else:
            self._send_json({'error': 'not found'}, 404)

    def do_POST(self):
        length = int(self.headers.get('Content-Length') or 0)
        raw = self.rfile.read(length) if length else b'{}'
        payload = json.loads(raw or b'{}')

        if self.path == '/api/chat':
            model = payload.get('model')
            if model == 'slow':
                time.sleep(self.slow_seconds)      # 模拟模型加载卡住
            elif model not in self.installed:
                self._send_json({'error': f"model '{model}' not found, try pulling it first"}, 404)
                return
            if not payload.get('stream', True):
                # 预热用的是非流式请求，回一个完整 JSON 对象
                self._send_json({'message': {'role': 'assistant', 'content': '好'},
                                 'done': True, 'eval_count': 1})
                return
            lines = [
                json.dumps({'message': {'role': 'assistant', 'content': '你'},
                            'done': False}, ensure_ascii=False),
                json.dumps({'message': {'role': 'assistant', 'content': '好'},
                            'done': False}, ensure_ascii=False),
                json.dumps({'message': {'role': 'assistant', 'content': ''},
                            'done': True, 'eval_count': 2}, ensure_ascii=False),
            ]
            self._send_stream(lines, 'application/x-ndjson')
            return

        if self.path == '/v1/chat/completions':
            if payload.get('model') == 'missing':
                self._send_json({'error': {'message': 'model not found'}}, 404)
                return
            lines = [
                'data: ' + json.dumps({'choices': [{'delta': {'content': '你'}}]},
                                      ensure_ascii=False),
                'data: ' + json.dumps({'choices': [{'delta': {'content': '好'}}]},
                                      ensure_ascii=False),
                'data: ' + json.dumps({'choices': [{'delta': {}, 'finish_reason': 'stop'}],
                                       'usage': {'completion_tokens': 2}}),
                'data: [DONE]',
            ]
            self._send_stream(lines, 'text/event-stream')
            return

        self._send_json({'error': 'not found'}, 404)


def start_server() -> tuple[HTTPServer, str]:
    server = HTTPServer(('127.0.0.1', 0), FakeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f'http://{host}:{port}'


# ======================================================================
# GGUF 造一个最小文件
# ======================================================================
def _gguf_string(text: str) -> bytes:
    data = text.encode('utf-8')
    return struct.pack('<Q', len(data)) + data


def make_gguf(path: Path, chat_template: bool = True) -> None:
    kv = [
        ('general.architecture', 8, _gguf_string('llama')),
        ('general.name', 8, _gguf_string('Test Chat Model')),
        ('general.file_type', 4, struct.pack('<I', 15)),        # Q4_K_M
        ('llama.context_length', 4, struct.pack('<I', 8192)),
    ]
    if chat_template:
        kv.append(('tokenizer.chat_template', 8,
                   _gguf_string("{% for m in messages %}{{ m['content'] }}{% endfor %}")))
    body = b'GGUF' + struct.pack('<I', 3)
    body += struct.pack('<Q', 2)          # tensor_count
    body += struct.pack('<Q', len(kv))
    for key, vtype, raw in kv:
        body += _gguf_string(key) + struct.pack('<I', vtype) + raw
    for name in ('blk.0.attn_q.weight', 'blk.1.attn_q.weight'):
        body += _gguf_string(name)
        body += struct.pack('<I', 2)
        body += struct.pack('<Q', 4096) + struct.pack('<Q', 4096)
        body += struct.pack('<I', 12)     # Q4_K
        body += struct.pack('<Q', 0)
    path.write_bytes(body)


# ======================================================================
# 用例
# ======================================================================
def test_gguf(tmp: Path) -> None:
    path = tmp / 'demo.Q4_K_M.gguf'
    make_gguf(path)
    info = llmkit.read_info(path)
    assert not info.error, info.error
    assert info.arch == 'llama', info.arch
    assert info.name == 'Test Chat Model', info.name
    assert info.quant == 'Q4_K_M', info.quant
    assert info.context == 8192, info.context
    assert info.params == 4096 * 4096 * 2, info.params
    print(f'  GGUF 解析 OK：{info.summary()} / ctx {info.context}')


def test_gguf_chat_template() -> None:
    """没有 chat template 的模型（ASR / 补全专用）必须被标成非对话模型"""
    from llmkit import registry

    with tempfile.TemporaryDirectory() as folder:
        tmp = Path(folder)
        instruct = tmp / 'demo-instruct.gguf'
        asr = tmp / 'demo-asr.gguf'
        make_gguf(instruct, chat_template=True)
        make_gguf(asr, chat_template=False)

        assert llmkit.read_info(instruct).has_chat_template
        assert not llmkit.read_info(asr).has_chat_template

        models = {Path(m.path).name: m for m in registry.scan_gguf([tmp])}
        assert models['demo-instruct.gguf'].chat_ready
        assert '非对话模型' not in models['demo-instruct.gguf'].display
        assert not models['demo-asr.gguf'].chat_ready
        assert '非对话模型' in models['demo-asr.gguf'].display
        assert 'chat template' in models['demo-asr.gguf'].warning
    print('  chat template 识别 OK：ASR/补全模型被标为「非对话模型」')


def test_model_download_idempotent(tmp: Path) -> None:
    """模型已就绪时，「下载推荐润色模型」按钮不能重复联网下载"""
    from llmkit import model_download as md

    fake = tmp / md.MODEL
    fake.write_bytes(b'x')
    saved = (md.target, md.validate, md._download_curl, md._download_urllib)

    def boom(*_args, **_kwargs):
        raise AssertionError('模型已就绪时不应该联网下载')

    md.target = lambda: fake
    md.validate = lambda _p: (True, '假校验通过')
    md._download_curl = boom
    md._download_urllib = boom
    try:
        ok, message = md.download(on_stage=lambda _t: None)
    finally:
        (md.target, md.validate,
         md._download_curl, md._download_urllib) = saved
    assert ok and '已就绪' in message, message
    print('  模型下载幂等 OK：文件已就绪时不联网')


def test_ollama_stream(base: str) -> None:
    client = OllamaClient(base, connect_timeout=2, read_timeout=10)
    assert client.version() == '0.6.1-fake'
    assert len(client.tags()) == 2

    text, done = '', False
    for chunk in client.chat(model='demo:latest',
                             messages=[{'role': 'user', 'content': 'hi'}]):
        text += chunk.message.content
        done = done or chunk.done
    assert text == '你好', text
    assert done
    print('  Ollama 流式解析 OK：收到「你好」')


def test_missing_model(base: str) -> None:
    client = OllamaClient(base, connect_timeout=2, read_timeout=10)
    try:
        list(client.chat(model='不存在的模型',
                         messages=[{'role': 'user', 'content': 'hi'}]))
    except LLMStatusError as exc:
        assert exc.model_missing, exc.body
        print(f'  模型缺失识别 OK：{exc}')
        return
    raise AssertionError('应该抛出 LLMStatusError')


def test_openai_stream(base: str) -> None:
    client = OpenAICompatClient(base + '/v1', 'k', 2, 10)
    text = ''
    for chunk in client.chat.completions.create(
            model='local-demo', messages=[{'role': 'user', 'content': 'hi'}]):
        if chunk.choices:
            text += chunk.choices[0].delta.content or ''
    assert text == '你好', text
    print('  OpenAI 兼容流式解析 OK：收到「你好」')


def test_timeout(base: str) -> None:
    client = OllamaClient(base, connect_timeout=2, read_timeout=1)
    started = time.time()
    try:
        list(client.chat(model='slow', messages=[{'role': 'user', 'content': 'hi'}]))
    except LLMTimeoutError as exc:
        elapsed = time.time() - started
        assert elapsed < 5, elapsed
        print(f'  读取超时 OK：{elapsed:.1f}s 后报错（{exc}）')
        return
    raise AssertionError('应该抛出 LLMTimeoutError')


def test_connect_error() -> None:
    client = OllamaClient('http://127.0.0.1:9', connect_timeout=1, read_timeout=1)
    started = time.time()
    try:
        client.version()
    except Exception as exc:
        elapsed = time.time() - started
        assert elapsed < 3, elapsed
        print(f'  连接失败 OK：{elapsed:.1f}s 内报错（{type(exc).__name__}）')
        return
    raise AssertionError('应该连接失败')


def test_sanitize() -> None:
    cases = {
        'Qwen2.5-7B-Instruct-Q4_K_M': 'qwen2.5-7b-instruct-q4_k_m',
        '7b-model': 'm-7b-model',
        '中文模型': 'local-model',
    }
    for raw, expect in cases.items():
        got = llmkit.sanitize_name(raw)
        assert got == expect, f'{raw} -> {got}，期望 {expect}'
    print('  模型名清洗 OK')


# ======================================================================
# 用户主诉：自己下载的 gguf 导入后要能被识别、能对话、不联网
# ======================================================================
def test_import_gguf(base: str, tmp: Path) -> None:
    import subprocess
    from llmkit import connector, registry

    gguf = tmp / 'My-Model.Q4_K_M.gguf'
    make_gguf(gguf)
    calls = []
    modelfiles = []

    original_run = subprocess.run
    original_find = registry.find_ollama_exe

    def fake_run(args, **kwargs):
        """替身 ollama.exe：只记录 create 命令和 Modelfile，不碰网络"""
        calls.append(list(args))
        modelfiles.append(Path(args[4]).read_text(encoding='utf-8'))
        FakeHandler.installed.append(args[2])
        return subprocess.CompletedProcess(args, 0, '', '')

    registry.find_ollama_exe = lambda extra='': 'fake-ollama.exe'
    subprocess.run = fake_run
    try:
        ok, name = llmkit.import_gguf(str(gguf), host=base)
    finally:
        subprocess.run = original_run
        registry.find_ollama_exe = original_find

    assert ok, name
    assert len(calls) == 1, calls
    assert calls[0][1] == 'create', calls[0]
    assert not any('pull' in str(a) for a in calls[0]), calls[0]
    assert modelfiles[0].splitlines()[0] == f'FROM "{gguf.as_posix()}"', modelfiles[0]
    assert 'PARAMETER num_ctx' in modelfiles[0]

    # 注册后必须能查到 —— 这就是「导入后不识别」的回归点
    installed = [m.name for m in llmkit.list_installed(base)]
    assert name in installed, installed

    # 并且能真的拿它对话
    backend = connector.from_settings({
        'backend': 'ollama', 'api_url': base, 'model': name,
        'connect_timeout': 2, 'read_timeout': 10,
    })
    result = connector.chat_test(backend)
    assert result['ok'], result
    assert result['text'] == '你好', result
    print(f'  本地 gguf 导入 OK：{name} 已注册且可对话（全程无 pull）')


def test_connector_settings(base: str) -> None:
    """连接超时和生成超时必须分开；探测/预热要能构造出正确客户端"""
    from types import SimpleNamespace
    from llmkit import connector

    settings = {
        'backend': 'ollama', 'api_url': base, 'model': 'demo:latest',
        'connect_timeout': 3, 'read_timeout': 77, 'keep_alive': -1,
    }
    backend = connector.from_settings(settings)
    assert backend.connect_timeout == 3, backend
    assert backend.read_timeout == 77, backend

    role = SimpleNamespace(provider='ollama', backend='', timeout=77,
                           keep_alive=-1, api_url=base, model='demo:latest',
                           api_key='')
    runtime = connector.from_role(role, settings)
    assert runtime.connect_timeout == 3, runtime   # 旧代码这里会被读超时带成 77
    assert runtime.read_timeout == 77, runtime

    probe = connector.probe(connector.from_settings({
        'backend': 'ollama', 'api_url': base}))
    assert probe['ok'], probe
    assert 'demo:latest' in probe['models'], probe

    warm = connector.warmup(connector.from_settings(settings))
    assert warm[0], warm
    print('  超时分层 / 探测 / 预热 OK')


def test_runtime_timeouts(base: str) -> None:
    """界面里设的连接/生成超时要真的进到 HTTP 客户端"""
    from core.client.llm.llm_client_pool import ClientPool

    pool = ClientPool()
    first = pool.get_client('ollama', base, timeout=77, connect_timeout=3)
    assert (first.connect_timeout, first.read_timeout) == (3.0, 77.0), first.__dict__
    second = pool.get_client('ollama', base, timeout=77, connect_timeout=9)
    assert second is not first and second.connect_timeout == 9.0, second.__dict__
    print('  客户端池超时透传 OK')


def test_processor(base: str) -> None:
    """运行时 LLMProcessor 用界面设置完整跑一轮对话"""
    from core.client.llm.llm_client_pool import ClientPool
    from core.client.llm.llm_processor import LLMProcessor
    from core.client.llm.llm_role_config import RoleConfig

    processor = LLMProcessor(ClientPool())
    processor._settings = {
        'backend': 'ollama', 'api_url': base, 'model': 'demo:latest',
        'connect_timeout': 2, 'read_timeout': 10, 'keep_alive': -1,
    }
    processor._settings_loaded = True

    role = RoleConfig(name='测试', module_name='test', provider='ollama',
                      api_url=base, model='demo:latest', enabled=True)
    text, tokens, _ = processor.process(
        role, [{'role': 'user', 'content': 'hi'}])
    assert text == '你好', text
    assert tokens == 2, tokens
    print(f'  运行时对话 OK：{text!r} tokens={tokens}')


# ======================================================================
# v2.8：不装 Ollama，直接用项目自带的 llama.cpp
# ======================================================================
def test_native_library() -> None:
    """动态库能被 ctypes 真实加载，且需要的 C API 都在"""
    from llmkit import native

    ok, message = native.available()
    if not ok:
        print(f'  动态库不可用，跳过：{message}')
        return
    lib = native.load_lib()
    required = (
        'llama_backend_init', 'llama_model_load_from_file',
        'llama_model_chat_template', 'llama_chat_apply_template',
        'llama_init_from_model', 'llama_decode', 'llama_memory_clear',
        'llama_sampler_chain_init', 'llama_sampler_sample',
        'llama_vocab_is_eog', 'llama_token_to_piece',
    )
    for name in required:
        assert hasattr(lib, name), f'缺少 C API：{name}'
    assert lib.llama_chat_apply_template.argtypes is not None
    print(f'  进程内 llama.cpp 动态库 OK：{native.dll_dir()}')


def test_native_abi() -> None:
    """不加载真实模型，验证 ctypes 结构体布局与 DLL 一致。

    llama_model_params / llama_context_params / llama_batch 都是按值传递的 C
    结构体，布局一旦和 DLL 对不上，只有加载真实 .gguf 时才会崩，而且崩在 C
    里很难查。这里用 default_params / batch_init / chat_apply_template 几个
    不需要模型的调用把字段读回来做合理性检查，DLL 升级后能第一时间发现。
    """
    from llmkit import native

    ok, message = native.available()
    if not ok:
        print(f'  动态库不可用，跳过 ABI 校验：{message}')
        return
    lib = native.load_lib()

    # 真实模型能加载的前提：ggml 后端（CPU / Vulkan 等）必须真的注册上。
    # 只查符号存在是不够的 —— ggml_backend_load_all() 枚举的是 CWD 下的
    # ggml-*.dll，CWD 不对时会静默注册 0 个，直到加载 .gguf 才报
    # "no backends are loaded"。
    ggml = native._GGML
    assert ggml is not None
    reg_count = ggml.ggml_backend_reg_count
    reg_count.argtypes = []
    reg_count.restype = ctypes.c_size_t
    assert reg_count() >= 1, 'ggml 后端注册数为 0，真实模型一定加载不了'
    print(f'  ggml 后端已注册：{reg_count()} 个')

    mp = lib.llama_model_default_params()
    assert mp.n_gpu_layers == -1, mp.n_gpu_layers      # llama.cpp 默认全部卸载到 GPU
    assert bool(mp.use_mmap) is True
    assert bool(mp.vocab_only) is False

    cp = lib.llama_context_default_params()
    assert 0 < cp.n_ctx <= 8192, cp.n_ctx
    assert 0 < cp.n_batch <= 65536, cp.n_batch
    assert 0 < cp.n_ubatch <= 65536, cp.n_ubatch
    assert cp.n_seq_max >= 1, cp.n_seq_max
    assert cp.n_threads >= 1, cp.n_threads

    batch = lib.llama_batch_init(8, 0, 1)
    try:
        # llama_batch_init 只按容量分配内存，n_tokens 仍然是 0。
        # LlamaNative._decode() 必须显式填 batch.n_tokens，否则 llama_decode
        # 会报 "n_tokens == 0" 直接失败 —— 这个坑只有真实模型才暴露。
        assert batch.n_tokens == 0, batch.n_tokens
        for name in ('token', 'pos', 'n_seq_id', 'seq_id', 'logits'):
            assert bool(getattr(batch, name)), f'llama_batch.{name} 指针为空'
    finally:
        lib.llama_batch_free(batch)

    # llama_chat_message 结构与模板格式化：不需要模型，纯字符串运算
    tmpl = (b"{% for message in messages %}"
            b"{{ '<|im_start|>' + message['role'] + '\n' + message['content'] + '<|im_end|>\n' }}"
            b"{% endfor %}{% if add_generation_prompt %}"
            b"{{ '<|im_start|>assistant\n' }}{% endif %}")
    msgs = (native.llama_chat_message * 2)()
    msgs[0].role, msgs[0].content = b'system', b'you are helpful'
    msgs[1].role, msgs[1].content = b'user', b'hi'
    buf = ctypes.create_string_buffer(4096)
    written = lib.llama_chat_apply_template(tmpl, msgs, 2, True, buf, 4096)
    text = buf.raw[:max(written, 0)].decode('utf-8', 'replace')
    assert '<|im_start|>system\nyou are helpful<|im_end|>' in text, repr(text)
    assert '<|im_start|>user\nhi<|im_end|>' in text, repr(text)
    assert text.rstrip().endswith('<|im_start|>assistant'), repr(text)
    print(f'  llama.cpp ABI 校验 OK：model {ctypes.sizeof(native.llama_model_params)}B / '
          f'ctx {ctypes.sizeof(native.llama_context_params)}B / '
          f'batch {ctypes.sizeof(native.llama_batch)}B，chat template 正常')


def test_packaged_dll_lookup() -> None:
    """模拟 PyInstaller 布局：打包后（只有 sys._MEIPASS）也能定位动态库"""
    from llmkit import native

    names = native._WINDOWS_DLLS if os.name == 'nt' else native._POSIX_DLLS.get(
        sys.platform, native._POSIX_DLLS['other'])
    saved = (native.__file__, native._app_root,
             getattr(sys, '_MEIPASS', None),
             os.environ.get('QIFEI_LLAMA_DLL_DIR'))
    try:
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            bin_dir = root / 'core' / 'server' / 'engines' / 'llama' / 'bin'
            bin_dir.mkdir(parents=True)
            for name in names:
                (bin_dir / name).write_bytes(b'fake')

            # app_root 和源码目录都指向一个没有 DLL 的地方，
            # 只留 sys._MEIPASS 能命中，才算真的验证了打包布局
            empty = root / 'elsewhere'
            empty.mkdir()
            native.__file__ = str(empty / 'llmkit' / 'native.py')
            native._app_root = lambda: empty
            sys._MEIPASS = str(root)
            assert native.dll_dir() == bin_dir, native.dll_dir()

            env_dir = root / 'custom'
            env_dir.mkdir()
            for name in names:
                (env_dir / name).write_bytes(b'fake')
            os.environ['QIFEI_LLAMA_DLL_DIR'] = str(env_dir)
            assert native.dll_dir() == env_dir, native.dll_dir()
        print('  打包布局 / 环境变量定位 llama 动态库 OK')
    finally:
        native.__file__, native._app_root = saved[0], saved[1]
        if saved[2] is None:
            if hasattr(sys, '_MEIPASS'):
                del sys._MEIPASS
        else:
            sys._MEIPASS = saved[2]
        if saved[3] is None:
            os.environ.pop('QIFEI_LLAMA_DLL_DIR', None)
        else:
            os.environ['QIFEI_LLAMA_DLL_DIR'] = saved[3]


def test_defaults_are_direct_llama() -> None:
    """没有配置时，界面/角色/运行时都必须默认直连 llama.cpp"""
    from types import SimpleNamespace
    from llmkit import connector, registry
    from llmkit import server as llama_server

    defaults = llmkit.default_settings()
    assert defaults['backend'] == 'local', defaults
    assert defaults['unify_roles'] is True

    empty = connector.from_settings({})
    assert empty.kind == 'local', empty
    assert empty.base_url.endswith(':11500/v1'), empty.base_url
    assert connector._pick_auto({'model_path': 'x.gguf'}) == 'local'
    assert connector._pick_auto({}) == 'local'

    role = SimpleNamespace(provider='local', backend='', timeout=0,
                           keep_alive=None, api_url='', model='', api_key='')
    runtime = connector.from_role(role, {})
    assert runtime.kind == 'local', runtime

    # 角色加载器在没有 llm 段时也要覆盖成 local，不能悄悄回到 Ollama
    import config_override
    from core.client.llm.llm_role_loader import RoleLoader
    from core.client.llm.llm_role_config import RoleConfig
    original_load = config_override.load_overrides
    config_override.load_overrides = lambda: {}
    try:
        cfg = RoleConfig(name='测试', module_name='test', provider='ollama',
                         model='gemma3:4b', enabled=True)
        RoleLoader._apply_runtime_overrides(cfg)
        assert cfg.provider == 'local', cfg.provider
    finally:
        config_override.load_overrides = original_load

    # ensure(local) 不许碰任何 Ollama 函数
    original_ollama = registry.ensure_serving
    original_find = registry.find_ollama_exe
    original_server_ensure = llama_server.LlamaServer.ensure

    def boom(*_args, **_kwargs):
        raise AssertionError('直连 llama.cpp 时不应该调用 Ollama')

    registry.ensure_serving = boom
    registry.find_ollama_exe = boom
    llama_server.LlamaServer.ensure = lambda self, *a, **k: (True, 'fake native')
    try:
        ok, message = connector.ensure(empty)
        assert ok, message
    finally:
        registry.ensure_serving = original_ollama
        registry.find_ollama_exe = original_find
        llama_server.LlamaServer.ensure = original_server_ensure
        llama_server._server = None
    print('  默认直连 llama.cpp OK：配置 / 角色 / ensure 全程不碰 Ollama')


def test_discover_no_ollama(tmp: Path) -> None:
    """直连模式下扫描不查 Ollama 端口，gguf 直接标为可用"""
    from llmkit import registry

    gguf = tmp / 'local-only.Q4_K_M.gguf'
    make_gguf(gguf)

    original_list = registry.list_installed
    original_serving = registry.is_serving
    original_dirs = registry.default_scan_dirs

    def boom(*_args, **_kwargs):
        raise AssertionError('include_ollama=False 时不应该查 Ollama')

    registry.list_installed = boom
    registry.is_serving = boom
    registry.default_scan_dirs = lambda extra=None: [tmp]
    try:
        result = llmkit.discover('http://127.0.0.1:9',
                                 include_ollama=False)
    finally:
        registry.list_installed = original_list
        registry.is_serving = original_serving
        registry.default_scan_dirs = original_dirs

    assert result['installed'] == [], result
    assert result['serving'] is False
    files = result['files']
    assert any(m.path == str(gguf) for m in files), files   # 前面几轮造的 gguf 也在 tmp 里
    assert all(m.ready for m in files), files                # 全部直接可用，不用「导入」
    assert 'native_ok' in result
    print('  直连模式扫描 OK：不查 Ollama，gguf 直接可用')


def test_direct_llama_chain() -> None:
    """内嵌服务 → SSE → OpenAI 兼容客户端 → 运行时处理器，全链路跑通

    用一个假的推理引擎替身，不加载真实大模型，但 HTTP 层、SSE 格式、
    客户端解析、LLMProcessor 全部是真的。
    """
    from llmkit import native as llm_native
    from llmkit import server as llama_server
    from llmkit import connector as llm_connector
    from core.client.llm.llm_client_pool import ClientPool
    from core.client.llm.llm_processor import LLMProcessor
    from core.client.llm.llm_role_config import RoleConfig

    class FakeEngine:
        loaded = True
        n_ctx = 4096

        def generate(self, messages, **kwargs):
            yield {'text': '你'}
            yield {'text': '好'}
            yield {'text': '', 'done': True, 'text_full': '你好',
                   'tokens': 2, 'error': ''}

        def unload(self):
            pass

    original_engine = llm_native.get_engine
    llm_native.get_engine = lambda: FakeEngine()

    runtime = llama_server._Runtime()
    runtime.model_id = 'fake.gguf'
    http = llama_server._LocalHTTPServer(('127.0.0.1', 0), runtime)
    port = http.server_address[1]
    thread = threading.Thread(target=http.serve_forever, daemon=True)
    thread.start()

    base_v1 = f'http://127.0.0.1:{port}/v1'
    original_ensure = llm_connector.ensure
    try:
        assert llama_server.is_serving(port), '健康检查失败'
        assert llama_server.loaded_model(port) == 'fake.gguf'

        # 预热走的是非流式 /v1/chat/completions
        warm_server = llama_server.get_server(port=port)
        ok, message = warm_server.warmup(timeout=5)
        assert ok, message

        # SSE 能被 OpenAI 兼容客户端正确解析
        client = OpenAICompatClient(base_v1, '', 2, 10)
        text = ''
        for chunk in client.chat.completions.create(
                model='fake.gguf',
                messages=[{'role': 'user', 'content': 'hi'}], stream=True):
            if chunk.choices:
                text += chunk.choices[0].delta.content or ''
        assert text == '你好', text

        # 运行时处理器：local 后端 + 内嵌服务，完整跑一轮
        llm_connector.ensure = lambda backend, wait_seconds=30.0: (True, 'fake native')
        processor = LLMProcessor(ClientPool())
        processor._settings = {
            'backend': 'local', 'model': 'fake.gguf',
            'model_path': 'fake.gguf', 'llama_port': port,
            'connect_timeout': 2, 'read_timeout': 10,
        }
        processor._settings_loaded = True
        role = RoleConfig(name='测试', module_name='test', provider='local',
                          api_url=base_v1, model='fake.gguf', enabled=True)
        answer, tokens, _ = processor.process(
            role, [{'role': 'user', 'content': 'hi'}])
        assert answer == '你好', answer
        assert tokens >= 2, tokens
        print(f'  直连 llama.cpp 全链路 OK：内嵌服务 → SSE → 运行时对话 {answer!r}')
    finally:
        llm_connector.ensure = original_ensure
        llm_native.get_engine = original_engine
        http.shutdown()
        http.server_close()
        llama_server._server = None


def main() -> int:
    print('llmkit 自检')
    server, base = start_server()
    try:
        with tempfile.TemporaryDirectory() as folder:
            tmp = Path(folder)
            test_gguf(tmp)
            test_gguf_chat_template()
            test_model_download_idempotent(tmp)
            test_ollama_stream(base)
            test_missing_model(base)
            test_openai_stream(base)
            test_timeout(base)
            test_connect_error()
            test_sanitize()
            test_import_gguf(base, tmp)
            test_connector_settings(base)
            test_runtime_timeouts(base)
            test_processor(base)
            # v2.8：不装 Ollama，直连 llama.cpp
            test_native_library()
            test_native_abi()
            test_packaged_dll_lookup()
            test_defaults_are_direct_llama()
            test_discover_no_ollama(tmp)
            test_direct_llama_chain()
    finally:
        server.shutdown()
    print('全部通过')
    return 0


if __name__ == '__main__':
    sys.exit(main())
