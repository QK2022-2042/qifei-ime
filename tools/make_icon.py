# coding: utf-8
"""
启飞输入法 · 图标生成器（可重复运行，只依赖 Pillow + numpy，两者都是项目已有依赖）

设计：「启飞 · 声起字落」
  - 品牌蓝斜向渐变的圆角方形：#7BA2FF → #3D6BFF → #2A45B8
  - 白色圆头声波竖条 = 说出来
  - 底部一条输入基线 + 右端光标 = 落成文字
  - 左上淡高光 = 轻微质感，不做投影 / 描边

尺寸分级（每个尺寸单独绘制，不是从 256 一张缩下来）：
  - 16 / 20 / 24：3 根粗条 + 基线，托盘里才不会糊成一团
  - 32：5 根条 + 基线，作为小尺寸到大尺寸的过渡
  - 48 / 64 / 128 / 256：5 根条 + 基线 + 光标 + 淡高光

输出：
  assets/icon.ico          8 个尺寸的标准 32bpp DIB ICO
  assets/icon.png          256×256 RGBA，文档 / 网页用
  assets/icon-preview.png  多尺寸深浅底对照预览

ICO 这里手写 32bpp DIB 容器，不用 PNG-in-ICO：PyInstaller 的
PyInstaller.utils.win32.icon.IconFile 会把条目原样写进 exe 的 RT_ICON
资源，Qt 的 ICO 插件和 Windows 外壳对 DIB 的兼容性都最稳。

用法：
  python tools/make_icon.py           # 生成
  python tools/make_icon.py --check  # 只校验现有 ico
"""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageChops, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / 'assets'
ICO = ASSETS / 'icon.ico'
PNG = ASSETS / 'icon.png'
PREVIEW = ASSETS / 'icon-preview.png'

SIZES = (16, 20, 24, 32, 48, 64, 128, 256)
TINY_SIZES = (16, 20, 24)   # 3 根粗条
MID_SIZES = (32,)           # 5 根条，不画光标
SS = 4  # 超采样倍数

# 与 qifei/app.py、installer/installer_app.py 的品牌色保持一致
STOPS = (
    (0.0, (0x7B, 0xA2, 0xFF)),
    (0.5, (0x3D, 0x6B, 0xFF)),
    (1.0, (0x2A, 0x45, 0xB8)),
)
WHITE = (255, 255, 255, 255)


def _gradient(s: int, highlight: bool) -> Image.Image:
    """左上亮、右下深的斜向渐变，可选左上淡高光"""
    yy, xx = np.mgrid[0:s, 0:s].astype(np.float32)
    t = np.clip((xx + yy) / (2.0 * (s - 1)), 0.0, 1.0)
    xs = [p[0] for p in STOPS]
    arr = np.empty((s, s, 3), dtype=np.float32)
    for i in range(3):
        arr[..., i] = np.interp(t, xs, [p[1][i] for p in STOPS])
    if highlight:
        hl = (np.clip(1.0 - t * 1.9, 0.0, 1.0) ** 1.4) * 0.13
        arr += (255.0 - arr) * hl[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), 'RGB')


