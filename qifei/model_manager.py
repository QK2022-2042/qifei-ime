# coding: utf-8
"""
语音模型管理

- 列出可选引擎与所需文件
- 检测模型是否就绪（支持任意自定义模型目录）
- 断点续传下载 + 自动解压 + 目录层级修正

模型包来自 CapsWriter-Offline 的 models Release，
下载后按 config_server.ModelPaths 约定的结构落盘。
"""

from __future__ import annotations

import os
import shutil
import time
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, List, Optional, Tuple

BASE_URL = 'https://github.com/HaujetZhao/CapsWriter-Offline/releases/download/models/'

# 国内加速镜像（前缀拼接）
MIRRORS = {
    'github': '',
    'ghproxy': 'https://ghfast.top/',
}


@dataclass
class Package:
    """一个需要下载的压缩包"""
    filename: str
    target: str            # 解压到 模型根目录/该子目录
    size_mb: int

    @property
    def url(self) -> str:
        return BASE_URL + self.filename


@dataclass
class ModelSpec:
    key: str
    name: str
    desc: str
    badge: str
    packages: List[Package]
    required: List[str]
    tag: str = ''

    @property
    def total_mb(self) -> int:
        return sum(p.size_mb for p in self.packages)


MODELS: dict[str, ModelSpec] = {
    'sensevoice': ModelSpec(
        key='sensevoice',
        name='SenseVoice 小模型',
        desc='启动快、占用小，支持中英日韩粤，日常听写首选。',
        badge='推荐',
        tag='约 414 MB',
        packages=[Package('Sensevoice-Small-ONNX.zip', 'SenseVoice-Small', 414)],
        required=[
            'SenseVoice-Small/Sensevoice-Small-ONNX/SenseVoice-Encoder.fp16.onnx',
            'SenseVoice-Small/Sensevoice-Small-ONNX/SenseVoice-CTC.fp16.onnx',
            'SenseVoice-Small/Sensevoice-Small-ONNX/tokenizer.bpe.model',
        ],
    ),
    'paraformer': ModelSpec(
        key='paraformer',
        name='Paraformer',
        desc='纯 CPU 也能跑，速度最快，准确率略低，适合老电脑。',
        badge='轻量',
        tag='约 488 MB',
        packages=[
            Package('Paraformer.zip', 'Paraformer', 229),
            Package('Punct-CT-Transformer.zip', 'Punct-CT-Transformer', 259),
        ],
        required=[
            'Paraformer/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx/model.onnx',
            'Paraformer/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-onnx/tokens.txt',
            'Punct-CT-Transformer/sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12/model.onnx',
        ],
    ),
    'fun_asr_nano': ModelSpec(
        key='fun_asr_nano',
        name='Fun-ASR-Nano',
        desc='准确率与速度平衡，支持热词上下文，适合办公记录。',
        badge='均衡',
        tag='约 796 MB',
        packages=[Package('Fun-ASR-Nano-GGUF.zip', 'Fun-ASR-Nano', 796)],
        required=[
            'Fun-ASR-Nano/Fun-ASR-Nano-GGUF/Fun-ASR-Nano-Encoder-Adaptor.fp16.onnx',
            'Fun-ASR-Nano/Fun-ASR-Nano-GGUF/Fun-ASR-Nano-CTC.fp16.onnx',
            'Fun-ASR-Nano/Fun-ASR-Nano-GGUF/Fun-ASR-Nano-Decoder.q5_k.gguf',
            'Fun-ASR-Nano/Fun-ASR-Nano-GGUF/tokens.txt',
        ],
    ),
    'qwen_asr': ModelSpec(
        key='qwen_asr',
        name='Qwen3-ASR 1.7B',
        desc='准确率最高，长句与专业词汇表现好，需要较好的显卡或耐心。',
        badge='最准',
        tag='约 1.86 GB',
        packages=[Package('Qwen3-ASR-1.7B-q5_k.zip', 'Qwen3-ASR', 1861)],
        required=[
            'Qwen3-ASR/Qwen3-ASR-1.7B/qwen3_asr_encoder_frontend.onnx',
            'Qwen3-ASR/Qwen3-ASR-1.7B/qwen3_asr_encoder_backend.onnx',
            'Qwen3-ASR/Qwen3-ASR-1.7B/qwen3_asr_llm.gguf',
        ],
    ),
}


def resolve_url(url: str, mirror: str = 'github') -> str:
    """按下载源拼接 URL"""
    prefix = MIRRORS.get(mirror, '')
    return prefix + url if prefix else url


