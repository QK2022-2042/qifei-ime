"""
GGUF 文件信息读取

只读文件头，不加载权重，几百毫秒就能判断一个 .gguf 是什么模型、
多少参数量、什么量化、上下文多长。用来在界面里把「用户自己下载的模型文件」
显示成看得懂的名字，而不是一串文件名。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

GGUF_MAGIC = b'GGUF'

# 元数据值类型
_T_UINT8, _T_INT8, _T_UINT16, _T_INT16 = 0, 1, 2, 3
_T_UINT32, _T_INT32, _T_FLOAT32, _T_BOOL = 4, 5, 6, 7
_T_STRING, _T_ARRAY, _T_UINT64, _T_INT64, _T_FLOAT64 = 8, 9, 10, 11, 12

# general.file_type -> 量化名
_FILE_TYPES = {
    0: 'F32', 1: 'F16', 2: 'Q4_0', 3: 'Q4_1', 7: 'Q8_0', 8: 'Q5_0', 9: 'Q5_1',
    10: 'Q2_K', 11: 'Q3_K_S', 12: 'Q3_K_M', 13: 'Q3_K_L', 14: 'Q4_K_S',
    15: 'Q4_K_M', 16: 'Q5_K_S', 17: 'Q5_K_M', 18: 'Q6_K', 19: 'IQ2_XXS',
    20: 'IQ2_XS', 21: 'Q2_K_S', 22: 'IQ3_XS', 23: 'IQ3_XXS', 24: 'IQ1_S',
    25: 'IQ4_NL', 26: 'IQ3_S', 27: 'IQ3_M', 28: 'IQ2_S', 29: 'IQ2_M',
    30: 'IQ4_XS', 31: 'IQ1_M', 32: 'BF16', 36: 'TQ1_0', 37: 'TQ2_0',
    38: 'MXFP4',
}

# ggml_type -> 每个元素占用的字节（块量化按 每块字节/每块元素 折算）
_GGML_TYPE_BYTES = {
    0: 4.0, 1: 2.0, 2: 18 / 32, 3: 20 / 32, 6: 22 / 32, 7: 24 / 32,
    8: 34 / 32, 9: 36 / 32, 10: 84 / 256, 11: 110 / 256, 12: 144 / 256,
    13: 176 / 256, 14: 210 / 256, 15: 292 / 256, 16: 66 / 256,
    17: 74 / 256, 18: 98 / 256, 19: 50 / 256, 20: 18 / 32,
    21: 110 / 256, 22: 82 / 256, 23: 136 / 256, 24: 1.0, 25: 2.0,
    26: 4.0, 27: 8.0, 28: 8.0, 29: 56 / 256, 30: 2.0, 34: 1.0,
}

# 读取上限：只读元数据，正常几 MB 内就结束，防止误读大文件
_MAX_READ = 96 * 1024 * 1024


@dataclass
class GgufInfo:
    """一个 GGUF 文件的关键信息"""
    path: str
    name: str = ''
    arch: str = ''
    quant: str = ''
    context: int = 0
    params: int = 0
    size_bytes: int = 0
    error: str = ''
    has_chat_template: bool = False    # 有对话模板才是能润色/对话的 Instruct 模型

    @property
    def params_text(self) -> str:
        if self.params <= 0:
            return ''
        billion = self.params / 1e9
        if billion >= 1:
            return f'{billion:.1f}B'
        return f'{self.params / 1e6:.0f}M'

    @property
    def size_text(self) -> str:
        if self.size_bytes <= 0:
            return ''
        gb = self.size_bytes / (1024 ** 3)
        if gb >= 1:
            return f'{gb:.2f} GB'
        return f'{self.size_bytes / (1024 ** 2):.0f} MB'

    def summary(self) -> str:
        bits = [b for b in (self.params_text, self.quant) if b]
        return ' · '.join(bits) if bits else self.size_text


class _Reader:
    """带读取上限的小端读取器"""

    def __init__(self, handle):
        self._f = handle
        self._read = 0

    def take(self, size: int) -> bytes:
        if size < 0 or self._read + size > _MAX_READ:
            raise ValueError('元数据过大，已停止读取')
        data = self._f.read(size)
        if len(data) != size:
            raise ValueError('文件被截断')
        self._read += size
        return data

    def unpack(self, fmt: str):
        size = struct.calcsize(fmt)
        return struct.unpack(fmt, self.take(size))[0]

    def string(self) -> str:
        length = self.unpack('<Q')
        if length > 8 * 1024 * 1024:
            raise ValueError('字符串长度异常')
        return self.take(length).decode('utf-8', 'replace')

    def value(self, vtype: int, depth: int = 0) -> Any:
        if depth > 6:
            raise ValueError('嵌套过深')
        simple = {
            _T_UINT8: '<B', _T_INT8: '<b', _T_UINT16: '<H', _T_INT16: '<h',
            _T_UINT32: '<I', _T_INT32: '<i', _T_FLOAT32: '<f', _T_BOOL: '<?',
            _T_UINT64: '<Q', _T_INT64: '<q', _T_FLOAT64: '<d',
        }
        if vtype in simple:
            return self.unpack(simple[vtype])
        if vtype == _T_STRING:
            return self.string()
        if vtype == _T_ARRAY:
            item_type = self.unpack('<I')
            count = self.unpack('<Q')
            if count > 200000:
                raise ValueError('数组过大')
            return [self.value(item_type, depth + 1) for _ in range(count)]
        raise ValueError(f'未知元数据类型 {vtype}')


def read_info(path: str | Path) -> GgufInfo:
    """读取 GGUF 头部信息，失败时返回带 error 的对象而不是抛异常"""
    file_path = Path(path)
    info = GgufInfo(path=str(file_path), name=file_path.stem)
    try:
        info.size_bytes = file_path.stat().st_size
    except OSError as exc:
        info.error = f'无法读取文件：{exc}'
        return info

    try:
        with open(file_path, 'rb') as handle:
            reader = _Reader(handle)
            if reader.take(4) != GGUF_MAGIC:
                info.error = '不是 GGUF 文件'
                return info
            version = reader.unpack('<I')
            if version not in (1, 2, 3):
                info.error = f'不支持的 GGUF 版本 {version}'
                return info

            tensor_count = reader.unpack('<Q')
            kv_count = reader.unpack('<Q')
            if kv_count > 100000:
                info.error = '元数据条目异常'
                return info

            meta: Dict[str, Any] = {}
            for _ in range(kv_count):
                key = reader.string()
                vtype = reader.unpack('<I')
                meta[key] = reader.value(vtype)

            info.arch = str(meta.get('general.architecture') or '')
            info.name = str(meta.get('general.name') or info.name)

            # 有没有对话模板是「能不能用来润色」的硬指标：ASR / embedding /
            # 纯补全模型通常没有 tokenizer.chat_template，选了只会吐转写格式。
            template = (meta.get('tokenizer.chat_template') or
                        meta.get('general.chat_template') or
                        meta.get('chat_template'))
            info.has_chat_template = (
                isinstance(template, str) and bool(template.strip()))

            file_type = meta.get('general.file_type')
            if isinstance(file_type, int):
                info.quant = _FILE_TYPES.get(file_type, f'T{file_type}')

            ctx_key = f'{info.arch}.context_length' if info.arch else ''
            if ctx_key and isinstance(meta.get(ctx_key), int):
                info.context = int(meta[ctx_key])

            # 参数量：优先用文件里声明的，没有就把张量维度乘起来
            declared = meta.get('general.parameter_count')
            if isinstance(declared, int) and declared > 0:
                info.params = declared
            else:
                info.params = _sum_params(reader, tensor_count)
    except Exception as exc:              # 损坏文件、权限问题等
        info.error = str(exc)
    return info


def _sum_params(reader: _Reader, tensor_count: int) -> int:
    """把张量维度全部相乘得到参数量（只读索引，不读权重）"""
    if tensor_count <= 0 or tensor_count > 200000:
        return 0
    total = 0
    for _ in range(tensor_count):
        reader.string()                    # 名称
        n_dims = reader.unpack('<I')
        if n_dims > 8:
            return total
        elements = 1
        for _ in range(n_dims):
            elements *= reader.unpack('<Q')
        reader.unpack('<I')                # ggml type
        reader.unpack('<Q')                # offset
        total += elements
    return total


def looks_like_gguf(path: str | Path) -> bool:
    """只看前 4 个字节，用于快速过滤"""
    try:
        with open(path, 'rb') as handle:
            return handle.read(4) == GGUF_MAGIC
    except OSError:
        return False
