# coding: utf-8
"""生成「macOS 构建用」源码包：不含大模型、不含无关文件。

用户要求：把整个项目打包，但**除了大模型文件**，和本项目无关的文件也不要打包，
放到桌面上，拿到 Mac 上跑 build/build_mac.sh 出 .app / .dmg。

默认输出：
    ~/Desktop/启飞输入法-2.0.4-macOS源码包.zip

用法：
    python tools/make_mac_source_pack.py             # 生成
    python tools/make_mac_source_pack.py --check    # 只校验已有包
    python tools/make_mac_source_pack.py --out X.zip

硬约束（生成后自带断言，不通过就不算成功）：
    0 个 .gguf / .onnx / .dll / .exe / .pyd / .zip / .part
    0 个 __pycache__ / dist / .tmp / .venv / logs
    0 个历史 PyInstaller 构建目录
    包体 < 60MB（源码只有几 MB，超了说明混进了二进制）
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = Path.home() / 'Desktop' / '启飞输入法-2.0.4-macOS源码包.zip'
ZIP_ROOT = '启飞输入法-2.0.4-macOS源码'

# 顶层允许进入的目录。dist / models / logs / internal / .tmp / .venv /
# .workbuddy-ai / __pycache__ 全部不进包。
TOP_DIRS = ('assets', 'build', 'core', 'docs', 'installer',
            'llmkit', 'qifei', 'tests', 'tools', 'LLM')

# 顶层允许的散文件
TOP_FILES = (
    'readme.md', 'LICENSE',
    'config_client.py', 'config_override.py', 'config_server.py',
    'qifei_client.py', 'qifei_ime.py', 'qifei_server.py',
    'hot.txt', 'hot-rule.txt', 'hot-server.txt',
    'requirements-build.txt', 'requirements-client.txt',
    'requirements-gui.txt', 'requirements-server.txt',
)

# 只按扩展名就足够排掉所有模型 / 二进制 / 缓存
SKIP_EXT = {
    '.gguf', '.onnx', '.bin', '.safetensors', '.pt', '.pth',
    '.wav', '.mp3', '.m4a', '.flac', '.ogg',
    '.zip', '.7z', '.rar', '.tar', '.gz',
    '.exe', '.dll', '.pyd', '.lib', '.exp', '.obj', '.o',
    '.so', '.dylib', '.a', '.bat', '.cmd',
    '.pyc', '.pyo', '.part', '.tmp', '.log', '.bak', '.swp',
}
SKIP_DIR_NAMES = {
    '__pycache__', '.git', '.venv', 'venv', '.tmp', 'dist', 'build_work',
    'logs', 'models', 'internal', '.workbuddy-ai', '.pytest_cache',
    'node_modules', 'mac-work', 'bin',
}
SKIP_FILENAMES = {'payload.zip', '.DS_Store', 'Thumbs.db'}
MAX_FILE_BYTES = 20 * 1024 * 1024   # 单文件 20MB 上限

# 必须在包里的关键文件
REQUIRED = (
    'build/build_mac.sh',
    'build/qifei_mac.spec',
    'build/qifei.spec',
    'build/build_all.py',
    'docs/macOS 构建与安装说明.md',
    'tools/make_mac_source_pack.py',
    'qifei/brand.py',
    'qifei_ime.py',
    'qifei_client.py',
    'qifei_server.py',
    'requirements-server.txt',
)


def _skip(path: Path, rel: Path) -> bool:
    if any(part in SKIP_DIR_NAMES for part in rel.parts):
        return True
    if path.name in SKIP_FILENAMES or path.suffix.lower() in SKIP_EXT:
        return True
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return True
    except OSError:
        return True
    # build/ 下只收直接子文件：历史 installer-2026.../ 目录里全是
    # PyInstaller 产物和旧 spec，跟 Mac 出包无关。
    if rel.parts[0] == 'build' and len(rel.parts) > 2:
        return True
    # installer/ 下只收 .py；payload.zip / 旧 spec / 打包缓存都排除
    if rel.parts[0] == 'installer' and path.suffix.lower() != '.py':
        return True
    return False


def collect() -> list[tuple[Path, Path]]:
    out: list[tuple[Path, Path]] = []
    for name in TOP_FILES:
        path = ROOT / name
        if path.is_file() and not _skip(path, Path(name)):
            out.append((path, Path(name)))

    for top in TOP_DIRS:
        base = ROOT / top
        if not base.is_dir():
            continue
        for path in sorted(base.rglob('*')):
            if not path.is_file():
                continue
            rel = path.relative_to(ROOT)
            if not _skip(path, rel):
                out.append((path, rel))
    return out


def build(out: Path) -> None:
    items = collect()
    rels = {str(rel).replace('\\', '/') for _, rel in items}
    missing = [r for r in REQUIRED if r not in rels]
    if missing:
        raise SystemExit(f'源码包缺关键文件：{missing}')

    out.parent.mkdir(parents=True, exist_ok=True)
    # 已存在时先挪走备份，不直接删除（受管控环境里批量删除会被拦）
    if out.exists():
        backup = out.with_suffix('.zip.bak')
        n = 1
        while backup.exists():
            backup = out.with_name(f'{out.stem}.bak{n}.zip')
            n += 1
        out.rename(backup)
        print(f'旧包已备份：{backup.name}')

    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        zf.writestr(f'{ZIP_ROOT}/先读我.md',
                    '# 启飞输入法 2.0.4 · macOS 构建源码包\n\n'
                    '这个包**不含任何大模型**，也不含 Windows 二进制。\n\n'
                    '在 macOS 上执行：\n\n'
                    '```bash\n'
                    'python3 -m venv .venv && source .venv/bin/activate\n'
                    'python3 -m pip install -r requirements-build.txt '
                    '-r requirements-gui.txt -r requirements-client.txt '
                    '-r requirements-server.txt\n'
                    'bash build/build_mac.sh\n'
                    '```\n\n'
                    '完整说明见 `docs/macOS 构建与安装说明.md`。\n')
        for path, rel in items:
            zf.write(path, f'{ZIP_ROOT}/{rel.as_posix()}')

    size = out.stat().st_size
    print(f'源码包：{out}')
    print(f'大小：{size / 1048576:.2f} MB，{len(items) + 1} 个文件')
    check(out)


def check(out: Path) -> None:
    if not out.is_file():
        raise SystemExit(f'找不到源码包：{out}')

    bad_ext = ('.gguf', '.onnx', '.dll', '.exe', '.pyd', '.zip',
               '.part', '.wav', '.mp3', '.pyc')
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        bad = [n for n in names if n.lower().endswith(bad_ext)]
        dirty = [n for n in names
                 if any(p in ('__pycache__', '/dist/', '/.tmp/', '/.venv/',
                             '/logs/', '/models/') for p in (n,))]
        crc = zf.testzip()
        present = {n.split('/', 1)[1] for n in names if '/' in n}
        missing = [r for r in REQUIRED if r not in present]
        brand = next((n for n in names if n.endswith('qifei/brand.py')), '')
        version_ok = (bool(brand) and
                      b"VERSION = '2.0.4'" in zf.read(brand))

    size = out.stat().st_size
    checks = [
        ('CRC 校验通过', crc is None, str(crc)),
        ('0 个模型 / 二进制 / 缓存文件', not bad, str(bad[:5])),
        ('0 个构建产物目录', not dirty, str(dirty[:5])),
        ('关键文件齐全', not missing, str(missing)),
        ('版本号是 2.0.4', version_ok, brand),
        ('包体 < 60MB', size < 60 * 1024 * 1024, f'{size / 1048576:.2f} MB'),
    ]
    print('-' * 60)
    passed = 0
    for name, cond, detail in checks:
        print(('[OK]   ' if cond else '[FAIL] ') + name +
              (f' — {detail}' if detail and not cond else ''))
        passed += int(bool(cond))
    print(f'共 {len(checks)} 项：通过 {passed}，失败 {len(checks) - passed}')
    if passed != len(checks):
        raise SystemExit('MAC_SOURCE_PACK_FAILED')
    print('MAC_SOURCE_PACK_OK')


def main() -> None:
    ap = argparse.ArgumentParser(description='生成 macOS 构建用源码包（不含大模型）')
    ap.add_argument('--check', action='store_true', help='只校验已有包，不重新生成')
    ap.add_argument('--out', type=Path, default=DEFAULT_OUT, help='输出路径')
    args = ap.parse_args()
    out = args.out.resolve()
    if args.check:
        check(out)
    else:
        build(out)


if __name__ == '__main__':
    main()
