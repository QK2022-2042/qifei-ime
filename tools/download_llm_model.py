# coding: utf-8
"""人工重新下载 / 校验安装包内置的中文润色模型（GGUF）

安装包已经自带 models/llm/ 下的推荐模型，第一次装完就能用，程序不会自动下载。
本工具只用于人工操作：模型损坏时恢复出厂模型、给旧版本补模型、或离线分发前
重新取一份。想换其他模型，直接把 .gguf 拷进 models/llm/ 即可。

下载逻辑在 llmkit/model_download.py，设置中心「大模型」页的
「下载推荐润色模型」按钮用的是同一套代码，命令行与界面行为完全一致。

模型放进 models/llm/ 后会被自动识别并设为默认，设置页与听写客户端都能
直接用，不需要 Ollama、不需要联网推理、不需要手工选文件。

用法：
    python tools/download_llm_model.py            # 已有有效模型时直接跳过
    python tools/download_llm_model.py --force   # 人工覆盖重下（忽略已有文件）
    python tools/download_llm_model.py --check   # 只校验已内置的模型
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from llmkit import model_download  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description='下载中文润色模型（GGUF）')
    parser.add_argument('--force', action='store_true', help='忽略已有文件重新下载')
    parser.add_argument('--check', action='store_true', help='只校验，不下载')
    args = parser.parse_args()

    dest = model_download.target()
    if args.check:
        print(f'检查：{dest}')
        ok, message = model_download.validate(dest)
        print(message)
        return 0 if ok else 1

    ok, message = model_download.download(
        force=args.force, on_stage=lambda text: print(text, flush=True))
    print(message)
    if ok:
        print('\n模型已就绪。打开设置中心 →「大模型」会自动选中；')
        print('听写客户端下次启动会自动预热，说话时直接可用。')
        print(f'自检：python tests/test_llm_real_model.py "{dest}"')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
