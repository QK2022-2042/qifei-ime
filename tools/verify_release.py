# coding: utf-8
"""发版自检：核对一键安装包与分发产物，全程零副作用。

**不运行 Setup.exe**：静默安装会写桌面 / 开始菜单快捷方式和 HKCU 卸载项，
在验证机上是实打实的环境改动。这里改为直接读 onefile 里内嵌的 payload
与主脚本字节码，再把 payload 解压到临时目录模拟一次安装。

覆盖这些容易踩的坑：
- 内置模型漏带 / 多带 / 带错版本（主包现在自带推荐 gguf，但只能有这一个）
- Setup.exe 里嵌的还是上一次的旧 payload
- 改了安装器源码但忘了重打包，导致离线机器没有 models/llm
- 覆盖安装把用户人工换过 / 升级过的模型冲掉
- 改了安装器却忘了重打 Setup.exe，覆盖安装遇到运行中的输入法又失败
- 改了设置页源码却忘了重出包：装到用户机器上的还是「主包不含模型」旧版

用法：
    python tools/verify_release.py
"""
from __future__ import annotations

import hashlib
import io
import marshal
import re
import shutil
import struct
import sys
import tempfile
import types
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'installer'))

from PyInstaller.archive.readers import CArchiveReader  # noqa: E402

DIST = ROOT / 'dist'
APP_DIR = DIST / 'QifeiIME'
MODEL_PACK = DIST / '启飞输入法-LLM润色模型包.zip'


def _installer_version() -> str:
    """从构建脚本读版本号，避免升级版本时这里硬编码的文件名掉队。"""
    text = (ROOT / 'installer' / 'build_installer.py').read_text(encoding='utf-8')
    match = re.search(r"^VERSION\s*=\s*'([^']+)'", text, re.M)
    return match.group(1) if match else ''


SETUP = DIST / f'启飞输入法-{_installer_version()}-安装包.exe'
PAYLOAD = ROOT / 'installer' / 'payload.zip'

EXES = ('QifeiIME.exe', 'QifeiClient.exe', 'QifeiServer.exe')
LLAMA_DLL_COUNT = 21

# 主包现在自带推荐润色模型：内置且仅此一份，首次安装即可用。
# 名字必须与 llmkit/registry.py::BUNDLED_CHAT_MODEL 一致，下面会做源码比对。
BUNDLED_MODEL = 'qwen2.5-1.5b-instruct-q4_k_m.gguf'
BUNDLED_MODEL_REL = f'models/llm/{BUNDLED_MODEL}'
MODEL_SRC = ROOT / 'models' / 'llm' / BUNDLED_MODEL
REGISTRY_SRC = ROOT / 'llmkit' / 'registry.py'

# 服务端走自定义 ONNX 引擎，这两个库漏打包时「模型检查通过但识别子进程退出」，
# 曾在真机安装实测里漏检成 SELFTEST PASS。发版时直接查 payload 里有没有。
ORT_PYD = '_internal/onnxruntime/capi/onnxruntime_pybind11_state.pyd'
SPM_PYD_DIR = '_internal/sentencepiece/'

# 图标：改了 assets/icon.ico 却忘了重出包，用户机器上的托盘 / 资源管理器
# 看到的还是旧图标。exe 的文件图标是 PyInstaller 构建时写进 PE 资源的，
# 光换 ico 文件不会变，只有直接读 RT_ICON 才能验出来。
ICON_SRC = ROOT / 'assets' / 'icon.ico'
ICON_REL = 'assets/icon.ico'
ICON_INTERNAL_REL = '_internal/assets/icon.ico'
BUILD_INSTALLER_SRC = ROOT / 'installer' / 'build_installer.py'

_checks: list[tuple[str, bool, str]] = []


def record(name: str, ok: bool, detail: str = '') -> bool:
    _checks.append((name, bool(ok), detail))
    print(f'  [{"OK" if ok else "FAIL"}] {name}' + (f' — {detail}' if detail else ''),
          flush=True)
    return bool(ok)


