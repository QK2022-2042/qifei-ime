"""
进程内直连 llama.cpp —— 不装 Ollama、不需要 llama-server.exe

项目里本来就带着 llama.cpp 的动态库（core/server/engines/llama/bin 下的
llama.dll / ggml.dll / ggml-base.dll 以及各 CPU / Vulkan 后端）。
这个模块用 ctypes 直接调它们的 C API 加载 .gguf 做对话推理：

    GGUF 文件 → llama_model_load_from_file → llama_init_from_model
             → llama_chat_apply_template → tokenize → decode → sampler → 流式吐字

为什么不复用 core/server/engines/llama/llama.py：
1. 那份绑定是给 ASR 用的，import 就执行 init()，会全局 chdir / 改 PATH；
2. 它依赖 numpy 和 gguf 第三方包，GUI / 听写客户端的离线包里没有；
3. 它没有 chat template 和流式生成，本来也不是给 LLM 对话写的。

这里全部用标准库，惰性加载（只有真的要用本地模型时才 CDLL），
所有 C 调用都在一把锁里，llama_context 不是线程安全的。
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
from ctypes import (POINTER, Structure, c_bool, c_char_p, c_float, c_int8,
                    c_int32, c_size_t, c_uint32, c_void_p)
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Sequence, Tuple

# ----------------------------------------------------------------------
# ABI：与 core/server/engines/llama/llama.py 里的定义保持一致，
# 这是同一个 llama.cpp 版本编出来的 DLL，结构体布局不能自己猜。
# ----------------------------------------------------------------------
llama_token = c_int32
llama_pos = c_int32
llama_seq_id = c_int32


class llama_model_params(Structure):
    _fields_ = [
        ('devices', POINTER(c_void_p)),
        ('tensor_buft_overrides', POINTER(c_void_p)),
        ('n_gpu_layers', c_int32),
        ('split_mode', c_int32),
        ('main_gpu', c_int32),
        ('tensor_split', POINTER(c_float)),
        ('progress_callback', ctypes.CFUNCTYPE(c_bool, c_float, c_void_p)),
        ('progress_callback_user_data', c_void_p),
        ('kv_overrides', POINTER(c_void_p)),
        ('vocab_only', c_bool),
        ('use_mmap', c_bool),
        ('use_direct_io', c_bool),
        ('use_mlock', c_bool),
        ('check_tensors', c_bool),
        ('use_extra_bufts', c_bool),
        ('no_host', c_bool),
        ('no_alloc', c_bool),
    ]


class llama_context_params(Structure):
    _fields_ = [
        ('n_ctx', c_uint32),
        ('n_batch', c_uint32),
        ('n_ubatch', c_uint32),
        ('n_seq_max', c_uint32),
        ('n_threads', c_int32),
        ('n_threads_batch', c_int32),
        ('rope_scaling_type', c_int32),
        ('pooling_type', c_int32),
        ('attention_type', c_int32),
        ('flash_attn_type', c_int32),
        ('rope_freq_base', c_float),
        ('rope_freq_scale', c_float),
        ('yarn_ext_factor', c_float),
        ('yarn_attn_factor', c_float),
        ('yarn_beta_fast', c_float),
        ('yarn_beta_slow', c_float),
        ('yarn_orig_ctx', c_uint32),
        ('defrag_thold', c_float),
        ('cb_eval', c_void_p),
        ('cb_eval_user_data', c_void_p),
        ('type_k', c_int32),
        ('type_v', c_int32),
        ('abort_callback', c_void_p),
        ('abort_callback_data', c_void_p),
        ('embeddings', c_bool),
        ('offload_kqv', c_bool),
        ('no_perf', c_bool),
        ('op_offload', c_bool),
        ('swa_full', c_bool),
        ('kv_unified', c_bool),
        ('samplers', POINTER(c_void_p)),
        ('n_samplers', c_size_t),
    ]


class llama_sampler_chain_params(Structure):
    _fields_ = [('no_perf', c_bool)]


class llama_batch(Structure):
    _fields_ = [
        ('n_tokens', c_int32),
        ('token', POINTER(llama_token)),
        ('embd', POINTER(c_float)),
        ('pos', POINTER(llama_pos)),
        ('n_seq_id', POINTER(c_int32)),
        ('seq_id', POINTER(POINTER(llama_seq_id))),
        ('logits', POINTER(c_int8)),
    ]


class llama_chat_message(Structure):
    _fields_ = [('role', c_char_p), ('content', c_char_p)]


_LOG_CALLBACK = ctypes.CFUNCTYPE(None, c_int32, c_char_p, c_void_p)


def _silence_log(level, text, user_data):
    return None


_silence_cb = _LOG_CALLBACK(_silence_log)   # 必须常驻，否则回调指针会失效

# ----------------------------------------------------------------------
# DLL 定位与惰性加载
# ----------------------------------------------------------------------
_LIB: Optional[ctypes.CDLL] = None
_GGML: Optional[ctypes.CDLL] = None
_GGML_BASE: Optional[ctypes.CDLL] = None
_LIB_ERROR = ''
_LIB_LOCK = threading.Lock()
_DLL_COOKIE = None      # os.add_dll_directory 的返回值必须常驻，见 load_lib()

_WINDOWS_DLLS = ('ggml.dll', 'ggml-base.dll', 'llama.dll')
_POSIX_DLLS = {
    'darwin': ('libggml.dylib', 'libggml-base.dylib', 'libllama.dylib'),
    'other': ('libggml.so', 'libggml-base.so', 'libllama.so'),
}


def _app_root() -> Path:
    from .registry import app_root
    return app_root()


def dll_dir() -> Optional[Path]:
    """返回包含 llama 动态库的目录，找不到返回 None（只做文件检查，不加载）"""
    names = _WINDOWS_DLLS if os.name == 'nt' else _POSIX_DLLS.get(
        sys.platform, _POSIX_DLLS['other'])

    roots: List[Path] = [_app_root()]
    meipass = getattr(sys, '_MEIPASS', '')
    if meipass:
        roots += [Path(meipass), Path(meipass) / '_internal']
    roots.append(Path(__file__).resolve().parents[1])

    candidates: List[Path] = []
    env_dir = os.environ.get('QIFEI_LLAMA_DLL_DIR', '')
    if env_dir:
        candidates.append(Path(env_dir))
    for root in roots:
        candidates += [
            root / 'core' / 'server' / 'engines' / 'llama' / 'bin',
            root / 'runtime' / 'llama',
            root / 'llama' / 'bin',
            root,
        ]

    seen = set()
    for folder in candidates:
        try:
            key = str(folder.resolve()).lower()
        except OSError:
            continue
        if key in seen:
            continue
        seen.add(key)
        if all((folder / name).is_file() for name in names):
            return folder
    return None


def available() -> Tuple[bool, str]:
    """快速能力探测：不加载 DLL，只看文件在不在"""
    folder = dll_dir()
    if folder is None:
        return False, ('没找到 llama.cpp 动态库（llama.dll / ggml.dll / ggml-base.dll）。'
                       '可设置环境变量 QIFEI_LLAMA_DLL_DIR 指向它们所在目录，'
                       '或改用 llama-server.exe。')
    return True, f'llama.cpp 动态库：{folder}'


def _bind(lib: ctypes.CDLL, ggml: ctypes.CDLL, folder: Path) -> ctypes.CDLL:
    """一次性把用到的 C 函数签名绑好"""

    def fn(name, argtypes, restype):
        func = getattr(lib, name)
        func.argtypes = argtypes
        func.restype = restype
        return func

    fn('llama_backend_init', [], None)
    fn('llama_backend_free', [], None)
    fn('llama_model_default_params', [], llama_model_params)
    fn('llama_model_load_from_file', [c_char_p, llama_model_params], c_void_p)
    fn('llama_model_free', [c_void_p], None)
    fn('llama_model_get_vocab', [c_void_p], c_void_p)
    fn('llama_model_chat_template', [c_void_p, c_char_p], c_char_p)
    fn('llama_model_n_ctx_train', [c_void_p], c_uint32)
    fn('llama_context_default_params', [], llama_context_params)
    fn('llama_init_from_model', [c_void_p, llama_context_params], c_void_p)
    fn('llama_free', [c_void_p], None)
    fn('llama_n_ctx', [c_void_p], c_uint32)
    fn('llama_batch_init', [c_int32, c_int32, c_int32], llama_batch)
    fn('llama_batch_free', [llama_batch], None)
    fn('llama_decode', [c_void_p, llama_batch], c_int32)
    fn('llama_get_memory', [c_void_p], c_void_p)
    fn('llama_memory_clear', [c_void_p, c_bool], None)
    fn('llama_tokenize',
       [c_void_p, c_char_p, c_int32, POINTER(llama_token), c_int32,
        c_bool, c_bool], c_int32)
    fn('llama_vocab_n_tokens', [c_void_p], c_int32)
    fn('llama_vocab_eos', [c_void_p], llama_token)
    fn('llama_vocab_is_eog', [c_void_p, llama_token], c_bool)
    fn('llama_token_to_piece',
       [c_void_p, llama_token, c_char_p, c_int32, c_int32, c_bool], c_int32)
    fn('llama_chat_apply_template',
       [c_char_p, POINTER(llama_chat_message), c_size_t, c_bool,
        c_char_p, c_int32], c_int32)

    fn('llama_sampler_chain_default_params', [], llama_sampler_chain_params)
    fn('llama_sampler_chain_init', [llama_sampler_chain_params], c_void_p)
    fn('llama_sampler_chain_add', [c_void_p, c_void_p], None)
    fn('llama_sampler_init_greedy', [], c_void_p)
    fn('llama_sampler_init_top_k', [c_int32], c_void_p)
    fn('llama_sampler_init_top_p', [c_float, c_size_t], c_void_p)
    fn('llama_sampler_init_temp', [c_float], c_void_p)
    fn('llama_sampler_init_dist', [c_uint32], c_void_p)
    fn('llama_sampler_sample', [c_void_p, c_void_p, c_int32], llama_token)
    fn('llama_sampler_free', [c_void_p], None)

    # 先装静默日志回调：后端注册本身也会打日志，不提前装会刷到控制台。
    # 设 QIFEI_LLAMA_LOG=1 可保留 llama.cpp 原始日志，用于排查加载问题。
    if not os.environ.get('QIFEI_LLAMA_LOG'):
        try:
            fn('llama_log_set', [_LOG_CALLBACK, c_void_p], None)
            lib.llama_log_set(_silence_cb, None)
        except AttributeError:
            pass

    # ggml 后端（CPU / Vulkan 等）必须显式注册，否则 llama_model_load_from_file
    # 会立刻返回空指针并报 "no backends are loaded"，而且静默到只剩一句
    # 「模型加载失败」。ggml_backend_load_all() 只枚举当前工作目录下的
    # ggml-*.dll，而这里的 CWD 通常是程序根目录、bin 却在子目录里，
    # 所以优先用带路径的版本，避免改全局 CWD（客户端是多线程的）。
    registered = False
    try:
        from_path = ggml.ggml_backend_load_all_from_path
        from_path.argtypes = [c_char_p]
        from_path.restype = None
        from_path(str(folder).encode('utf-8'))
        registered = True
    except AttributeError:
        pass

    if not registered:
        # 老版本 DLL 没有 from_path：只能临时切到 bin 目录再枚举，
        # 窗口压到最小并立刻还原
        try:
            load_all = ggml.ggml_backend_load_all
            load_all.argtypes = []
            load_all.restype = None
            cwd = os.getcwd()
            try:
                os.chdir(folder)
                load_all()
            finally:
                os.chdir(cwd)
            registered = True
        except (AttributeError, OSError):
            pass

    # 注册成功与否不能靠猜：数一下后端，0 个就明确报错
    try:
        reg_count = ggml.ggml_backend_reg_count
        reg_count.argtypes = []
        reg_count.restype = c_size_t
        if int(reg_count()) <= 0:
            raise RuntimeError(
                f'ggml 后端没有注册成功（{folder}）。通常是 ggml-cpu-*.dll / '
                f'ggml-vulkan.dll 不在 llama.dll 同目录，或依赖的 '
                f'libomp140.x86_64.dll 缺失。')
    except AttributeError:
        pass

    lib.llama_backend_init()
    return lib


def load_lib() -> ctypes.CDLL:
    """惰性加载动态库（进程内只做一次）"""
    global _LIB, _GGML, _GGML_BASE, _LIB_ERROR, _DLL_COOKIE
    if _LIB is not None:
        return _LIB
    with _LIB_LOCK:
        if _LIB is not None:
            return _LIB
        folder = dll_dir()
        if folder is None:
            raise RuntimeError(available()[1])

        names = _WINDOWS_DLLS if os.name == 'nt' else _POSIX_DLLS.get(
            sys.platform, _POSIX_DLLS['other'])
        # Windows 下让 LoadLibrary 能找到同目录的依赖（ggml-cpu-*.dll 等）
        if hasattr(os, 'add_dll_directory') and _DLL_COOKIE is None:
            # cookie 一旦被 GC，Windows 会把该目录从 DLL 搜索路径移除，
            # 所以必须留一个模块级引用；PATH 只作兜底。
            _DLL_COOKIE = os.add_dll_directory(str(folder))
        os.environ['PATH'] = str(folder) + os.pathsep + os.environ.get('PATH', '')

        try:
            ggml = ctypes.CDLL(str(folder / names[0]))
            ggml_base = ctypes.CDLL(str(folder / names[1]))
            lib = ctypes.CDLL(str(folder / names[2]))
            _bind(lib, ggml, folder)
        except Exception as exc:
            _LIB_ERROR = f'{type(exc).__name__}: {exc}'
            raise RuntimeError(f'加载 llama.cpp 动态库失败（{folder}）：{_LIB_ERROR}') from exc

        _GGML, _GGML_BASE, _LIB = ggml, ggml_base, lib
        return lib


# ----------------------------------------------------------------------
# 推理引擎
# ----------------------------------------------------------------------
def _fallback_prompt(messages: Sequence[Dict[str, str]]) -> str:
    """GGUF 没带 chat template 时的通用 ChatML 兜底"""
    parts: List[str] = []
    for msg in messages:
        role = str(msg.get('role') or 'user')
        content = str(msg.get('content') or '')
        parts.append(f'<|im_start|>{role}\n{content}<|im_end|>\n')
    parts.append('<|im_start|>assistant\n')
    return ''.join(parts)


class LlamaNative:
    """一个常驻的 llama.cpp 模型实例（进程内）"""

    def __init__(self) -> None:
        self._lib: Optional[ctypes.CDLL] = None
        self._model: Optional[int] = None
        self._vocab: Optional[int] = None
        self._ctx: Optional[int] = None
        self._lock = threading.RLock()
        self.model_path = ''
        self.model_id = 'local'
        self.n_ctx = 4096
        self.n_gpu_layers = 0
        self.error = ''

    # ---------- 状态 ----------
    @property
    def loaded(self) -> bool:
        return self._model is not None and self._ctx is not None

    def describe(self) -> str:
        if not self.loaded:
            return '未加载'
        return f'{Path(self.model_path).name} · ctx {self.n_ctx} · gpu {self.n_gpu_layers}'

    # ---------- 加载 / 释放 ----------
    def load(self, model_path: str, n_ctx: int = 4096,
             n_gpu_layers: int = 0, n_threads: int = 0) -> Tuple[bool, str]:
        path = Path(model_path)
        if not path.is_file():
            return False, f'模型文件不存在：{model_path}'

        with self._lock:
            if self.loaded and self.model_path == str(path):
                return True, f'已加载 {path.name}'
            self.unload()

            try:
                lib = load_lib()
            except Exception as exc:
                self.error = str(exc)
                return False, str(exc)

            self._lib = lib
            mparams = lib.llama_model_default_params()
            # 默认是 -1（全部卸载到 GPU）；本地后端默认纯 CPU，归零就够了。
            # 不要动 devices：默认 nullptr 表示由 llama.cpp 自动挑后端，
            # 与同版本 DLL 的 ASR 绑定 core/server/engines/llama/llama.py 一致；
            # 手动塞一个「只有空指针的数组」反而可能让它认为没有可用后端。
            mparams.n_gpu_layers = int(n_gpu_layers)

            model = lib.llama_model_load_from_file(
                str(path).encode('utf-8'), mparams)
            if not model:
                self.error = 'llama_model_load_from_file 返回空指针'
                return False, (f'模型加载失败：{path.name}。可能是文件损坏、'
                               f'量化格式不受支持，或内存/显存不足。')
            self._model = model
            self._vocab = lib.llama_model_get_vocab(model)

            train_ctx = int(lib.llama_model_n_ctx_train(model) or 0)
            ctx_size = max(512, int(n_ctx or 4096))
            if train_ctx:
                ctx_size = min(ctx_size, train_ctx)

            cparams = lib.llama_context_default_params()
            cparams.n_ctx = ctx_size
            cparams.n_batch = min(512, ctx_size)
            cparams.n_ubatch = min(512, ctx_size)
            if n_threads and int(n_threads) > 0:
                cparams.n_threads = int(n_threads)
                cparams.n_threads_batch = int(n_threads)

            ctx = lib.llama_init_from_model(model, cparams)
            if not ctx:
                self.unload()
                return False, f'创建推理上下文失败（n_ctx={ctx_size}），内存可能不足'

            self._ctx = ctx
            self.n_ctx = int(lib.llama_n_ctx(ctx) or ctx_size)
            self.n_gpu_layers = int(n_gpu_layers)
            self.model_path = str(path)
            # 与 server.ensure() 里 loaded_model() 的比较口径保持一致
            self.model_id = path.name
            self.error = ''
            return True, f'已加载 {path.name}（进程内 llama.cpp，ctx {self.n_ctx}）'

    def unload(self) -> None:
        with self._lock:
            lib = self._lib
            if lib is not None:
                try:
                    if self._ctx:
                        lib.llama_free(self._ctx)
                    if self._model:
                        lib.llama_model_free(self._model)
                except Exception:
                    pass
            self._ctx = None
            self._model = None
            self._vocab = None
            self.model_path = ''
            self.model_id = 'local'

    # ---------- 内部小工具 ----------
    def _format_prompt(self, messages: Sequence[Dict[str, str]]) -> str:
        lib = self._lib
        assert lib is not None
        tmpl = None
        try:
            tmpl = lib.llama_model_chat_template(self._model, None)
        except Exception:
            tmpl = None
        if not tmpl:
            return _fallback_prompt(messages)

        count = len(messages)
        arr = (llama_chat_message * count)()
        for i, msg in enumerate(messages):
            arr[i].role = str(msg.get('role') or 'user').encode('utf-8')
            arr[i].content = str(msg.get('content') or '').encode('utf-8')

        size = 4096
        for msg in messages:
            size += len(str(msg.get('content') or '').encode('utf-8')) * 2
        size = min(max(size, 4096), 4 * 1024 * 1024)
        for _ in range(4):
            buf = ctypes.create_string_buffer(size)
            written = lib.llama_chat_apply_template(
                tmpl, arr, count, True, buf, size)
            if 0 <= written <= size:
                return buf.raw[:written].decode('utf-8', 'replace')
            if written < 0:
                size = min(max(size * 2, -written + 1), 16 * 1024 * 1024)
            else:
                size = min(written + 1, 16 * 1024 * 1024)
        return _fallback_prompt(messages)

    def _tokenize(self, prompt: str) -> List[int]:
        lib = self._lib
        assert lib is not None and self._vocab is not None
        raw = prompt.encode('utf-8')
        capacity = len(raw) + 64
        tokens = (llama_token * capacity)()
        count = lib.llama_tokenize(
            self._vocab, raw, len(raw), tokens, capacity, False, True)
        if count < 0:
            capacity = -count + 8
            tokens = (llama_token * capacity)()
            count = lib.llama_tokenize(
                self._vocab, raw, len(raw), tokens, capacity, False, True)
        if count <= 0:
            return []
        return [int(tokens[i]) for i in range(min(count, capacity))]

    def _piece(self, token: int) -> str:
        lib = self._lib
        assert lib is not None and self._vocab is not None
        buf = ctypes.create_string_buffer(256)
        count = lib.llama_token_to_piece(
            self._vocab, token, buf, len(buf), 0, True)
        if count <= 0:
            return ''
        return buf.raw[:count].decode('utf-8', 'replace')

    def _decode(self, tokens: Sequence[int], pos: int, want_logits: bool) -> bool:
        """把一批 token 喂进 context；pos 从 0 开始连续递增"""
        lib = self._lib
        assert lib is not None and self._ctx is not None
        count = len(tokens)
        batch = lib.llama_batch_init(count, 0, 1)
        try:
            # llama_batch_init 只按容量分配内存，n_tokens 仍然是 0！
            # 不填实际 token 数的话 llama_decode 会直接报 "n_tokens == 0"。
            batch.n_tokens = count
            for i, token in enumerate(tokens):
                batch.token[i] = token
                batch.pos[i] = pos + i
                batch.n_seq_id[i] = 1
                batch.seq_id[i][0] = 0
                batch.logits[i] = 0
            if want_logits:
                batch.logits[count - 1] = 1
            return lib.llama_decode(self._ctx, batch) == 0
        finally:
            lib.llama_batch_free(batch)

    def _make_sampler(self, temperature: float, top_p: float):
        lib = self._lib
        assert lib is not None
        params = lib.llama_sampler_chain_default_params()
        chain = lib.llama_sampler_chain_init(params)
        if not chain:
            return None
        try:
            if temperature <= 0:
                lib.llama_sampler_chain_add(chain, lib.llama_sampler_init_greedy())
            else:
                lib.llama_sampler_chain_add(chain, lib.llama_sampler_init_top_k(40))
                lib.llama_sampler_chain_add(
                    chain, lib.llama_sampler_init_top_p(float(top_p or 0.9), 1))
                lib.llama_sampler_chain_add(
                    chain, lib.llama_sampler_init_temp(float(temperature)))
                lib.llama_sampler_chain_add(
                    chain, lib.llama_sampler_init_dist(int(time.time()) & 0xFFFFFFFF))
        except Exception:
            lib.llama_sampler_free(chain)
            raise
        return chain

    # ---------- 生成 ----------
    def generate(self, messages: Sequence[Dict[str, str]],
                 max_tokens: int = 512, temperature: float = 0.2,
                 top_p: float = 0.9,
                 stop: Optional[Sequence[str]] = None) -> Iterator[Dict[str, object]]:
        """流式生成，逐段 yield {'text': 片段}；结束前 yield {'done': True, ...}

        llama_context 不是线程安全的，整轮生成都在 self._lock 里跑。
        每轮请求清空 KV cache 重新 prefill：听写润色的输入都很短，
        换来的是不用管会话状态、不会串上下文。
        """
        if not self.loaded:
            yield {'text': '', 'done': True, 'error': '模型没有加载'}
            return

        stops = [s for s in (stop or []) if s]
        with self._lock:
            lib = self._lib
            assert lib is not None and self._ctx is not None
            text = ''
            produced = 0
            error = ''
            try:
                prompt = self._format_prompt(messages)
                prompt_tokens = self._tokenize(prompt)
                if not prompt_tokens:
                    raise RuntimeError('提示词 tokenize 失败，模型可能缺少词表')

                lib.llama_memory_clear(lib.llama_get_memory(self._ctx), True)

                room = max(1, self.n_ctx - len(prompt_tokens) - 1)
                limit = min(max(1, int(max_tokens or 512)), room)
                if not self._decode(prompt_tokens, 0, True):
                    raise RuntimeError('llama_decode 处理提示词失败')

                chain = self._make_sampler(temperature, top_p)
                if chain is None:
                    raise RuntimeError('创建采样器失败')
                pos = len(prompt_tokens)

                try:
                    for _ in range(limit):
                        token = int(lib.llama_sampler_sample(chain, self._ctx, -1))
                        if lib.llama_vocab_is_eog(self._vocab, token):
                            break
                        piece = self._piece(token)
                        if not piece:
                            break

                        candidate = text + piece
                        cut_at = None
                        for marker in stops:
                            index = candidate.find(marker)
                            if index >= 0 and (cut_at is None or index < cut_at):
                                cut_at = index
                        hit_stop = cut_at is not None
                        if hit_stop:
                            candidate = candidate[:cut_at]

                        emit = candidate[len(text):] if len(candidate) >= len(text) else ''
                        if emit:
                            yield {'text': emit}
                        text = candidate
                        produced += 1
                        if hit_stop:
                            break

                        if not self._decode([token], pos, True):
                            break
                        pos += 1
                finally:
                    lib.llama_sampler_free(chain)
            except Exception as exc:
                error = f'{type(exc).__name__}: {exc}'

            yield {'text': '', 'done': True, 'text_full': text,
                   'tokens': produced, 'error': error}


# 进程内单例：GUI 和听写客户端各维护自己的一份
_engine: Optional[LlamaNative] = None
_engine_lock = threading.Lock()


def get_engine() -> LlamaNative:
    global _engine
    if _engine is None:
        with _engine_lock:
            if _engine is None:
                _engine = LlamaNative()
    return _engine


def shutdown() -> None:
    global _engine
    with _engine_lock:
        if _engine is not None:
            _engine.unload()
            _engine = None


__all__ = ['LlamaNative', 'available', 'dll_dir', 'get_engine', 'load_lib',
           'shutdown']
