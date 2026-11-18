"""
本地大模型注册表

解决的核心问题：用户自己下载好一个 .gguf 文件，程序却「不认识」，
非要联网 `ollama pull` 才能用。

这里做的事：
1. 扫描本机所有可能放模型的地方（应用目录、Ollama、LM Studio、自定义目录）；
2. 读 GGUF 头部，把文件名翻译成「7B · Q4_K_M」这种看得懂的信息，
   并标出有没有 chat template —— 没有的就是 ASR / 补全专用模型，不能拿来润色；
3. 直连 llama.cpp 模式下 .gguf 本身就是可用模型，不需要注册；
4. Ollama 只是可选后端：只有用户显式选中时才找 ollama.exe、拉服务、
   `ollama create` 注册，全程不联网。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

from .gguf import GgufInfo, read_info
from .http_client import (LLMConnectError, LLMHTTPError, LLMTimeoutError,
                          OllamaClient)

DEFAULT_HOST = 'http://127.0.0.1:11434'

# 随安装包内置的默认润色模型，放在 models/llm/ 下，第一次装完就能用。
# 文件在就自动设为默认模型；用户人工换过的模型不会被覆盖安装替换。
# 仅在用户主动确认时才会重新下载这一份（tools/download_llm_model.py --force）。
BUNDLED_CHAT_MODEL = 'qwen2.5-1.5b-instruct-q4_k_m.gguf'

# 扫描上限，避免把整个硬盘翻一遍
_MAX_GGUF = 300
_MAX_DEPTH = 4

_CREATE_FLAGS = 0
if os.name == 'nt':
    _CREATE_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


# ======================================================================
# 数据结构
# ======================================================================
@dataclass
class LocalModel:
    """界面上展示的一条模型记录"""
    key: str                      # 唯一标识（下拉框 data）
    name: str                     # 显示名
    kind: str                     # 'ollama' | 'gguf'
    detail: str = ''              # 参数 · 量化 · 体积
    path: str = ''                # gguf 绝对路径（ollama 模型为空）
    size_bytes: int = 0
    context: int = 0
    ready: bool = True            # 是否能直接被对话接口使用
    source: str = ''              # 来源说明
    chat_ready: bool = True       # 有没有 chat template（Instruct 对话模型）
    warning: str = ''             # 选中后要提醒用户的问题

    @property
    def display(self) -> str:
        # 「非对话模型」比「未导入」更重要：直连模式下 ready=True 看不出来，
        # 但纯 scan_gguf 时 ready=False，也不能把这条警告盖掉。
        if not self.chat_ready:
            mark = '（非对话模型）'
        elif not self.ready:
            mark = '（未导入）'
        else:
            mark = ''
        suffix = f'　{self.detail}' if self.detail else ''
        return f'{self.name}{mark}{suffix}'


# ======================================================================
# Ollama 进程与接口
# ======================================================================
def find_ollama_exe(extra: str = '') -> str:
    """找 ollama.exe，找不到返回空串"""
    if extra and Path(extra).is_file():
        return str(Path(extra))

    found = shutil.which('ollama')
    if found:
        return found

    candidates = []
    local_appdata = os.environ.get('LOCALAPPDATA', '')
    program_files = os.environ.get('ProgramFiles', r'C:\Program Files')
    program_files_x86 = os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)')
    home = Path.home()

    if local_appdata:
        candidates += [
            Path(local_appdata) / 'Programs' / 'Ollama' / 'ollama.exe',
            Path(local_appdata) / 'Ollama' / 'ollama.exe',
        ]
    candidates += [
        Path(program_files) / 'Ollama' / 'ollama.exe',
        Path(program_files_x86) / 'Ollama' / 'ollama.exe',
        home / 'AppData' / 'Local' / 'Programs' / 'Ollama' / 'ollama.exe',
    ]
    for path in candidates:
        try:
            if path.is_file():
                return str(path)
        except OSError:
            continue
    return ''


def make_client(host: str = DEFAULT_HOST, timeout: float = 3.0) -> OllamaClient:
    return OllamaClient(host or DEFAULT_HOST, connect_timeout=timeout,
                        read_timeout=max(timeout, 10.0))


def is_serving(host: str = DEFAULT_HOST, timeout: float = 1.5) -> bool:
    """服务是否活着"""
    try:
        make_client(host, timeout).version(timeout=timeout)
        return True
    except Exception:
        return False


def ensure_serving(host: str = DEFAULT_HOST, exe: str = '',
                   wait_seconds: float = 25.0) -> Tuple[bool, str]:
    """确保 Ollama 服务在跑，必要时后台拉起

    Returns:
        (是否可用, 说明)
    """
    if is_serving(host):
        return True, '已连接'

    binary = find_ollama_exe(exe)
    if not binary:
        return False, '没有找到 Ollama，可改用「内置 llama.cpp」后端，或先安装 Ollama'

    try:
        creation = _CREATE_FLAGS | getattr(subprocess, 'DETACHED_PROCESS', 0)
        subprocess.Popen(
            [binary, 'serve'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, creationflags=creation, close_fds=True,
        )
    except Exception as exc:
        return False, f'启动 Ollama 失败：{exc}'

    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        if is_serving(host, timeout=1.5):
            return True, '已启动 Ollama 服务'
        time.sleep(0.6)
    return False, f'Ollama 已拉起，但 {wait_seconds:.0f} 秒内没响应，请检查端口 {host}'


def list_installed(host: str = DEFAULT_HOST, timeout: float = 5.0) -> List[LocalModel]:
    """Ollama 里已经可用的模型"""
    models: List[LocalModel] = []
    try:
        raw = make_client(host, timeout).tags(timeout=timeout)
    except Exception:
        return models

    for item in raw:
        name = str(item.get('name') or item.get('model') or '').strip()
        if not name:
            continue
        size = int(item.get('size') or 0)
        detail = _fmt_size(size)
        models.append(LocalModel(
            key=f'ollama::{name}',
            name=name,
            kind='ollama',
            detail=detail,
            size_bytes=size,
            ready=True,
            source='Ollama',
        ))
    models.sort(key=lambda m: m.name.lower())
    return models


def running_models(host: str = DEFAULT_HOST, timeout: float = 3.0) -> List[str]:
    """当前已加载进内存的模型名"""
    try:
        raw = make_client(host, timeout).running(timeout=timeout)
    except Exception:
        return []
    return [str(item.get('name') or '') for item in raw if item.get('name')]


# ======================================================================
# 本地 GGUF 扫描
# ======================================================================
def app_root() -> Path:
    """应用根目录：打包后是 exe 所在目录，源码运行是仓库根目录"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