def extract(reader: CArchiveReader, name: str) -> bytes:
    data = reader.extract(name)
    if isinstance(data, tuple):
        data = data[1]
    if isinstance(data, memoryview):
        data = data.tobytes()
    assert isinstance(data, bytes), f'{name} 提取结果不是 bytes: {type(data)}'
    return data


def check_dist_files() -> bool:
    print('\n[1/5] 分发产物', flush=True)
    ok = True
    for path in (APP_DIR, SETUP, MODEL_PACK, PAYLOAD):
        ok &= record(f'存在 {path.relative_to(ROOT)}', path.exists(),
                     f'{path.stat().st_size / 1048576:.1f} MB' if path.is_file() else '目录')
    # 顶层只允许这三件：应用目录 + Setup.exe + 模型补包。
    # 用白名单而不是数文件个数——多一个旧 exe / 旧 zip 都要报出来。
    allowed = {APP_DIR.name, SETUP.name, MODEL_PACK.name}
    extra = sorted(p.name for p in DIST.iterdir() if p.name not in allowed)
    ok &= record('dist 顶层没有旧产物叠加', not extra,
                 ', '.join(extra) if extra else '仅三件正式产物')

    # 升级版本号最容易漏改其中一处：品牌页显示旧版本、卸载项写另一个数字、
    # Setup 文件名还是旧数字，用户会以为装错了版本。三处必须一致。
    def _ver(rel: str) -> str:
        text = (ROOT / rel).read_text(encoding='utf-8')
        m = re.search(r"^VERSION\s*=\s*'([^']+)'", text, re.M)
        return m.group(1) if m else ''

    versions = {
        'qifei/brand.py': _ver('qifei/brand.py'),
        'installer/installer_app.py': _ver('installer/installer_app.py'),
        'installer/build_installer.py': _ver('installer/build_installer.py'),
    }
    ok &= record('三处版本号一致', len(set(versions.values())) == 1 and
                 all(versions.values()), str(versions))
    ok &= record('Setup 文件名与版本号一致',
                 SETUP.name ==
                 f'启飞输入法-{versions["installer/build_installer.py"]}-安装包.exe',
                 SETUP.name)
    return ok


def ico_dibs(data: bytes) -> list[bytes]:
    """把 ICO 拆成各尺寸的 DIB 字节；PyInstaller 会把它们原样写进 RT_ICON"""
    if data[:4] != b'\x00\x00\x01\x00':
        return []
    count = struct.unpack('<H', data[4:6])[0]
    out: list[bytes] = []
    for i in range(count):
        off = 6 + 16 * i
        if off + 16 > len(data):
            return []
        _w, _h, _c, _r, _planes, bits, length, img_off = struct.unpack(
            '<BBBBHHII', data[off:off + 16])
        blob = data[img_off:img_off + length]
        if bits != 32 or len(blob) != length:
            return []
        out.append(blob)
    return out


def pe_icon_blobs(exe: Path) -> list[bytes]:
    """读 PE 的 RT_ICON 资源；LOAD_LIBRARY_AS_DATAFILE 只映射不执行 exe"""
    import win32api
    handle = win32api.LoadLibraryEx(str(exe.resolve()), 0, 0x2)
    try:
        names = win32api.EnumResourceNames(handle, 3)  # RT_ICON = 3
        return [win32api.LoadResource(handle, 3, n) for n in names]
    finally:
        win32api.FreeLibrary(handle)


def check_pe_icons() -> bool:
    """直接核对 exe 的文件图标，专抓「改了 ico 忘重出包」"""
    print('  -- PE 文件图标', flush=True)
    ok = True
    dibs = ico_dibs(ICON_SRC.read_bytes())
    ok &= record('build_installer.py 给 Setup 传了 --icon',
                 "'--icon'" in BUILD_INSTALLER_SRC.read_text(encoding='utf-8'))
    for exe in (APP_DIR / 'QifeiIME.exe', APP_DIR / 'QifeiClient.exe',
                APP_DIR / 'QifeiServer.exe', SETUP):
        try:
            blobs = pe_icon_blobs(exe)
            same = (len(dibs) == 8 and len(blobs) == 8
                    and sorted(blobs) == sorted(dibs))
            ok &= record(f'{exe.name} 的 PE 图标 = 新 ico（8 尺寸逐字节一致）',
                         same, f'RT_ICON {len(blobs)} 个 / 期望 {len(dibs)} 个')
        except Exception as exc:
            ok &= record(f'{exe.name} 的 PE 图标 = 新 ico', False,
                         f'{type(exc).__name__}: {exc}')
    return ok


