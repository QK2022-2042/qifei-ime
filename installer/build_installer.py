# coding: utf-8
"""
生成一键安装包

把 dist/QifeiIME 整个目录压缩成 payload.zip，
再用 PyInstaller 把安装器与 payload 打成一个 Setup exe。

产物：dist/启飞输入法-2.0.4-安装包.exe

v2.15 两处调整（针对「下载时被杀毒软件拦截」）：
  1. Setup.exe 补 VS_VERSION_INFO 版本资源（公司/产品/版本/版权），去掉
     「无厂商、无版本、无描述」这个启发式减分项；
  2. payload 排除纯开发期产物（导入库 / 调试符号 / 临时残留），并把
     非 GGUF 文件的 deflate 级别从 6 提到 9。GGUF 仍用 ZIP_STORED。
注意：这些都是零功能影响的无损瘦身，不移除内置模型、不删任何运行时 DLL。
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'
APP_DIR = DIST / 'QifeiIME'
INSTALLER_DIR = ROOT / 'installer'
PAYLOAD = INSTALLER_DIR / 'payload.zip'

BRAND = '启飞输入法'
VERSION = '2.0.4'

# 必须与 llmkit/registry.py::BUNDLED_CHAT_MODEL 保持一致；
# tools/verify_release.py 会解析源码做一致性校验。
BUNDLED_MODEL = 'qwen2.5-1.5b-instruct-q4_k_m.gguf'
BUNDLED_MODEL_REL = f'models/llm/{BUNDLED_MODEL}'


def make_payload() -> None:
    if not APP_DIR.exists():
        sys.exit(f'找不到应用目录：{APP_DIR}\n请先运行 build/build_all.py')
    if PAYLOAD.exists():
        # 与 build_all.py::clean() 一致：挪走而不是删除，
        # 1GB 级文件直接 unlink 在受管控环境里可能被批量删除保护拦下。
        trash = ROOT / '.tmp' / 'trash'
        trash.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime('%Y%m%d-%H%M%S')
        backup = trash / f'payload-{stamp}.zip'
        index = 1
        while backup.exists():
            index += 1
            backup = trash / f'payload-{stamp}-{index}.zip'
        PAYLOAD.rename(backup)

    # 日志不进包。models/ 下只收「内置推荐模型」这一个文件：
    # 源码 models/ 里还有 ASR 权重、下载缓存、示例脚本，绝不能整目录带走。
    skip = {'logs'}

    # v2.15 无损瘦身：链接期导入库 / 调试符号 / 下载残留这类文件运行时
    # 绝不会被加载，却白白增加 payload 条目数（也增加杀软扫包时要看的
    # PE 数量）。只按后缀排除这几个确定无用的类型；任何 .dll / .pyd /
    # .exe / .gguf 一律保留，不碰内置模型，不碰 GPU 后端。
    SKIP_SUFFIXES = {'.lib', '.a', '.exp', '.pdb', '.ilk', '.idb',
                     '.pyc', '.pyo', '.bak', '.old', '.tmp', '.part', '.log'}
    skipped_dev = 0

    print('正在压缩应用目录…')
    count = 0
    # GGUF 是量化权重，单独用 ZIP_STORED；其余文件用更高压缩级别，
    # 对 300 多个 DLL / PYD 再挤出一点体积，代价只是打包慢几十秒。
    with zipfile.ZipFile(PAYLOAD, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for path in APP_DIR.rglob('*'):
            if not path.is_file():
                continue
            rel = path.relative_to(APP_DIR)
            parts = rel.parts
            if parts and parts[0] in skip:
                continue
            if rel.suffix.lower() in SKIP_SUFFIXES:
                skipped_dev += 1
                continue
            if parts and parts[0] == 'models':
                # 白名单：只有内置模型进包，其余模型文件一律排除
                if rel.as_posix() != BUNDLED_MODEL_REL:
                    continue
                # GGUF 已经量化过，deflate 几乎压不动还要白等几分钟
                zf.write(path, rel, compress_type=zipfile.ZIP_STORED)
                print(f'内置模型：{BUNDLED_MODEL_REL}  '
                      f'{path.stat().st_size / 1048576:.1f} MB（ZIP_STORED）')
            else:
                zf.write(path, rel)
            count += 1

    with zipfile.ZipFile(PAYLOAD) as zf:
        if BUNDLED_MODEL_REL not in zf.namelist():
            sys.exit(f'内置模型没有进 payload：{BUNDLED_MODEL_REL}\n'
                     f'检查 build_all.py 是否用了 QIFEI_BUNDLE_MODEL=0')
        gguf = [n for n in zf.namelist() if n.lower().endswith('.gguf')]
        if gguf != [BUNDLED_MODEL_REL]:
            sys.exit(f'payload 里的模型清单不对：{gguf}')
    print(f'payload.zip = {PAYLOAD.stat().st_size / 1048576:.1f} MB（{count} 个文件）')
    if skipped_dev:
        print(f'已排除 {skipped_dev} 个开发期 / 临时文件（无损瘦身）')


def build_setup() -> Path:
    out_name = f'{BRAND}-{VERSION}-安装包'
    work = ROOT / 'build' / f'installer-{time.strftime("%Y%m%d-%H%M%S")}'
    cmd = [
        sys.executable, '-m', 'PyInstaller',
        '--noconfirm', '--onefile', '--windowed',
        '--name', out_name,
    ]
    # 以前没传 --icon，Setup.exe 用的是 PyInstaller 默认图标，跟产品图标
    # 完全无关；换成产品主图标，装之前在资源管理器里看到的就是同一个标识。
    icon = ROOT / 'assets' / 'icon.ico'
    if icon.is_file():
        cmd += ['--icon', str(icon)]
    # v2.15：补 PE 版本资源。未签名 exe 没有公司名 / 产品名 / 版本号时，
    # 信誉评分天然吃亏；这是不改动功能就能去掉的误报减分项。
    version_file = INSTALLER_DIR / 'file_version_info.txt'
    if version_file.is_file():
        cmd += ['--version-file', str(version_file)]
        print(f'Setup 版本资源：{version_file.name}')
    cmd += [
        '--add-data', f'{PAYLOAD}{os.pathsep}.',
        '--distpath', str(DIST),
        '--workpath', str(work),
        '--specpath', str(work),
        str(INSTALLER_DIR / 'installer_app.py'),
    ]
    print('>', ' '.join(cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        sys.exit('安装包构建失败')

    exe = DIST / f'{out_name}.exe'
    print(f'\n安装包：{exe}  ({exe.stat().st_size / 1048576:.1f} MB)')
    return exe


def main() -> None:
    make_payload()
    build_setup()


if __name__ == '__main__':
    main()
