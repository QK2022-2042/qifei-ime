# coding: utf-8
"""
构建脚本：把源码打成三个 exe，并可选地生成一键安装包

用法：
    python build/build_all.py                # 全部（界面 + 客户端 + 服务端）
    python build/build_all.py --target gui   # 只打界面，便于快速验证
    python build/build_all.py --installer    # 打完后再生成 Setup.exe

前置：
    pip install -r requirements-gui.txt -r requirements-client.txt -r requirements-server.txt
    pip install -r requirements-build.txt

可选：
    set QIFEI_LLAMA_MINIMAL=1 再构建，可打「纯 CPU 精简包」——去掉
    ggml-vulkan.dll（约 58MB）。默认保留 GPU 后端，gpu_layers > 0 才能用。
    set QIFEI_BUNDLE_MODEL=0 可退回「主包不带模型」的旧策略（做小包时用）。
    默认把推荐中文润色模型打进包，用户第一次安装就有大模型可用。
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'
BUILD = ROOT / 'build'
APP_DIR = DIST / 'QifeiIME'

# 必须与 llmkit/registry.py::BUNDLED_CHAT_MODEL 保持一致；
# tools/verify_release.py 会解析源码做一致性校验，改错名字发版自检会报错。
BUNDLED_MODEL = 'qwen2.5-1.5b-instruct-q4_k_m.gguf'
MODEL_SRC = ROOT / 'models' / 'llm' / BUNDLED_MODEL


def bundle_chat_model() -> None:
    """把推荐中文润色模型放进绿色版目录，安装包会随 payload 一起带上。

    只在文件缺失或大小不符时复制，重复构建（--no-clean）不会反复拷 1GB。
    """
    if os.environ.get('QIFEI_BUNDLE_MODEL', '1') == '0':
        print('已按 QIFEI_BUNDLE_MODEL=0 跳过内置模型')
        return
    if not MODEL_SRC.is_file():
        sys.exit(f'找不到推荐润色模型：{MODEL_SRC}\n'
                 f'请先把它放到 models/llm/，或用 QIFEI_BUNDLE_MODEL=0 明确不带模型')
    with MODEL_SRC.open('rb') as f:
        if f.read(4) != b'GGUF':
            sys.exit(f'内置模型不是合法 GGUF 文件：{MODEL_SRC}')

    dst = APP_DIR / 'models' / 'llm' / BUNDLED_MODEL
    if dst.is_file() and dst.stat().st_size == MODEL_SRC.stat().st_size:
        print(f'内置模型已就位：{BUNDLED_MODEL}')
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(MODEL_SRC, dst)
    print(f'内置模型：{BUNDLED_MODEL}  {dst.stat().st_size / 1048576:.1f} MB')


def run(cmd: list[str], env: dict | None = None) -> None:
    print('>', ' '.join(cmd))
    merged = os.environ.copy()
    if env:
        merged.update(env)
    result = subprocess.run(cmd, cwd=ROOT, env=merged)
    if result.returncode != 0:
        sys.exit(f'命令失败，退出码 {result.returncode}')


def clean() -> None:
    """把上一次的产物挪到 .tmp/trash，而不是直接删掉

    直接 rmtree 一个几百上千文件的目录，会触发文件系统的批量删除保护，
    构建中途被打断。改名是原子操作，又快又安全，也方便出问题时回看。
    """
    trash = ROOT / '.tmp' / 'trash'
    stamp = time.strftime('%Y%m%d-%H%M%S')
    stale = [APP_DIR] + sorted(p for p in BUILD.glob('qifei-*') if p.is_dir())
    for path in stale:
        if not path.exists():
            continue
        trash.mkdir(parents=True, exist_ok=True)
        target = trash / f'{path.name}-{stamp}'
        try:
            path.rename(target)
        except OSError:
            shutil.rmtree(path, ignore_errors=True)


def build(target: str) -> None:
    started = time.time()
    # 每次构建用独立的工作目录，避免覆盖上一次的中间产物
    # （覆盖已存在的 .pyc 在部分受管控的环境里会被拦下）
    work = BUILD / f'qifei-{time.strftime("%Y%m%d-%H%M%S")}'
    run([sys.executable, '-m', 'PyInstaller', '--noconfirm',
         '--workpath', str(work),
         '--distpath', str(DIST),
         str(BUILD / 'qifei.spec')], env={'QIFEI_BUILD_TARGET': target})
    print(f'\n构建耗时 {time.time() - started:.0f}s')


def prepare_runtime_files() -> None:
    """补齐运行期需要的目录与用户可编辑文件

    打包后 BASE_DIR 指向 exe 所在目录，热词、LLM 角色这些用户会自己改的文件
    必须放在这一层，而不是藏在 _internal 里。
    """
    if not APP_DIR.exists():
        return
    # models/llm 提前建好：内置模型落这里，用户以后人工换模型也拷到这里。
    for name in ('models', 'models/llm', 'logs'):
        (APP_DIR / name).mkdir(parents=True, exist_ok=True)

    bundle_chat_model()

    for name in ('hot.txt', 'hot-rule.txt', 'hot-server.txt'):
        src = ROOT / name
        if src.exists():
            shutil.copy2(src, APP_DIR / name)

    llm_src = ROOT / 'LLM'
    if llm_src.is_dir():
        llm_dst = APP_DIR / 'LLM'
        if not llm_dst.exists():
            shutil.copytree(llm_src, llm_dst)

    assets_src = ROOT / 'assets'
    if assets_src.is_dir():
        assets_dst = APP_DIR / 'assets'
        if not assets_dst.exists():
            shutil.copytree(assets_src, assets_dst)

    readme = ROOT / 'docs' / '启飞输入法使用说明.md'
    if readme.exists():
        shutil.copy2(readme, APP_DIR / '使用说明.md')


def build_installer() -> None:
    script = ROOT / 'installer' / 'build_installer.py'
    run([sys.executable, str(script)])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--target', default='all', choices=['all', 'gui', 'client', 'server'])
    parser.add_argument('--installer', action='store_true', help='同时生成一键安装包')
    parser.add_argument('--no-clean', action='store_true')
    args = parser.parse_args()

    if not args.no_clean:
        clean()

    build(args.target)
    prepare_runtime_files()

    if args.installer:
        build_installer()

    print('\n产物目录：', APP_DIR)
    if APP_DIR.exists():
        for item in sorted(APP_DIR.glob('*.exe')):
            print('  -', item.name, f'{item.stat().st_size / 1048576:.1f} MB')


if __name__ == '__main__':
    main()