def payload_names() -> list[str]:
    with zipfile.ZipFile(PAYLOAD) as zf:
        return zf.namelist()


def check_payload() -> bool:
    print('\n[2/5] payload 内容', flush=True)
    ok = True
    with zipfile.ZipFile(PAYLOAD) as zf:
        names = zf.namelist()
        infos = {i.filename: i for i in zf.infolist()}
        gguf = [n for n in names if n.lower().endswith('.gguf')]
        models = [n for n in names
                  if n.split('/')[0] == 'models' and not n.endswith('/')]
        top_exes = [n for n in names if n in EXES]
        dll = [n for n in names if 'engines/llama/bin/' in n]

        # 策略反转：以前「无 .gguf」才算干净，现在必须恰好内置一份推荐模型。
        ok &= record('内置且仅一份推荐模型', gguf == [BUNDLED_MODEL_REL], str(gguf[:3]))
        ok &= record('models/ 条目只有内置模型', models == [BUNDLED_MODEL_REL],
                     str(models[:3]))
        info = infos.get(BUNDLED_MODEL_REL)
        ok &= record('内置模型条目存在', info is not None, BUNDLED_MODEL_REL)
        if info is not None:
            expect = MODEL_SRC.stat().st_size if MODEL_SRC.is_file() else 0
            ok &= record('内置模型大小与源文件一致', info.file_size == expect,
                         f'{info.file_size} / {expect} bytes')
            ok &= record('内置模型用 ZIP_STORED（不空等压缩）',
                         info.compress_type == zipfile.ZIP_STORED,
                         f'compress_type={info.compress_type}')
            with zf.open(info) as f:      # 只读 4 字节，不把 1GB 读进内存
                magic = f.read(4)
            ok &= record('内置模型是合法 GGUF', magic == b'GGUF', str(magic))

        ok &= record('顶层三个 exe 齐全', len(top_exes) == 3, str(top_exes))
        ok &= record(f'llama 动态库 {LLAMA_DLL_COUNT} 个', len(dll) == LLAMA_DLL_COUNT,
                     f'实际 {len(dll)}')
        ok &= record('含用户可编辑文件', all(n in names for n in
                     ('hot.txt', 'hot-rule.txt', 'hot-server.txt', '使用说明.md')))
        # 服务端依赖：漏了会「自检 PASS 但识别子进程直接退出」
        ok &= record('onnxruntime capi 已随包', ORT_PYD in names, ORT_PYD)
        spm = [n for n in names if n.startswith(SPM_PYD_DIR) and n.endswith('.pyd')]
        ok &= record('sentencepiece 扩展已随包', bool(spm), str(spm[:2]))

        # 图标：随包 ico 必须与源码一致，而且是分级绘制的 8 尺寸 ICO。
        # 只换源码 ico 不重出包，装到用户机器上的托盘仍是旧图标。
        icon_src = ICON_SRC.read_bytes() if ICON_SRC.is_file() else b''
        packed = zf.read(ICON_REL) if ICON_REL in infos else b''
        dibs = ico_dibs(packed)
        ok &= record('payload 内 assets/icon.ico 与源码一致',
                     bool(packed) and packed == icon_src,
                     f'{len(packed)} / {len(icon_src)} bytes')
        ok &= record('随包 ico 是 8 尺寸 32bpp DIB', len(dibs) == 8,
                     f'{len(dibs)} 个条目')
        if ICON_INTERNAL_REL in infos:
            ok &= record('payload 内 _internal ico 与源码一致',
                         zf.read(ICON_INTERNAL_REL) == icon_src)

    # 防止三处硬编码的模型名与 registry 漂移，导致装完自动选中失效
    match = re.search(r"BUNDLED_CHAT_MODEL\s*=\s*'([^']+)'",
                      REGISTRY_SRC.read_text(encoding='utf-8'))
    ok &= record('内置模型名与 llmkit.registry 一致',
                 bool(match) and match.group(1) == BUNDLED_MODEL,
                 match.group(1) if match else '未解析到常量')
    return ok


