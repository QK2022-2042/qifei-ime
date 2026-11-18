#!/usr/bin/env bash
# 启飞输入法 · macOS 出包脚本
#
# 必须在 macOS 主机（或 macOS CI runner）上运行。PyInstaller 不支持
# Windows → macOS 交叉编译，所以在 Windows 上跑这个脚本只会得到明确的报错。
#
# 产物：
#   dist/启飞输入法.app
#   dist/启飞输入法-2.0.4-macOS.dmg      （不含任何大模型）
#
# 用法：
#   bash build/build_mac.sh                    # 构建 .app + .dmg
#   QIFEI_SKIP_DMG=1 bash build/build_mac.sh  # 只构建 .app
#   CODESIGN_IDENTITY="Developer ID Application: ..." bash build/build_mac.sh
#
set -euo pipefail

cd "$(dirname "$0")/.."
ROOT="$(pwd)"
DIST="$ROOT/dist"
STAGE="$DIST/QifeiIME-mac"
APP="$DIST/启飞输入法.app"
CONTENTS="$APP/Contents"
MACOS_DIR="$CONTENTS/MacOS"

# ── 0. 平台检查 ────────────────────────────────────────────────────
if [[ "$(uname -s)" != "Darwin" ]]; then
    echo "错误：只能在 macOS 上构建 .app / .dmg（当前是 $(uname -s)）。" >&2
    echo "PyInstaller 不支持跨平台编译；请在 Mac 主机或 macOS CI runner 上执行。" >&2
    echo "Windows 上可以先运行 tools/make_mac_source_pack.py 生成源码包带过去。" >&2
    exit 2
fi

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "错误：找不到 python3，请先安装 Python 3.11+。" >&2
    exit 2
fi

echo "Python: $("$PYTHON" --version)"

# ── 1. 依赖检查 ────────────────────────────────────────────────────
echo "==> 检查构建依赖"
if ! "$PYTHON" -c "import importlib.util, sys; required = ('PyInstaller', 'PySide6', 'numpy', 'sounddevice', 'onnxruntime', 'sentencepiece', 'websockets'); missing = [m for m in required if importlib.util.find_spec(m) is None]; print('缺少 Python 依赖: ' + ', '.join(missing)) if missing else print('依赖齐全'); sys.exit(1 if missing else 0)"; then
    echo "请执行：python3 -m pip install -r requirements-build.txt -r requirements-gui.txt -r requirements-client.txt -r requirements-server.txt" >&2
    exit 2
fi

VERSION="$(sed -n "s/^VERSION = '\(.*\)'/\1/p" qifei/brand.py | head -1)"
if [[ -z "$VERSION" ]]; then
    echo "错误：没能从 qifei/brand.py 读出 VERSION" >&2
    exit 2
fi
echo "版本: $VERSION"

# ── 2. 图标：从 PNG 生成 icns ──────────────────────────────────────
echo "==> 生成 macOS 图标"
ICNS="$ROOT/assets/icon.icns"
if [[ -f "$ROOT/assets/icon.png" ]]; then
    ICONSET="$ROOT/.tmp/icon.iconset"
    rm -rf "$ICONSET"
    mkdir -p "$ICONSET"
    for size in 16 32 64 128 256 512; do
        sips -z "$size" "$size" "$ROOT/assets/icon.png" \
            --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
        double=$((size * 2))
        sips -z "$double" "$double" "$ROOT/assets/icon.png" \
            --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
    done
    iconutil -c icns "$ICONSET" -o "$ICNS"
    echo "    $ICNS"
else
    echo "    警告：缺 assets/icon.png，.app 将使用默认图标"
fi