def missing_files(spec: ModelSpec, root: Path) -> List[str]:
    """返回缺失的相对路径列表"""
    return [rel for rel in spec.required if not (root / rel).exists()]


def is_ready(spec: ModelSpec, root: Path) -> bool:
    return not missing_files(spec, root)


def detect_installed(root: Path) -> List[str]:
    """检测目录里已就绪的引擎"""
    return [key for key, spec in MODELS.items() if is_ready(spec, root)]


def _normalize_nesting(target_dir: Path) -> None:
    """修正压缩包多套一层同名目录的问题"""
    for _ in range(3):
        try:
            entries = list(target_dir.iterdir())
        except OSError:
            return
        inner = target_dir / target_dir.name
        if len(entries) == 1 and inner.is_dir():
            for child in list(inner.iterdir()):
                shutil.move(str(child), str(target_dir / child.name))
            try:
                inner.rmdir()
            except OSError:
                return
        else:
            return


class DownloadCancelled(Exception):
    """用户取消下载"""


class ModelDownloader:
    """下载 + 解压，全部在调用线程里跑（由界面放进 QThread）"""

    def __init__(self, root: Path, mirror: str = 'github'):
        self.root = Path(root)
        self.mirror = mirror
        self._cancelled = False
        self._current: Optional[Path] = None

    def cancel(self) -> None:
        self._cancelled = True

    # ---------- 下载 ----------

    def _download(self, url: str, dest: Path,
                  on_progress: Callable[[int, int], None]) -> None:
        part = dest.with_suffix(dest.suffix + '.part')
        existing = part.stat().st_size if part.exists() else 0

        headers = {'User-Agent': 'QifeiIME/1.0'}
        if existing:
            headers['Range'] = f'bytes={existing}-'

        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=30) as response:
            total = int(response.headers.get('Content-Length') or 0)
            if response.status != 206:
                existing = 0
            total = total + existing if total else 0

            mode = 'ab' if existing else 'wb'
            with open(part, mode) as f:
                done = existing
                last_emit = 0.0
                while True:
                    if self._cancelled:
                        raise DownloadCancelled()
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    f.write(chunk)
                    done += len(chunk)
                    now = time.monotonic()
                    if now - last_emit > 0.15:
                        on_progress(done, total)
                        last_emit = now
                on_progress(done, total or done)

        os.replace(part, dest)

    # ---------- 解压 ----------

    def _extract(self, archive: Path, target_dir: Path,
                 on_progress: Callable[[int, int], None]) -> None:
        target_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as zf:
            members = zf.infolist()
            total = len(members) or 1
            for index, member in enumerate(members, 1):
                if self._cancelled:
                    raise DownloadCancelled()
                zf.extract(member, target_dir)
                if index % 20 == 0 or index == total:
                    on_progress(index, total)
        _normalize_nesting(target_dir)

    # ---------- 主流程 ----------

    def install(self, spec: ModelSpec,
                on_stage: Callable[[str], None] = lambda s: None,
                on_progress: Callable[[int, int], None] = lambda d, t: None,
                keep_archive: bool = True) -> Tuple[bool, str]:
        """安装一个引擎，返回 (是否成功, 说明)"""
        self.root.mkdir(parents=True, exist_ok=True)
        cache = self.root / '_packages'
        cache.mkdir(parents=True, exist_ok=True)

        for package in spec.packages:
            if self._cancelled:
                return False, '已取消'
            archive = cache / package.filename
            target_dir = self.root / package.target

            if not archive.exists() or archive.stat().st_size < package.size_mb * 1024 * 512:
                on_stage(f'正在下载 {package.filename}')
                try:
                    self._download(resolve_url(package.url, self.mirror), archive, on_progress)
                except DownloadCancelled:
                    return False, '已取消'
                except Exception as exc:  # 网络异常
                    return False, f'下载失败：{exc}'
            else:
                on_stage(f'已存在 {package.filename}，跳过下载')

            on_stage(f'正在解压 {package.filename}')
            try:
                self._extract(archive, target_dir, on_progress)
            except DownloadCancelled:
                return False, '已取消'
            except Exception as exc:
                return False, f'解压失败：{exc}'

            if not keep_archive:
                try:
                    archive.unlink()
                except OSError:
                    pass

        missing = missing_files(spec, self.root)
        if missing:
            return False, '文件不完整：' + '、'.join(m.split('/')[-1] for m in missing[:3])
        return True, '安装完成'
