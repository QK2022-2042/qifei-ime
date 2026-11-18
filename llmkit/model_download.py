# coding: utf-8
"""
安装包内置推荐中文润色模型的人工重新下载 / 校验（可选，需要联网）

安装包已经自带 models/llm/ 下的推荐模型，第一次装完就能用，程序不会自动下载。
这里只在用户主动操作时才会跑：设置中心「大模型」页点「下载推荐润色模型」
（模型损坏或想恢复出厂模型时，确认后覆盖重下），或命令行
tools/download_llm_model.py --force。想换其他模型请用「选择模型文件」，
或直接把 .gguf 拷进 models/llm/，同样不需要 Ollama、推理全程离线。

纯标准库；优先用系统 curl（断点续传更可靠），没有 curl 时回落 urllib。
下载到 .part 临时文件，校验通过才改名，避免半截文件被当成模型。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from typing import Callable, List, Optional, Tuple

from .gguf import read_info
from .registry import BUNDLED_CHAT_MODEL, app_root

MODEL = BUNDLED_CHAT_MODEL
# Qwen2.5-1.5B-Instruct Q4_K_M 的官方文件大小
EXPECTED_SIZE = 1_117_320_736
REPO = 'Qwen/Qwen2.5-1.5B-Instruct-GGUF'
SOURCES: List[Tuple[str, str]] = [
    ('魔搭 ModelScope',
     f'https://www.modelscope.cn/models/{REPO}/resolve/master/{MODEL}'),
    ('hf-mirror',
     f'https://hf-mirror.com/{REPO}/resolve/main/{MODEL}'),
    ('Hugging Face',
     f'https://huggingface.co/{REPO}/resolve/main/{MODEL}'),
]

Stage = Callable[[str], None]
_NOOP: Stage = lambda _text: None

# 设置中心是 windowed 程序（spec 里 console=False）。从这里起 curl 必须
# 显式禁止创建新控制台，否则用户点「下载推荐润色模型」会闪一个黑框。
# 与 llmkit/registry.py、llmkit/server.py 的子进程处理保持一致。
_CREATE_FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0


def target() -> Path:
    return app_root() / 'models' / 'llm' / MODEL


def part_file() -> Path:
    return target().with_name(MODEL + '.part')


def describe(path: Path) -> str:
    size = path.stat().st_size
    return f'{size / 1024 ** 3:.2f} GB ({size} bytes)'


def validate(path: Path) -> Tuple[bool, str]:
    """校验文件确实是完整、可对话的 GGUF

    除了大小和魔数，还要确认带 chat template：ASR / 纯补全模型没有模板，
    下回来了也只会输出 `language .../ <asr_text>`，不能拿来润色。
    """
    if not path.is_file():
        return False, f'文件不存在：{path}'
    size = path.stat().st_size
    if size < EXPECTED_SIZE * 0.95 or size > EXPECTED_SIZE * 1.05:
        return False, (f'文件大小异常：{describe(path)}，'
                       f'期望约 {EXPECTED_SIZE} bytes')
    with path.open('rb') as f:
        if f.read(4) != b'GGUF':
            return False, '文件头不是 GGUF（可能下到了 HTML 错误页）'
    try:
        info = read_info(path)
        if info.error or not info.arch:
            return False, f'GGUF 元数据解析失败：{info.error or "缺少 arch"}'
        if not info.has_chat_template:
            return False, ('这个文件没有 chat template，不是可对话的 '
                           'Instruct 模型，润色会输出异常内容')
        return True, (f'校验通过：{info.name} · {info.arch} · {info.quant} · '
                      f'ctx {info.context} · 对话模板 ✓')
    except Exception as exc:
        return False, f'GGUF 元数据解析异常：{type(exc).__name__}: {exc}'


def _download_curl(url: str, dest: Path, say: Stage) -> bool:
    if not shutil.which('curl'):
        return False
    resumed = dest.exists() and dest.stat().st_size > 0
    cmd = ['curl', '-L', '--fail', '--silent', '--show-error',
           '--retry', '3', '--retry-delay', '2',
           '--connect-timeout', '30', '-o', str(dest)]
    if resumed:
        cmd += ['-C', '-']
        say(f'从断点继续（已有 {dest.stat().st_size / 1024 ** 2:.0f} MB）')
    cmd.append(url)
    try:
        result = subprocess.run(
            cmd, capture_output=True, timeout=3600,
            stdin=subprocess.DEVNULL, creationflags=_CREATE_FLAGS)
    except (OSError, subprocess.TimeoutExpired) as exc:
        say(f'curl 失败：{type(exc).__name__}: {exc}')
        return False
    if result.returncode != 0:
        detail = (result.stderr or b'').decode('utf-8', 'replace').strip()
        say(f'curl 返回 {result.returncode}：{detail[-200:]}')
        return False
    return True


def _download_urllib(url: str, dest: Path, say: Stage) -> bool:
    """curl 不可用时的兜底：支持 Range 续传，走系统代理设置"""
    existing = dest.stat().st_size if dest.exists() else 0
    headers = {'Range': f'bytes={existing}-'} if existing else {}
    request = urllib.request.Request(url, headers=headers)
    started = time.time()
    written = existing
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            # 服务端忽略 Range 并返回 200 时，必须从头覆盖写
            full_restart = response.status == 200
            mode = 'wb' if full_restart else ('ab' if existing else 'wb')
            with dest.open(mode) as out:
                if full_restart:
                    written = 0
                while True:
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)
                    written += len(chunk)
                    if (written // (32 * 1024 * 1024) !=
                            (written - len(chunk)) // (32 * 1024 * 1024)):
                        say(f'已下载 {written / 1024 ** 2:.0f} MB')
        say(f'用时 {time.time() - started:.0f}s，共 {describe(dest)}')
        return True
    except Exception as exc:
        say(f'下载失败：{type(exc).__name__}: {exc}')
        return False


def _watch_progress(part: Path, say: Stage, stop: threading.Event) -> None:
    """curl 没有进度回调，靠轮询 .part 文件大小给界面一个反馈"""
    while not stop.wait(3.0):
        try:
            size = part.stat().st_size
        except OSError:
            continue
        say(f'已下载 {size / 1024 ** 2:.0f} MB / '
            f'{EXPECTED_SIZE / 1024 ** 2:.0f} MB（中断没关系，下次会续传）')


def download(force: bool = False,
             on_stage: Optional[Stage] = None) -> Tuple[bool, str]:
    """下载推荐润色模型，返回 (是否成功, 说明)

    on_stage 由界面传入，会在后台线程里被调用，用于更新进度文字。
    """
    say = on_stage or _NOOP
    dest = target()
    tmp = part_file()

    if dest.is_file() and not force:
        ok, message = validate(dest)
        if ok:
            return True, f'模型已就绪，无需重复下载。\n{dest}\n{message}'
        say('已有文件校验失败，重新下载。')
        try:
            dest.unlink()
        except OSError:
            return False, f'无法删除损坏文件：{dest}'

    dest.parent.mkdir(parents=True, exist_ok=True)
    for name, url in SOURCES:
        say(f'尝试下载源：{name}')
        stop = threading.Event()
        watcher = threading.Thread(
            target=_watch_progress, args=(tmp, say, stop), daemon=True,
            name='qifei-model-progress')
        watcher.start()
        try:
            ok = _download_curl(url, tmp, say) or _download_urllib(url, tmp, say)
        finally:
            stop.set()
        if not ok:
            say('该源失败，换下一个。')
            continue
        good, why = validate(tmp)
        if good:
            try:
                tmp.replace(dest)
            except OSError as exc:
                return False, f'无法保存模型文件：{exc}'
            say(f'下载完成：{describe(dest)}')
            return True, f'完成：{dest}\n{describe(dest)}\n{why}'
        say(f'下载内容校验不通过（{why}），丢弃并换源重试。')
        try:
            tmp.unlink()
        except OSError:
            pass

    return False, (f'所有下载源都失败了。可以手动下载后放到：\n{dest}\n'
                   f'或检查网络 / 代理后重试。')