# ── 3. 直连 llama.cpp 的 macOS 动态库（可选）────────────────────────
# 用户明确要求 macOS 版不内置大模型。这里只检查推理库本身；没有 dylib
# 依然能出包，只是「大模型」页会提示本地推理不可用，不会静默失败。
echo "==> 检查 llama.cpp macOS 动态库"
LLAMA_DIR="$ROOT/runtime/llama"
if [[ -d "$LLAMA_DIR" ]] && ls "$LLAMA_DIR"/*.dylib >/dev/null 2>&1; then
    echo "    找到 $(ls "$LLAMA_DIR"/*.dylib | wc -l | tr -d ' ') 个 .dylib"
else
    echo "    未找到 dylib —— 大模型本地推理将不可用（本包本来就不带模型）。"
    echo "    如需启用，在 Mac 上执行 brew install llama.cpp 后把"
    echo "    libllama.dylib / libggml*.dylib 拷到 runtime/llama/ 再重新构建。"
fi

# ── 4. PyInstaller 构建（由其 BUNDLE 直接产出 .app）────────────────
# BUNDLE 会按 Apple 规范把代码放进 Contents/MacOS / Contents/Frameworks，
# 数据放进 Contents/Resources 并交叉链接，最后用 codesign --deep 做 ad-hoc
# 签名。签名标识取自 Info.plist 的 CFBundleIdentifier（cn.qifei.ime）。
#
# 这一步不能用「手工拼目录」替代：把 _internal 整个塞进 Frameworks 会让
# codesign 拒绝签名（里面混有非代码文件），而未签名的 .app 在 TCC 眼里
# 没有稳定身份，辅助功能/输入监控授权会永远对不上。
echo "==> PyInstaller 构建（QIFEI_BUNDLE_MODEL=0，不带任何 .gguf）"
export QIFEI_BUNDLE_MODEL=0
export QIFEI_BUILD_TARGET=all
rm -rf "$APP" "$STAGE"
"$PYTHON" -m PyInstaller \
    --noconfirm \
    --distpath "$DIST" \
    --workpath "$ROOT/build/mac-work" \
    "$ROOT/build/qifei_mac.spec"

if [[ ! -f "$MACOS_DIR/QifeiIME" ]]; then
    echo "错误：PyInstaller 没有产出 $MACOS_DIR/QifeiIME" >&2
    exit 1
fi

# ── 5. 校验包结构 ──────────────────────────────────────────────────
echo "==> 校验 .app 结构"
for exe in QifeiIME QifeiClient QifeiServer; do
    if [[ ! -f "$MACOS_DIR/$exe" ]]; then
        echo "错误：Contents/MacOS 缺少 $exe" >&2
        exit 1
    fi
done
if [[ ! -f "$CONTENTS/Info.plist" ]]; then
    echo "错误：缺少 Info.plist" >&2
    exit 1
fi
echo "    三个可执行文件就位，Info.plist 已生成"

# 运行时数据（热词、角色、图标）由 spec 的 COMMON_DATAS 收集：BUNDLE 会把
# 它们放进 Contents/Resources 并交叉链接到 Contents/Frameworks。程序通过
# config_client/config_server 的 BASE_DIR（= sys._MEIPASS）读取。
for rel in hot.txt hot-rule.txt hot-server.txt LLM/assets; do
    if [[ ! -e "$CONTENTS/Frameworks/$rel" ]]; then
        echo "    警告：Frameworks 里找不到 ${rel}（程序可能读不到它）"
    fi
done

# ── 5.1 硬断言：包里不能有任何大模型 / Windows DLL ──────────────────
echo "==> 校验：包内不得含大模型或 Windows DLL"
BAD="$(find "$APP" \( -iname '*.gguf' -o -iname '*.onnx' -o -iname '*.dll' -o -iname '*.exe' \) -print)"
if [[ -n "$BAD" ]]; then
    echo "错误：.app 里出现了不该有的文件：" >&2
    echo "$BAD" >&2
    exit 1
fi
echo "    通过：0 个 .gguf / .onnx / .dll / .exe"

# ── 6. 签名 ────────────────────────────────────────────────────────
# PyInstaller 的 BUNDLE 已经做过 ad-hoc + --deep 签名。这里只在用户提供了
# Developer ID 时重签（并启用 hardened runtime）。没有身份就保持 ad-hoc：
# 本机使用完全够用，而且 ad-hoc 下 TCC 也能建立稳定身份。
if [[ -n "${CODESIGN_IDENTITY:-}" ]]; then
    echo "==> 重新签名：$CODESIGN_IDENTITY"
    codesign --deep --force --options runtime \
        --identifier cn.qifei.ime \
        --sign "$CODESIGN_IDENTITY" "$APP"
    codesign --verify --deep --strict "$APP"
else
    echo "==> 使用 PyInstaller 的 ad-hoc 签名（CFBundleIdentifier=cn.qifei.ime）"
    codesign -dv "$APP" 2>&1 | sed -n 's/^Identifier=/    Identifier=/p'
    codesign --verify --deep --strict "$APP" && echo "    签名校验通过" \
        || echo "    警告：签名校验未通过，辅助功能授权可能失效"
fi

# ── 8. 打 DMG ──────────────────────────────────────────────────────
DMG="$DIST/启飞输入法-${VERSION}-macOS.dmg"
if [[ "${QIFEI_SKIP_DMG:-0}" == "1" ]]; then
    echo "==> 按 QIFEI_SKIP_DMG=1 跳过 dmg"
else
    echo "==> 生成 dmg"
    rm -f "$DMG"
    hdiutil create -volname "启飞输入法 ${VERSION}" \
        -srcfolder "$APP" -ov -format UDZO "$DMG"
    echo "    $DMG"
fi

echo ""
echo "完成："
echo "  $APP"
[[ -f "$DMG" ]] && echo "  $DMG"
echo "  注意：本包不含任何大模型。首次使用请在「大模型」页点「选择模型文件」，"
echo "        或把 .gguf 放到「打开模型目录」指向的位置；语音识别模型另行下载。"
