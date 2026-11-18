# coding: utf-8
"""把 Windows exe 安装包压缩打包，放到桌面。

用户诉求：
    「最后生成 exe 版本的安装包之后对安装包进行压缩打包，放在桌面上。」

为什么要多一层 zip（实测结论，不是想当然）：给 1.2GB 的 Setup.exe 再套
zip，deflate6 只省 0.25MB / 0.02%，几乎不减小体积 —— Setup.exe 内部已经
是 PyInstaller onefile（zlib 压过一遍），内嵌 payload.zip 里的 1.04GB
GGUF 又是 ZIP_STORED 的已量化权重，压不动。这层 zip 的价值是**分发形态**：
浏览器 / 邮件网关 / 网盘对「下载裸 exe」会做即时阻断，对 zip 通常只在解压
时检查；用户主动解压后再自行运行，能明显减少「下载时被杀毒软件拦截」。

包里除 exe 外还放两份纯文本：
  - SHA256SUMS.txt  标准校验清单，方便 sha256sum -c / certutil 核对
  - 校验与安装说明.txt  版本 / 大小 / 哈希 + 误报原因与处理办法

说明文档里的 payload 文件数从 installer/payload.zip **动态读取**，不写死：
2.0.4 做过无损瘦身（排除开发期 / 临时文件），实际是 312 个，而 2.0.3 基线
是 314；写死数字曾让已交付的压缩包带着旧数字。--check 会断言文档数字与
构建产物一致，避免以后版本升级再次脱节。

用法：
    python tools/make_setup_zip.py             # 生成到桌面
    python tools/make_setup_zip.py --check     # 只校验已有压缩包
    python tools/make_setup_zip.py --out D:\\x.zip
"""
from __future__ import annotations

import argparse
import hashlib
import os
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / 'dist'
BUILD_INSTALLER = ROOT / 'installer' / 'build_installer.py'
PAYLOAD = ROOT / 'installer' / 'payload.zip'
DEFAULT_OUT = Path.home() / 'Desktop'

EXE_ENTRY = '{setup_name}'
SUMS_NAME = 'SHA256SUMS.txt'
README_NAME = '校验与安装说明.txt'


def installer_version() -> str:
    text = BUILD_INSTALLER.read_text(encoding='utf-8')
    m = re.search(r"^VERSION\s*=\s*'([^']+)'", text, re.M)
    if not m:
        sys.exit('读不到 installer/build_installer.py 的 VERSION')
    return m.group(1)


def find_setup(version: str) -> Path:
    setup = DIST / f'启飞输入法-{version}-安装包.exe'
    if not setup.is_file():
        sys.exit(f'找不到安装包：{setup}\n请先运行 build/build_all.py --installer')
    return setup


def payload_entry_count() -> int | None:
    """真读 installer/payload.zip 的条目数，供说明文档动态引用。

    版本升级、无损瘦身都会改变条目数，写死必然脱节。payload.zip 不在时
    （例如只单独跑分发脚本、没有构建产物）返回 None，说明文档退化为不带
    数字的措辞，绝不编造一个数字。
    """
    if not PAYLOAD.is_file():
        return None
    with zipfile.ZipFile(PAYLOAD) as zf:
        return len(zf.namelist())


def sha256(path: Path, chunk: int = 4 << 20) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for buf in iter(lambda: f.read(chunk), b''):
            h.update(buf)
    return h.hexdigest()


