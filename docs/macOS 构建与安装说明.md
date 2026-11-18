# macOS 构建与安装说明（2.0.4）

## 先说结论

**在这台 Windows 电脑上，不能生成真正可安装的 macOS `.app` / `.dmg`。**

PyInstaller 不支持跨平台编译：它在哪个系统上运行，就只能产出哪个系统的可执行文件。
macOS 的 `.app` bundle、代码签名、公证（notarization）都必须在 macOS 主机或 macOS CI
runner 上完成。硬凑一个 Windows 生成的「Mac 安装包」是假的，装不上，也不会在这里做。

本仓库已经把 **Mac 侧的一键出包链路** 准备好了，你在 Mac 上跑一条命令即可产出：

| 文件 | 说明 |
| --- | --- |
| `build/build_mac.sh` | macOS 出包脚本，产出 `.app` + `.dmg` |
| `build/qifei_mac.spec` | macOS 专用 PyInstaller 配置（不带模型、不带 Windows DLL） |
| `tools/make_mac_source_pack.py` | 在 Windows 上生成不含大模型的源码包，拷到 Mac 用 |
| `桌面/启飞输入法-2.0.4-macOS源码包.zip` | 上面这个工具产出的源码包 |

**版本 2.0.4 已统一** 到 `qifei/brand.py`、`installer/installer_app.py`、
`installer/build_installer.py`；Windows 安装包文件名也会跟着变成
`启飞输入法-2.0.4-安装包.exe`。

## 这个 macOS 包「不需要大模型」意味着什么

- 包里 **0 个 `.gguf`、0 个 `.onnx`**。`build_mac.sh` 有硬断言，任何一个混进去就构建失败。
- 也不带 Windows 的 `llama.dll` / `ggml-*.dll`（Mac 上完全无用）。
- 语音识别模型（SenseVoice）仍是首次启动时按界面引导下载，或人工放到模型目录。
- 润色大模型完全由你人工决定：在「大模型」页点「选择模型文件」，或把 `.gguf` 拷进模型目录。程序不会自动下载、不会替换。
- 想用本地直连 llama.cpp，需要 Mac 版动态库：`brew install llama.cpp` 后把
  `libllama.dylib`、`libggml*.dylib` 拷到 `runtime/llama/` 再重新构建。没有这些库
  依然能出包、能装、能跑语音识别，只是大模型页会明确提示本地推理不可用 —— 不会静默失败。

## 在 Mac 上出包（三步）

```bash
# 0. 前置：macOS 12+、Python 3.11+、Xcode Command Line Tools
xcode-select --install

# 1. 解压源码包并进入目录
unzip 启飞输入法-2.0.4-macOS源码包.zip
cd 启飞输入法-2.0.4-macOS源码

# 2. 装依赖
python3 -m venv .venv && source .venv/bin/activate
python3 -m pip install -r requirements-build.txt \
    -r requirements-gui.txt -r requirements-client.txt -r requirements-server.txt

# 3. 出包
bash build/build_mac.sh
```

产物：

```
dist/启飞输入法.app
dist/启飞输入法-2.0.4-macOS.dmg
```

只想要 `.app` 调试：`QIFEI_SKIP_DMG=1 bash build/build_mac.sh`。

## 安装与首次运行

1. 打开 `.dmg`，把「启飞输入法」拖进「应用程序」。
2. 首次打开若被 Gatekeeper 拦：右键 → 「打开」 → 确认。未签名的包只能这样开。
3. 系统会依次请求权限，都要允许（离线识别必需的）：
   - **麦克风**：录音；
   - **辅助功能 / 输入监控**：全局快捷键监听、把识别结果写入当前应用。
4. 在「设置中心 → 大模型」页点「选择模型文件」挑一个带 `-Instruct` 的 `.gguf`。
   没有 chat template 的 ASR / 补全模型会被标成「非对话模型」，不能拿来润色。

## 正式分发：签名与公证

未签名、未公证的 `.dmg` 在别人 Mac 上会被拦。需要 Apple Developer 账号：