def bundled_chat_model() -> str:
    """返回随产品提供的默认润色模型路径；文件不存在时返回空串。

    只认这一个固定文件名，不扫描整个目录，避免把 ASR 专用模型
    （例如 qwen3_asr_llm.gguf）误当成对话模型自动选中 —— 那类模型没有
    chat template，只会输出 `language .../ <asr_text>` 转写格式。
    """
    path = app_root() / 'models' / 'llm' / BUNDLED_CHAT_MODEL
    try:
        return str(path) if path.is_file() else ''
    except OSError:
        return ''


def norm_path(value: str) -> str:
    """路径比较专用：统一大小写、分隔符、首尾空白，并解析符号链接。

    Windows 上盘符大小写、`\\` 与 `/`、末尾空格、`.` / `..` 都会让
    「配置里记的路径」和「扫描出来的路径」字符串不相等，进而导致界面
    明明列出了模型却选不中。所有路径比较都必须走这里。

    v2.12：还必须解析符号链接 / junction。安装目录经常是
    `C:\\Users\\...\\Programs\\QifeiIME` 指向 `D:\\Programs\\QifeiIME`
    的链接：`app_root()` 用 `Path.resolve()` 拿到真实路径，扫描结果也是
    真实路径，而配置里可能记着链接路径。只做字符串规范化时两者不相等，
    界面会误判「当前模型没被扫到」，进而把用户人工选好的模型换掉。
    `os.path.realpath()` 对不存在的路径也能安全返回规范化的绝对路径，
    所以这里依旧不要求文件真实存在。
    """
    text = str(value or '').strip()
    if not text:
        return ''
    try:
        return os.path.normcase(os.path.realpath(os.path.normpath(text)))
    except (OSError, ValueError):
        try:
            return os.path.normcase(os.path.abspath(os.path.normpath(text)))
        except (OSError, ValueError):
            return text.lower()


