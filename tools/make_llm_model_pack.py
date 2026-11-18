# coding: utf-8
"""把推荐润色模型打成独立补包，供完全离线的机器解压即用。

主安装包不含模型（installer/build_installer.py 里 skip={'models', 'logs'}），
完全离线的机器把这个 zip 解压到安装目录，文件就会落到 models/llm/，
被自动识别并设为默认模型。GGUF 已经量化过，再压缩省不了多少，
这里用 ZIP_STORED：打包快、解压也快，CRC 仍由 zip 自己保证。

用法：
    python tools/make_llm_model_pack.py
    python tools/make_llm_model_pack.py --check     # 只校验已有补包
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llmkit import model_download  # noqa: E402

PACK_NAME = '启飞输入法-LLM润色模型包.zip'
OUT = ROOT / 'dist' / PACK_NAME


def arcname() -> str:
    return f'models/llm/{model_download.target().name}'


def build() -> int:
    src = model_download.target()
    ok, message = model_download.validate(src)
    if not ok:
        sys.exit(f'模型不可用，拒绝打补包：{message}')
    print(f'源文件：{src}')
    print(f'校验：{message}')

    OUT.parent.mkdir(parents=True, exist_ok=True)
    if OUT.exists():
        OUT.unlink()
    print(f'正在生成：{OUT}')
    with zipfile.ZipFile(OUT, 'w', zipfile.ZIP_STORED, allowZip64=True) as zf:
        zf.write(src, arcname())
    print(f'补包大小：{OUT.stat().st_size / 1024 ** 3:.2f} GB')
    return check()


def check() -> int:
    if not OUT.is_file():
        print(f'补包不存在：{OUT}')
        return 1
    src = model_download.target()
    with zipfile.ZipFile(OUT) as zf:
        names = zf.namelist()
        if names != [arcname()]:
            print(f'补包条目不对：{names}')
            return 1
        info = zf.getinfo(arcname())
        if info.file_size != src.stat().st_size:
            print(f'大小不符：zip 内 {info.file_size}，源文件 {src.stat().st_size}')
            return 1
        # testzip 会读完整文件校验 CRC，1GB 也就几秒
        bad = zf.testzip()
        if bad:
            print(f'CRC 校验失败：{bad}')
            return 1
    print(f'补包校验通过：{PACK_NAME} → {arcname()}')
    print('离线机器把它解压到安装目录即可，推理全程离线。')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='生成 LLM 润色模型独立补包')
    parser.add_argument('--check', action='store_true', help='只校验已有补包，不重新生成')
    args = parser.parse_args()
    return check() if args.check else build()


if __name__ == '__main__':
    sys.exit(main())