def out_path(version: str, explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    desktop = DEFAULT_OUT
    if not desktop.is_dir():
        desktop = Path.home()
    return desktop / f'启飞输入法-{version}-安装包-压缩版.zip'


def readme_text(version: str, setup: Path, digest: str,
               payload_count: int | None) -> str:
    size = setup.stat().st_size
    if payload_count is None:
        payload_line = ('安装包内容已做只读体检：payload 全部为程序本体、运行时依赖、\n'
                        '随包资源与内置模型，无任何日志 / 缓存 / 备份 / 临时文件夹带；')
    else:
        payload_line = (f'安装包内容已做只读体检：payload {payload_count} 个文件'
                        '全部为程序本体、运行时依赖、\n'
                        '随包资源与内置模型，无任何日志 / 缓存 / 备份 / 临时文件夹带；')
    return f"""启飞输入法 {version} · Windows 安装包（压缩分发版）

============================================================
一、这个文件是什么
============================================================
压缩包里的  {setup.name}
就是完整的 Windows 一键安装包，双击即可安装，不需要管理员权限。
已内置中文润色大模型 qwen2.5-1.5b-instruct-q4_k_m.gguf（约 1.04GB），
第一次装完打开「大模型」页就能用，不用联网下载、不用装 Ollama。

文件信息
  版本      ：{version}
  文件名    ：{setup.name}
  大小      ：{size} bytes（{size / 1048576:.2f} MB）
  SHA-256   ：{digest}

校验方法（任选其一，哈希对不上就别运行）
  PowerShell ： Get-FileHash .\\{setup.name} -Algorithm SHA256
  命令行     ： certutil -hashfile {setup.name} SHA256
  sha256sum  ： sha256sum -c {SUMS_NAME}

============================================================
二、为什么是 zip，而不是直接给 exe
============================================================
Setup.exe 是 PyInstaller 打包的未签名自解压安装程序，体积约 1.2GB。
部分杀毒软件 / 浏览器下载保护会对「直接下载无签名的 exe」做即时阻断。
压缩成 zip 后，传输与下载阶段拿到的是压缩包，由你主动解压再运行。

注意：这层 zip **不是为了减小体积**。实测给 Setup.exe 再套 zip 只能省
约 0.25MB（0.02%）——包内程序已经压过一层，内置的 GGUF 是已量化权重，
压不动。它的作用是改变分发形态，以及附带下面的校验信息。

============================================================
三、如果杀毒软件仍然报警
============================================================
这是启发式误报的高发组合，每一项都是安装器的正常行为：
  · 未做 Authenticode 代码签名（个人/小团队没有证书）
  · PyInstaller onefile：运行时把程序解压到系统临时目录
  · 安装时 taskkill 结束正在运行的旧版输入法（否则 exe 被占用无法覆盖）
  · 用 PowerShell 建桌面 / 开始菜单快捷方式
  · 写 HKCU 卸载信息，生成卸载脚本（含 rmdir / reg delete）
  · 包内含多个 exe / dll（这是输入法本体，不是夹带）

建议按顺序处理：
  1. 先用上面的 SHA-256 核对文件完整性；
  2. 确认无误后把该文件加入杀毒软件白名单 / 信任区，再解压运行；
  3. 也可把哈希提交给杀软厂商做误报申报；
  4. 根治办法是给 exe 做代码签名，需要有效的代码签名证书。

{payload_line}
Setup.exe 内嵌的 payload.zip 与构建产物逐字节一致。

============================================================
四、覆盖安装与模型保护
============================================================
覆盖安装不会冲掉你人工换过或下载过的模型（models/ 下已存在的文件一律
跳过），程序本体照常升级。后续想升级 / 换模型，请把新的 .gguf 拷进安装
目录 models\\llm\\，或在「大模型」页点「选择模型文件（已下载好的）」人工
导入；程序不会自动联网下载或替换模型。
"""


def build(out: Path, setup: Path, version: str) -> None:
    digest = sha256(setup)
    out.parent.mkdir(parents=True, exist_ok=True)

    count = payload_entry_count()
    if count is not None:
        print(f'payload.zip 实测 {count} 个文件（说明文档按此动态生成）')
    sums = f'{digest}  {setup.name}\n'
    readme = readme_text(version, setup, digest, count)

    # 先写同目录临时文件：1.2GB 压缩约 1 分钟，中途失败也不会留下半截 zip
    # 盖掉已交付的旧包；写完后 os.replace 在本卷原子替换。
    tmp = out.with_name(out.name + '.tmp')
    if tmp.exists():
        tmp.unlink()
    print(f'正在压缩 {setup.name} …（1.2GB，约需 1 分钟）')
    t0 = time.time()
    with zipfile.ZipFile(tmp, 'w', zipfile.ZIP_DEFLATED,
                         compresslevel=6, allowZip64=True) as zf:
        zf.write(setup, setup.name)
        zf.writestr(SUMS_NAME, sums.encode('utf-8'))
        zf.writestr(README_NAME, readme.encode('utf-8'))
    dt = time.time() - t0

    # 旧包先在本目录改名（同卷 os.rename 必定成功），新产品原子就位之后
    # 再把 .old 归档。桌面多在 C:、项目在 D:，直接把旧包 rename 到 D 盘会
    # 抛 WinError 17「系统无法将文件移到不同的磁盘驱动器」（2.0.4 重出压缩
    # 包时实测踩到）；shutil.move 跨文件系统时自动退化为 copy2 + unlink。
    old = out.with_name(out.name + '.old')
    if old.exists():
        old.unlink()
    if out.exists():
        out.rename(old)
    os.replace(tmp, out)

    if old.exists():
        trash = ROOT / '.tmp' / 'trash'
        trash.mkdir(parents=True, exist_ok=True)
        backup = trash / f'{out.stem}-{time.strftime("%Y%m%d-%H%M%S")}.zip'
        i = 1
        while backup.exists():
            i += 1
            backup = trash / f'{out.stem}-{time.strftime("%Y%m%d-%H%M%S")}-{i}.zip'
        shutil.move(str(old), str(backup))
        print(f'旧压缩包已归档：{backup.name}')
    src = setup.stat().st_size
    dst = out.stat().st_size
    print(f'生成：{out}')
    print(f'  源 exe : {src} bytes（{src / 1048576:.2f} MB）')
    print(f'  压缩包 : {dst} bytes（{dst / 1048576:.2f} MB）')
    print(f'  差值   : {src - dst} bytes（{(src - dst) / src * 100:.3f}%）'
          f'  耗时 {dt:.1f}s')
    print(f'  SHA256 : {digest}')


def check(out: Path, setup: Path, version: str) -> int:
    results: list[tuple[str, bool, str]] = []

    def record(name: str, ok: bool, detail: str = '') -> None:
        results.append((name, bool(ok), detail))
        print(f'  [{"OK" if ok else "FAIL"}] {name}'
              + (f' — {detail}' if detail else ''), flush=True)

    print(f'校验：{out}')
    if not out.is_file():
        print(f'  文件不存在：{out}')
        return 1
    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
        record('压缩包 CRC 完整（testzip）', zf.testzip() is None,
               str(zf.testzip()))
        record('包内含 Setup.exe', setup.name in names, str(names))
        record('包内含 SHA256SUMS.txt', SUMS_NAME in names)
        record('包内含 校验与安装说明.txt', README_NAME in names)

        if setup.name in names:
            info = zf.getinfo(setup.name)
            record('exe 条目原始大小与源文件一致',
                   info.file_size == setup.stat().st_size,
                   f'{info.file_size} / {setup.stat().st_size}')
            # 流式读，不把 1.2GB 一次读进内存
            h = hashlib.sha256()
            with zf.open(info) as f:
                for buf in iter(lambda: f.read(4 << 20), b''):
                    h.update(buf)
            expect = sha256(setup)
            record('解压出的 exe 与源 exe 哈希一致',
                   h.hexdigest() == expect, h.hexdigest())
        if SUMS_NAME in names:
            text = zf.read(SUMS_NAME).decode('utf-8')
            record('SHA256SUMS.txt 记录的哈希正确',
                   sha256(setup) in text, text.strip())
        if README_NAME in names:
            text = zf.read(README_NAME).decode('utf-8')
            record('说明文档写的是当前版本', version in text, version)
            record('说明文档含误报处理指引', '白名单' in text and '代码签名' in text)
            # 说明里的 payload 文件数必须与真实构建产物一致，
            # 防止版本升级 / 瘦身后遗留旧数字（2.0.4 曾残留 2.0.3 的 314）。
            count = payload_entry_count()
            if count is not None:
                record('说明文档 payload 文件数与构建产物一致',
                       f'payload {count} 个文件' in text,
                       f'payload.zip 实测 {count} 个文件')

    passed = sum(1 for _n, ok, _d in results if ok)
    print(f'共 {len(results)} 项：通过 {passed}，失败 {len(results) - passed}')
    ok = passed == len(results)
    print('SETUP_ZIP_OK' if ok else 'SETUP_ZIP_FAILED')
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument('--check', action='store_true', help='只校验已有压缩包')
    ap.add_argument('--out', default=None, help='自定义输出路径')
    args = ap.parse_args()

    version = installer_version()
    setup = find_setup(version)
    out = out_path(version, args.out)
    print(f'版本 {version} · 安装包 {setup.name} '
          f'({setup.stat().st_size / 1048576:.2f} MB)')
    print(f'目标 {out}')
    print()
    if args.check:
        return check(out, setup, version)
    build(out, setup, version)
    print()
    return check(out, setup, version)


if __name__ == '__main__':
    sys.exit(main())