def same_path(left: str, right: str) -> bool:
    """两个路径是否指向同一个文件（含符号链接 / junction / 大小写差异）。

    先做规范化字符串比较（快，且不要求文件存在）；不相等但两边都是真实
    文件时，再用 `(st_dev, st_ino)` 兜底 —— 某些 Windows 卷上 realpath
    解析不完整，文件 identity 更可靠。
    """
    a, b = norm_path(left), norm_path(right)
    if a and b and a == b:
        return True
    try:
        sa = os.stat(str(left or '').strip())
        sb = os.stat(str(right or '').strip())
        return (sa.st_dev, sa.st_ino) == (sb.st_dev, sb.st_ino)
    except (OSError, ValueError):
        return False


def resolve_model_path(configured: str = '',
                       extra_dirs: Optional[List[str]] = None
                       ) -> Tuple[str, str]:
    """把配置里的模型路径解析成「这台机器上真实可用」的路径。

    旧版把 model_path 写成绝对路径写进 %APPDATA%。换电脑、换盘符、
    换用户名、换安装目录，或者把绿色版整个拷到另一台机器上之后，
    这个路径就失效了 —— 界面能扫到随包模型，但客户端仍拿着旧路径去
    llama_model_load_from_file()，于是表现为「装完不识别大模型」。

    解析顺序：
    1. 配置路径本身有效 → 原样返回（不改动用户选择）；
    2. 失效 → 按**文件名**在安装目录 / 原配置目录 / 用户常用目录里重定位；
    3. 还找不到 → 退回随包内置的推荐润色模型；
    4. 内置模型也没有 → 返回空串，让界面明确提示用户手动选文件。

    Returns:
        (可用路径或空串, 说明文字)。说明为空表示原配置本来就有效。
    """
    configured = str(configured or '').strip()
    if configured:
        try:
            if Path(configured).is_file():
                return str(Path(configured)), ''
        except OSError:
            pass

    name = Path(configured).name.lower() if configured else ''

    # 候选目录按优先级排：安装目录最先，其次是旧配置自己指向的目录
    # （用户可能只是把整个文件夹搬了家），再是用户下载 / Ollama 等常见位置。
    dirs: List[Path] = [app_root() / 'models' / 'llm', app_root() / 'models']
    if configured:
        try:
            dirs.append(Path(configured).parent)
        except (OSError, ValueError):
            pass
    dirs += [Path(d) for d in (extra_dirs or []) if d]
    dirs += default_scan_dirs()

    seen = set()
    if name:
        for directory in dirs:
            try:
                key = norm_path(str(directory))
                if key in seen or not directory.is_dir():
                    continue
                seen.add(key)
                for entry in directory.iterdir():
                    if (entry.is_file() and entry.suffix.lower() == '.gguf'
                            and entry.name.lower() == name):
                        found = str(entry.resolve())
                        return found, (f'原配置路径已失效，已自动定位到：{found}')
            except OSError:
                continue

    bundled = bundled_chat_model()
    if bundled:
        note = (f'原配置路径已失效，已改用随包内置模型：{bundled}'
                if configured else '')
        return bundled, note

    # 没有随包内置模型（macOS 包、精简包，或用户删掉了出厂模型）时，
    # 用户自己下载放好的模型很可能是别的文件名，按同名是找不到的。
    # 这里扫一遍候选目录，挑第一个真正带 chat template 的对话模型。
    # 只有「配置为空 / 配置路径失效」才会走到这里，不会顶掉用户手动
    # 选好的模型；ASR / embedding 之类没有模板的模型会被跳过。
    try:
        for model in scan_gguf(dirs, limit=80):
            if not model.chat_ready:
                continue
            candidate = Path(model.path)
            if candidate.is_file():
                found = str(candidate.resolve())
                prefix = ('原配置路径已失效，已自动识别到可用对话模型'
                          if configured else '已自动识别到可用对话模型')
                return found, f'{prefix}：{found}'
    except OSError:
        pass

    if configured:
        return '', (f'原配置的模型路径已失效（{configured}），本机也没找到可用对话模型；'
                    '请在「大模型」页点「选择模型文件（已下载好的）」，'
                    '或把 .gguf 放进模型目录。')
    return '', ''