def check_setup_payload(reader: CArchiveReader) -> bytes | None:
    print('\n[3/5] Setup.exe 内嵌 payload', flush=True)
    hits = [str(k) for k in reader.toc if 'payload' in str(k).lower()]
    if not record('找到内嵌 payload.zip', bool(hits), str(hits)):
        return None
    data = extract(reader, hits[0])
    # 1GB 级 payload 不再 PAYLOAD.read_bytes() 再造一份完整副本：
    # 源文件分块算 md5，只和已在内存里的内嵌数据比哈希与长度。
    digest = hashlib.md5(data).hexdigest()
    file_hash = hashlib.md5()
    with PAYLOAD.open('rb') as f:
        for chunk in iter(lambda: f.read(4 * 1024 * 1024), b''):
            file_hash.update(chunk)
    same = (len(data) == PAYLOAD.stat().st_size
            and digest == file_hash.hexdigest())
    record('内容与 installer/payload.zip 完全一致', same,
           f'{len(data)} bytes, md5 {digest[:12]}')
    return data if same else None


def check_frozen_installer_code(reader: CArchiveReader) -> bool:
    """确认 Setup.exe 里的 installer_app 是新版（含 models/llm 目录契约）"""
    print('\n[4/5] exe 内部（frozen 代码 + PE 文件图标）', flush=True)
    hits = [str(k) for k in reader.toc if str(k) == 'installer_app']
    if not record('找到主脚本 installer_app', bool(hits)):
        return False
    data = extract(reader, hits[0])
    try:
        code = marshal.loads(data)
    except Exception as exc:
        return record('主脚本可解析', False, f'{type(exc).__name__}: {exc}')
    if not isinstance(code, types.CodeType):
        return record('主脚本是 code object', False, str(type(code)))

    # ensure_dirs 的目录清单是元组常量，递归收集 str 和 tuple/list 里的 str
    ensure = next((c for c in code.co_consts
                   if isinstance(c, types.CodeType) and c.co_name == 'ensure_dirs'), None)
    if ensure is None:
        return record('找到 ensure_dirs', False)

    consts: list[str] = []
    for item in ensure.co_consts:
        if isinstance(item, str):
            consts.append(item)
        elif isinstance(item, (tuple, list, frozenset)):
            consts.extend(x for x in item if isinstance(x, str))
    record('ensure_dirs 常量', True, str([c for c in consts if c in
                                        ('models', 'models/llm', 'logs')]))
    ok = record('已预建 models/llm（新版安装器）', 'models/llm' in consts)
    # 改了源码却忘了重打 Setup.exe，用户模型还是会被覆盖安装冲掉
    top_level = [c.co_name for c in code.co_consts if isinstance(c, types.CodeType)]
    ok &= record('含 extract_payload（用户模型保护已打进 Setup）',
                 'extract_payload' in top_level,
                 str([n for n in top_level if 'payload' in n]))
    # 真机覆盖安装踩到过：4 个 QifeiServer.exe worker 锁住 exe，
    # 解压半途 PermissionError，留下半更新状态。改了源码必须重打 Setup。
    ok &= record('含 stop_running_apps（装前结束运行中进程）',
                 'stop_running_apps' in top_level,
                 str([n for n in top_level if 'stop' in n or 'running' in n]))
    return ok