def _rounded_mask(s: int) -> Image.Image:
    mask = Image.new('L', (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        [0, 0, s - 1, s - 1], radius=round(s * 0.225), fill=255)
    return mask


def _bar(d: ImageDraw.ImageDraw, cx: float, cy: float,
         width: float, half: float) -> None:
    d.rounded_rectangle(
        [cx - width / 2, cy - half, cx + width / 2, cy + half],
        radius=width / 2, fill=WHITE)


def _foreground(s: int, size: int) -> Image.Image:
    fg = Image.new('RGBA', (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(fg)

    if size in TINY_SIZES:
        # 托盘尺寸：把声波压扁、抬高，和基线拉开至少 2px，否则 16px 会糊成一团
        cx = (0.28, 0.50, 0.72)
        ratios = (0.50, 1.00, 0.62)
        max_half, cy, width = 0.22 * s, 0.40 * s, 0.15 * s
        thick, base_y, left, right = 0.085 * s, 0.82 * s, 0.24, 0.76
    elif size in MID_SIZES:
        # 32px 过渡档：条数与大尺寸一致，只是略粗、不画光标
        cx = (0.24, 0.37, 0.50, 0.63, 0.76)
        ratios = (0.34, 0.62, 1.00, 0.62, 0.34)
        max_half, cy, width = 0.24 * s, 0.40 * s, 0.09 * s
        thick, base_y, left, right = 0.075 * s, 0.80 * s, 0.22, 0.68
    else:
        cx = (0.24, 0.37, 0.50, 0.63, 0.76)
        ratios = (0.34, 0.62, 1.00, 0.62, 0.34)
        max_half, cy, width = 0.25 * s, 0.42 * s, 0.075 * s
        thick, base_y, left, right = 0.062 * s, 0.79 * s, 0.22, 0.68

    for x, ratio in zip(cx, ratios):
        _bar(d, x * s, cy, width, max_half * ratio)

    # 输入基线：文字落位的地方
    d.rounded_rectangle(
        [left * s, base_y - thick / 2, right * s, base_y + thick / 2],
        radius=thick / 2, fill=WHITE)

    # 输入光标：只在 ≥48px 画，再小就是噪点。
    # 细长条紧接基线右端，一眼是「光标」而不是一颗胶囊。
    if size not in TINY_SIZES and size not in MID_SIZES:
        cw = 0.045 * s
        ccx = 0.73 * s
        d.rounded_rectangle(
            [ccx - cw / 2, 0.71 * s, ccx + cw / 2, 0.87 * s],
            radius=cw / 2, fill=WHITE)

    return fg


def render(size: int) -> Image.Image:
    """生成单个尺寸的 RGBA 图标"""
    s = size * SS
    mask = _rounded_mask(s)

    base = _gradient(s, highlight=size not in TINY_SIZES).convert('RGBA')
    base.putalpha(mask)

    fg = _foreground(s, size)
    fg.putalpha(ImageChops.multiply(fg.getchannel('A'), mask))

    composed = Image.alpha_composite(base, fg)
    return composed.resize((size, size), Image.LANCZOS)


def _dib(img: Image.Image) -> bytes:
    """32bpp BGRA DIB（BITMAPINFOHEADER + XOR + AND），ICO 条目数据"""
    w, h = img.size
    px = img.convert('RGBA').load()
    rows = []
    for y in range(h - 1, -1, -1):  # DIB 自下而上
        row = bytearray()
        for x in range(w):
            r, g, b, a = px[x, y]
            row += bytes((b, g, r, a))
        rows.append(bytes(row))
    xor = b''.join(rows)
    and_stride = ((w + 31) // 32) * 4
    and_mask = b'\x00' * (and_stride * h)  # 32bpp 有 alpha，AND 全 0
    header = struct.pack(
        '<IiiHHIIiiII', 40, w, h * 2, 1, 32, 0, 0, 0, 0, 0, 0)
    return header + xor + and_mask


def write_ico(images: dict[int, Image.Image], path: Path) -> None:
    blobs = [(size, _dib(images[size])) for size in SIZES]
    count = len(blobs)
    offset = 6 + 16 * count
    entries = bytearray()
    for size, blob in blobs:
        b = size if size < 256 else 0
        entries += struct.pack('<BBBBHHII',
                               b, b, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    path.write_bytes(
        struct.pack('<HHH', 0, 1, count)
        + bytes(entries)
        + b''.join(blob for _, blob in blobs))


def write_preview(images: dict[int, Image.Image], path: Path) -> None:
    """两行实际像素对照：浅底 / 深底，一眼看小尺寸是否还认得出。

    列宽按该列图标实际边长分配，256px 那列必须留够位置，不能被裁掉。
    """
    col_w = [max(72, size + 32) for size in SIZES]
    row_h = 256 + 80
    width, height = sum(col_w), row_h * 2
    canvas = Image.new('RGB', (width, height))
    d = ImageDraw.Draw(canvas)
    for row, bg in enumerate(('#F4F7FF', '#141C2E')):
        top = row * row_h
        d.rectangle([0, top, width, top + row_h], fill=bg)
        ink = '#5A6B8C' if row == 0 else '#93A6CC'
        rule = '#E3E9F5' if row == 0 else '#24304A'
        x0 = 0
        for col, size in enumerate(SIZES):
            icon = images[size]
            ix = x0 + (col_w[col] - size) // 2
            iy = top + (row_h - 40 - size) // 2
            canvas.paste(icon, (ix, iy), icon)
            label = f'{size}px'
            d.text((x0 + (col_w[col] - len(label) * 6) // 2,
                    top + row_h - 34), label, fill=ink)
            x0 += col_w[col]
            if col < len(SIZES) - 1:
                d.line([(x0, top + 18), (x0, top + row_h - 46)], fill=rule)
    canvas.save(path)


def check(path: Path) -> bool:
    """零副作用校验：容器结构、每个尺寸的 DIB 头、圆角透明、构建链路可读"""
    ok = True

    def record(label: str, passed: bool, detail: str = '') -> None:
        nonlocal ok
        ok &= passed
        print(f'{"[ok]  " if passed else "[FAIL]"} {label}'
              + (f'  {detail}' if detail else ''))

    data = path.read_bytes()
    reserved, kind, count = struct.unpack('<HHH', data[:6])
    record('ICO 目录头合法', reserved == 0 and kind == 1,
           f'type={kind} count={count}')
    record('尺寸数量 = 8', count == len(SIZES), str(count))

    for i, size in enumerate(SIZES):
        off = 6 + 16 * i
        w, h, _, _, planes, bits, length, image_off = \
            struct.unpack('<BBBBHHII', data[off:off + 16])
        expect_w = size if size < 256 else 0
        blob = data[image_off:image_off + length]
        bi_size, bi_w, bi_h, _, bi_bits = struct.unpack('<IiiHH', blob[:16])
        good = (w == expect_w and h == expect_w and planes == 1
                and bits == 32 and len(blob) == length
                and bi_size == 40 and bi_w == size and bi_h == size * 2
                and bi_bits == 32)
        record(f'{size}px 条目是标准 32bpp DIB', good,
               f'{length} bytes')

    with Image.open(path) as im:
        got = set(im.ico.sizes())
        record('Pillow 能读出全部 8 个尺寸',
               got == set((n, n) for n in SIZES), str(sorted(got)))
        for size in SIZES:
            frame = im.ico.getimage((size, size)).convert('RGBA')
            corner = frame.getpixel((0, 0))[3]
            center = frame.getpixel((size // 2, size // 2))[3]
            record(f'{size}px 圆角透明 / 主体不透明',
                   corner < 24 and center > 230,
                   f'corner={corner} center={center}')

    try:
        from PyInstaller.utils.win32.icon import IconFile
        parsed = IconFile(str(path))
        good = (len(parsed.entries) == len(SIZES)
                and all(len(b) == e.dwBytesInRes
                        for b, e in zip(parsed.images, parsed.entries)))
        record('PyInstaller IconFile 可解析（构建 exe 图标用）', good,
               f'{len(parsed.entries)} entries')
    except ImportError:
        print('[skip] 未安装 PyInstaller，跳过构建链路校验')

    # 绿色版是运行时直接读 assets/icon.ico 的，改完图标必须同步过去；
    # 否则源码换了新图标，绿色版的托盘 / 窗口还是旧样子。
    for rel in ('dist/QifeiIME/assets/icon.ico',
                'dist/QifeiIME/_internal/assets/icon.ico'):
        target = ROOT / rel
        if target.is_file():
            record(f'{rel} 与源码一致', target.read_bytes() == data)

    print('ICON_CHECK_OK' if ok else 'ICON_CHECK_FAILED')
    return ok


def main() -> int:
    ap = argparse.ArgumentParser(description='生成 / 校验启飞输入法图标')
    ap.add_argument('--check', action='store_true', help='只校验现有 ico')
    args = ap.parse_args()

    if args.check:
        return 0 if check(ICO) else 1

    ASSETS.mkdir(parents=True, exist_ok=True)
    images = {size: render(size) for size in SIZES}
    write_ico(images, ICO)
    images[256].save(PNG)
    write_preview(images, PREVIEW)
    print(f'已生成 {ICO.relative_to(ROOT)}  ({ICO.stat().st_size} bytes)')
    print(f'已生成 {PNG.relative_to(ROOT)}')
    print(f'已生成 {PREVIEW.relative_to(ROOT)}')
    return 0 if check(ICO) else 1


if __name__ == '__main__':
    sys.exit(main())