def default_scan_dirs(extra: Optional[List[str]] = None) -> List[Path]:
    """默认扫描目录"""
    dirs: List[Path] = []
    home = Path.home()

    # 应用自带目录（打包后是 exe 旁边）
    root = app_root()
    dirs.append(root / 'models' / 'llm')
    dirs.append(root / 'models')

    # 注意：不要把 ~/Downloads 放进来。macOS 的 TCC 会在首次访问「下载」时
    # 弹授权框并阻塞调用线程；App 由 LaunchServices 启动时（有自己的 TCC
    # 身份）会在启动阶段卡死。需要扫描自定义目录请在设置里显式添加。
    dirs += [
        home / '.ollama' / 'models' / 'blobs',
        home / '.lmstudio' / 'models',
        home / '.cache' / 'lm-studio' / 'models',
        home / 'Models',
    ]
    if sys.platform == 'darwin':
        dirs.append(home / 'Library' / 'Application Support' / 'QifeiIME' / 'models' / 'llm')
    if os.name == 'nt':
        local_appdata = os.environ.get('LOCALAPPDATA', '')
        if local_appdata:
            dirs.append(Path(local_appdata) / 'LM-Studio' / 'models')

    for item in extra or []:
        if item:
            dirs.append(Path(item))

    seen, result = set(), []
    for path in dirs:
        try:
            key = str(path.resolve()).lower()
        except OSError:
            continue
        if key not in seen:
            seen.add(key)
            result.append(path)
    return result


def scan_gguf(dirs: Optional[List[Path]] = None,
              limit: int = _MAX_GGUF) -> List[LocalModel]:
    """扫描目录里的 .gguf 文件"""
    models: List[LocalModel] = []
    seen = set()

    for root in dirs or default_scan_dirs():
        if not root.exists() or not root.is_dir():
            continue
        base_depth = len(root.parts)
        for current, subdirs, files in os.walk(root):
            depth = len(Path(current).parts) - base_depth
            if depth >= _MAX_DEPTH:
                subdirs[:] = []
            subdirs[:] = [d for d in subdirs if not d.startswith('.')]

            for filename in files:
                if not filename.lower().endswith('.gguf'):
                    continue
                full = Path(current) / filename
                try:
                    key = str(full.resolve()).lower()
                except OSError:
                    continue
                if key in seen:
                    continue
                seen.add(key)
                models.append(_describe_gguf(full))
                if len(models) >= limit:
                    return models
    models.sort(key=lambda m: m.name.lower())
    return models


def _describe_gguf(path: Path) -> LocalModel:
    info: GgufInfo = read_info(path)
    name = info.name or path.stem
    detail_bits = [b for b in (info.params_text, info.quant) if b]
    if info.size_text:
        detail_bits.append(info.size_text)
    detail = ' · '.join(detail_bits)
    source = path.parent.name if path.parent.name else str(path.parent)

    if info.error:
        detail = f'读取失败：{info.error}'

    # ASR / embedding / 纯补全模型没有 chat template，能加载但不能对话：
    # 界面上标出来，默认选中时也跳过，别让用户莫名其妙拿到
    # `language Chinese<asr_text>...` 这种输出。
    chat_ready = info.has_chat_template
    warning = '' if chat_ready else (
        '这个文件没有 chat template，通常是语音识别 / 文本补全专用模型，'
        '用来润色或对话会输出异常内容（例如 language .../ <asr_text>）。'
        '建议换成带 -Instruct 的通用对话模型。')

    return LocalModel(
        key=f'gguf::{path}',
        name=name,
        kind='gguf',
        detail=detail,
        path=str(path),
        size_bytes=info.size_bytes,
        context=info.context,
        ready=False,
        source=source,
        chat_ready=chat_ready,
        warning=warning,
    )


def discover(host: str = DEFAULT_HOST, extra_dirs: Optional[List[str]] = None,
             include_gguf: bool = True,
             include_ollama: bool = True) -> Dict[str, object]:
    """汇总：已安装模型 + 待导入的本地文件

    include_ollama=False 用于「直连 llama.cpp」模式：完全不碰 Ollama，
    不查 /api/tags、不探测 11434 端口，扫描瞬间返回，也不会误报
    「Ollama 未运行」吓到没装 Ollama 的用户。
    """
    installed = list_installed(host) if include_ollama else []
    files = scan_gguf(default_scan_dirs(extra_dirs)) if include_gguf else []

    installed_names = {m.name.lower() for m in installed}
    pending: List[LocalModel] = []
    for model in files:
        if _match_installed(model, installed_names):
            continue
        if not include_ollama:
            model.ready = True   # 直连模式下 .gguf 本身就是可用模型，不需要注册
        pending.append(model)

    from . import native as llm_native
    native_ok, native_message = llm_native.available()

    return {
        'installed': installed,
        'files': pending,
        'serving': (bool(installed) or is_serving(host)) if include_ollama else False,
        'host': host,
        'native_ok': native_ok,
        'native_message': native_message,
    }