def _pyz_module_code(exe: Path, module: str):
    """从 onedir exe 内嵌的 PYZ 里内存解析出模块 code object。

    PyInstaller 的 ZlibArchiveReader 只接受文件路径；为了自检不落盘、不删临时
    文件（本机对批量删除有保护），这里直接按 PYZ 格式读：
    b'PYZ\\0' + 4B magic + 4B TOC 偏移 → marshal TOC → zlib 压缩的 marshal code。
    """
    data = extract(CArchiveReader(str(exe)), 'PYZ.pyz')
    if data[:4] != b'PYZ\0':
        raise ValueError('PYZ 魔数不匹配')
    toc_offset = struct.unpack('!i', data[8:12])[0]
    toc = dict(marshal.loads(data[toc_offset:]))
    _typ, off, length = toc[module]
    return marshal.loads(zlib.decompress(data[off:off + length]))


def _code_strings_and_names(code) -> tuple[list[str], set[str], list[tuple]]:
    """收集字符串常量、标识符、以及调用关键字元组（KW_NAMES）。

    Python 3.11+ 把 `func(a=1, b=2)` 的关键字名编译成 co_consts 里的一个
    str 元组，不在 co_names 里；`force` 又是闭包变量（cellvar / freevar）。
    所以要同时看这三处，才能确认 download() 真的带上了 force 关键字。
    """
    strings: list[str] = []
    names: set[str] = set()
    kw_groups: list[tuple] = []

    def walk(node):
        names.update(node.co_names)
        names.update(node.co_varnames)
        names.update(node.co_cellvars)
        names.update(node.co_freevars)
        for item in node.co_consts:
            if isinstance(item, str):
                strings.append(item)
            elif isinstance(item, (tuple, list, frozenset)) \
                    and all(isinstance(x, str) for x in item):
                strings.extend(item)
                kw_groups.append(tuple(item))
            elif isinstance(item, types.CodeType):
                walk(item)

    walk(code)
    return strings, names, kw_groups


def check_frozen_gui_code() -> bool:
    """确认 QifeiIME.exe 里的设置页是新版：模型已内置时人工确认才覆盖重下。

    这一项专门用来抓「源码改了但没重出包」——用户装到的仍是旧 exe，
    点下载只会弹「模型已就绪」，没有人工重下 / 恢复出厂模型的入口。
    """
    exe = APP_DIR / 'QifeiIME.exe'
    try:
        code = _pyz_module_code(exe, 'qifei.llm_page')
        strings, _names, kw_groups = _code_strings_and_names(code)
    except Exception as exc:
        return record('能读出 GUI 内嵌 llm_page 字节码', False,
                      f'{type(exc).__name__}: {exc}')
    ok = record('能读出 GUI 内嵌 llm_page 字节码', True,
                f'{len(strings)} 个字符串常量')
    ok &= record('设置页含「推荐模型已内置」人工确认',
                 '推荐模型已内置' in strings)
    ok &= record('重下完成后文案明确',
                 '已重新下载并恢复为默认模型' in strings)
    force_kw = [g for g in kw_groups if 'force' in g and 'on_stage' in g]
    ok &= record('download() 调用确实带 force 关键字', bool(force_kw),
                 str(force_kw[:2]))
    ok &= record('已清除「主包不含模型」旧文案',
                 not any('主包不含' in s for s in strings))
    return ok