```bash
# 签名（Developer ID Application）
CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
    bash build/build_mac.sh

# 公证（脚本之外，按需执行）
ditto -c -k --keepParent dist/启飞输入法.app /tmp/qifei.zip
xcrun notarytool submit /tmp/qifei.zip --apple-id ... --team-id ... --password ... --wait
xcrun stapler staple dist/启飞输入法.app
# 再重新打 dmg 并 stapler staple dmg
```

`build_mac.sh` 只做了签名（给了 `CODESIGN_IDENTITY` 时），公证需要账号凭据，留给发布者执行。

## 已知：Mac 侧仍需处理的跨平台代码（重要，别以为已经全改好了）

Windows 版 47/47 发版自检、21/21 真机安装验证都只覆盖 Windows。以下代码是
**Windows 专属实现**，在 Mac 上跑之前需要按清单改；这份源码包没有偷偷改掉它们，
因为无法在 Windows 上真机验证 Mac 行为，改了反而是不负责任：

| 文件 | Windows 实现 | Mac 上要做的 |
| --- | --- | --- |
| `qifei/autostart.py` | `winreg` 写 HKCU Run | 改成 `~/Library/LaunchAgents/cn.qifei.ime.plist`（`launchctl load`） |
| `installer/installer_app.py` | `winreg` / `taskkill` / `SHGetFolderPathW` / `.bat` 卸载 | 整个是 Windows 安装器；Mac 用 `.dmg` 拖拽安装，不要复用这个文件 |
| `qifei/app.py`、`qifei/llm_page.py`、`qifei/main_window.py` | `os.startfile()` | 换成 `subprocess.run(['open', path])` |
| `qifei/supervisor.py` | `CREATE_NO_WINDOW` 传给子进程 | Darwin 分支去掉这个 flag |
| `core/client/llm/llm_get_selection.py` | `keyboard.press_and_release('ctrl+c')` | Mac 要用 `cmd+c`；`keyboard` 库在 macOS 需要 root，建议统一走 `pynput` |
| `core/client/llm/llm_output_typing.py` | `keyboard.write()` | 同上，中英文上屏在 Mac 需验证 |
| `core/client/global_hotkey/` | `pynput` 全局快捷键 | macOS 需辅助功能 + 输入监控权限，已在 Info.plist 写明用途 |
| `llmkit/native.py` | 找 `.dll` | 找 `.dylib`，并用 `ctypes.CDLL` 加载；`os.add_dll_directory` 是 Windows 专属 |
| `qifei/paths.py::data_dir()` | `%APPDATA%\QifeiIME` | 建议 `~/Library/Application Support/QifeiIME`（当前会退到用户主目录，能跑但不合规范） |
| `config_override.py::config_dir()` | 同上 | 同上 |
| `llmkit/registry.py::default_scan_dirs()` | 扫 Windows 常见目录 | 加上 `~/Library/Application Support/QifeiIME/models/llm` |

`.app` 里 `Contents/MacOS/` 是可执行目录，签名后往里写模型/日志会破坏签名。
正式分发版建议把 `app_root()` 的可写部分重定向到
`~/Library/Application Support/QifeiIME/`，只读资源继续从 `_MEIPASS` 取。

## 验证清单（在 Mac 上出包后逐项确认）

- [ ] `find dist/启飞输入法.app -name '*.gguf' -o -name '*.dll'` 输出为空
- [ ] `.app` 能双击启动，主窗口显示「版本 2.0.4」
- [ ] 麦克风、辅助功能权限弹窗出现并已允许
- [ ] `QifeiServer` 能起来、语音识别能出字
- [ ] 「大模型」页：不带 `.gguf` 时提示「选择模型文件」，不误报「必须下载」
- [ ] 人工放入带 chat template 的 `.gguf` 后，点「测试连接」能返回正常中文
- [ ] 换一个安装目录 / 换用户后，旧配置里的绝对路径能自动重定位（v2.10 修复，Windows 已 20/20 验证）