def _match_installed(model: LocalModel, names: set) -> bool:
    stem = Path(model.path).stem.lower() if model.path else model.name.lower()
    if stem in names:
        return True
    # gemma-3-4b-it-Q4_K_M <-> gemma3:4b 这种不完全同名的情况不强求匹配
    return False


# ======================================================================
# 导入 GGUF 到 Ollama
# ======================================================================
class ImportError_(Exception):
    """导入失败"""


def import_gguf(gguf_path: str, model_name: str = '',
                host: str = DEFAULT_HOST, exe: str = '',
                on_stage: Callable[[str], None] = lambda s: None,
                timeout: float = 3600.0) -> Tuple[bool, str]:
    """把本地 .gguf 注册成 Ollama 模型（不联网）

    Returns:
        (是否成功, 模型名或错误说明)
    """
    path = Path(gguf_path)
    if not path.is_file():
        return False, f'文件不存在：{gguf_path}'
    if path.suffix.lower() != '.gguf':
        return False, '只能导入 .gguf 文件'

    ok, message = ensure_serving(host, exe)
    if not ok:
        return False, message

    binary = find_ollama_exe(exe)
    if not binary:
        return False, '没有找到 ollama.exe，无法注册模型'

    name = sanitize_name(model_name or path.stem)
    info = read_info(path)

    on_stage(f'正在注册 {name}…')
    modelfile = _build_modelfile(path, info)
    temp_dir = Path(tempfile.mkdtemp(prefix='qifei-modelfile-'))
    modelfile_path = temp_dir / 'Modelfile'
    try:
        modelfile_path.write_text(modelfile, encoding='utf-8')
        result = subprocess.run(
            [binary, 'create', name, '-f', str(modelfile_path)],
            capture_output=True, text=True, encoding='utf-8', errors='replace',
            timeout=timeout, creationflags=_CREATE_FLAGS,
        )
    except subprocess.TimeoutExpired:
        return False, f'注册超时（{timeout:.0f} 秒），模型可能过大'
    except Exception as exc:
        return False, f'注册失败：{exc}'
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        return False, f'注册失败：{detail[-300:] or "未知错误"}'

    # 校验真的能查到
    on_stage('校验模型…')
    deadline = time.time() + 20
    while time.time() < deadline:
        names = {m.name for m in list_installed(host)}
        if name in names or f'{name}:latest' in names:
            return True, name
        time.sleep(0.5)

    return False, f'注册命令已执行，但模型列表里没有 {name}，请查看 Ollama 日志'


def _build_modelfile(path: Path, info: GgufInfo) -> str:
    """生成 Modelfile：只要 FROM，模板与参数由 GGUF 元数据自动推断"""
    lines = [f'FROM "{path.as_posix()}"']
    if info.context and info.context > 0:
        # 给一个保守的上下文，太大会吃满显存
        ctx = min(int(info.context), 8192)
        lines.append(f'PARAMETER num_ctx {ctx}')
    return '\n'.join(lines) + '\n'


def sanitize_name(name: str) -> str:
    """把文件名整理成合法的 Ollama 模型名"""
    text = Path(str(name)).stem if str(name).lower().endswith('.gguf') else str(name)
    text = text.strip().lower()
    text = re.sub(r'[^a-z0-9._\-]+', '-', text)
    text = re.sub(r'-{2,}', '-', text).strip('-._')
    if not text:
        text = 'local-model'
    if text[0].isdigit():
        text = f'm-{text}'
    return text[:64]


def delete_model(model: str, host: str = DEFAULT_HOST) -> Tuple[bool, str]:
    """删除一个 Ollama 模型"""
    client = make_client(host, 10)
    try:
        client._json('DELETE', '/api/delete', {'model': model}, timeout=30)
        return True, f'已删除 {model}'
    except Exception as exc:
        return False, f'删除失败：{exc}'


def _fmt_size(size: int) -> str:
    if size <= 0:
        return ''
    gb = size / (1024 ** 3)
    if gb >= 1:
        return f'{gb:.1f} GB'
    return f'{size / (1024 ** 2):.0f} MB'