def simulate_install() -> bool:
    """模拟一次全新安装 + 一次覆盖安装。

    直接读磁盘上的 payload.zip（第 3 组已证明 Setup 内嵌字节与它完全一致），
    不再把 1GB bytes 喂给 BytesIO 复制一份；内置模型只按 namelist / 魔数核对，
    不落盘，收尾 rmtree 才不会变成删 1GB 文件。
    """
    print('\n[5/5] 模拟安装（临时目录，不碰桌面 / 开始菜单 / 注册表）', flush=True)
    tmp = Path(tempfile.mkdtemp(prefix='verify-release-', dir=str(ROOT / '.tmp')))
    try:
        with zipfile.ZipFile(PAYLOAD) as zf:
            names = zf.namelist()
            info = zf.getinfo(BUNDLED_MODEL_REL)
            with zf.open(info) as f:      # 只读 4 字节
                magic = f.read(4)
            # 只落盘真正要读内容 / 交给 ensure_dirs 的少量文件，其余按
            # namelist 核对即可。全量解压 300+ 文件既慢，收尾 rmtree 还会在
            # 部分受管控环境里因批量删除被拦，让自检自己挂掉（实测踩到过）。
            needed = [n for n in names
                      if n in EXES or n == '使用说明.md' or n == ORT_PYD]
            for name in needed:
                zf.extract(name, tmp)

        import installer_app
        installer_app.ensure_dirs(tmp)

        # 用几字节的迷你 payload 跑真实的 extract_payload：
        # 全新安装要落地内置模型，覆盖安装不能冲掉用户人工换过的模型。
        mini = io.BytesIO()
        with zipfile.ZipFile(mini, 'w') as z:
            z.writestr(BUNDLED_MODEL_REL, b'BUNDLED_MODEL')
            z.writestr('upgrade-marker.bin', b'V2')
        mini_bytes = mini.getvalue()

        fresh = tmp / 'fresh'
        with zipfile.ZipFile(io.BytesIO(mini_bytes)) as z:
            written, _ = installer_app.extract_payload(z, fresh)

        (tmp / 'models' / 'llm').mkdir(parents=True, exist_ok=True)
        user_model = tmp / 'models' / 'llm' / BUNDLED_MODEL
        user_model.write_bytes(b'USER_REPLACED')
        other_model = tmp / 'models' / 'llm' / 'old-user-model.gguf'
        other_model.write_bytes(b'KEEP_ME')
        (tmp / 'upgrade-marker.bin').write_bytes(b'V1')
        with zipfile.ZipFile(io.BytesIO(mini_bytes)) as z:
            covered, kept = installer_app.extract_payload(z, tmp)

        ok = True
        ok &= record('解压后三个 exe 在位',
                     all((tmp / n).is_file() for n in EXES))
        ok &= record('models/llm 已创建', (tmp / 'models' / 'llm').is_dir())
        expect = MODEL_SRC.stat().st_size if MODEL_SRC.is_file() else 0
        ok &= record('首次安装会落地内置模型',
                     BUNDLED_MODEL_REL in names and info.file_size == expect
                     and magic == b'GGUF'
                     and (fresh / BUNDLED_MODEL_REL).read_bytes() == b'BUNDLED_MODEL',
                     f'{info.file_size} bytes，全新安装释放 {written} 个文件')
        ok &= record('覆盖安装不覆盖用户换过的同名模型',
                     user_model.read_bytes() == b'USER_REPLACED',
                     f'跳过 {kept} 个用户文件')
        ok &= record('覆盖安装保留用户其它模型',
                     other_model.read_bytes() == b'KEEP_ME')
        ok &= record('覆盖安装仍会更新程序本体',
                     (tmp / 'upgrade-marker.bin').read_bytes() == b'V2',
                     f'释放 {covered} 个文件')
        ok &= record('llama 动态库齐全', len([n for n in names
                                          if 'engines/llama/bin/' in n]) == LLAMA_DLL_COUNT)
        ok &= record('解压后 onnxruntime capi 在位',
                     tmp.joinpath(*ORT_PYD.split('/')).is_file())
        ok &= record('随包使用说明是新版',
                     BUNDLED_MODEL in (tmp / '使用说明.md').read_text(encoding='utf-8'))
        ok &= record('models/ 下无多余模型条目',
                     [n for n in names if n.startswith('models/')] == [BUNDLED_MODEL_REL])
        return ok
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main() -> int:
    print('启飞输入法 · 发版自检', flush=True)
    ok = check_dist_files()
    ok &= check_payload()
    reader = CArchiveReader(str(SETUP))
    payload = check_setup_payload(reader)
    ok &= payload is not None
    ok &= check_frozen_installer_code(reader)
    ok &= check_frozen_gui_code()
    ok &= check_pe_icons()
    if payload is not None:
        ok &= simulate_install()
    failed = [name for name, passed, _detail in _checks if not passed]
    print(f'\n共 {len(_checks)} 项检查：通过 {len(_checks) - len(failed)}，失败 {len(failed)}',
          flush=True)
    if failed:
        print('失败项：' + '、'.join(failed), flush=True)
    print('RELEASE_VERIFY_OK' if ok else 'RELEASE_VERIFY_FAILED', flush=True)
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())
