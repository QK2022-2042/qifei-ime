# macOS 移植踩坑与排障记录

> 记录时间：2026-11-15
> 机器环境：Mac mini（Apple Silicon / arm64），macOS 26（Darwin 27.2.1），
> 仅安装 CommandLineTools，系统 Python 3.9.6，无 Homebrew。
> 目标：把「启飞输入法 2.0.4」（CapsWriter-Offline 衍生版，Windows 优先）
> 改造成本机可用的离线中文语音输入法。

本文只记录**结论、根因和可复用的排障方法**，不复述每一步试错过程。

---

## 一、总体结构

- 服务端：`QifeiServer` + 识别 worker 子进程，加载 SenseVoice-Small
  （onnxruntime），监听 `0.0.0.0:6016`，WebSocket 子协议 `binary`。
- 客户端：`QifeiClient`，抓麦克风 → 发 WebSocket → 收识别结果 → 模拟键盘上屏。
- GUI：`QifeiIME`（PySide6），负责配置、托盘、拉起上面两个进程。
- 三者共用一个 `_internal`（打包后的 Python 运行时）。

数据与配置位置（macOS）：

```
~/Library/Application Support/QifeiIME/config.json          # 配置
~/Library/Application Support/QifeiIME/logs/                # 日志
~/Library/Application Support/QifeiIME/models/              # ASR / LLM 模型
```

---

## 二、最核心的三个坑

### 1. .app 必须是「合法签名」的 bundle，否则 TCC 授权永远对不上

**现象**

- 系统设置里「设备控制和数据访问 / 辅助功能」列表中存在「启飞输入法」，
  开关是开的；
- 但客户端日志每次启动都打印：
  `This process is not trusted! Input event monitoring will not be possible...`
- 程序里 `AXIsProcessTrusted()` 始终返回 `False`；
- 手动把包内 `Contents/MacOS/QifeiClient` 也加进列表，依然无效。

**根因（用日志实锤）**

```
# /usr/bin/log show --predicate 'process == "tccd"'
static code for: identifier /Applications/启飞输入法.app/Contents/MacOS/QifeiIME, type: 1
```

`type: 1` 表示 TCC 把这个进程识别成**路径身份**，而不是签名身份。
再结合设置面板自己的日志：

```
kTCCServiceAccessibility  cn.qifei.ime                                  full
kTCCServiceAccessibility  /Applications/.../MacOS/QifeiIME              none
```

用户打开的是 `cn.qifei.ime` 这一条，TCC 查询的是「路径」这一条，永远不匹配。

路径身份是因为**外层 .app 签名无效**：原先的 `build_mac.sh` 手工拼 bundle，
把 `_internal` 整个塞进 `Contents/Frameworks`，还把 `assets/`、`hot.txt`、
`LLM/`、`logs/` 放进 `Contents/MacOS`。Apple 的签名规范要求：

- `Contents/MacOS` / `Contents/Frameworks` 里**只能放代码**；
- 数据文件必须放 `Contents/Resources`，再由交叉链接提供给程序；
- 否则 `codesign` 直接拒绝签名：
  `code object is not signed at all. In subcomponent: Contents/MacOS/assets/icon.png`

没有有效签名 → TCC 退化为路径身份 → 授权条目的 identifier 永远匹配不上。

**修复**

1. 不再手工拼目录，改用 PyInstaller 的 `BUNDLE`（见 `build/qifei_mac.spec`）：
   它按 Apple 规范分类代码/数据、创建交叉链接，并用 `codesign --deep`
   做 ad-hoc 签名，签名标识取 `CFBundleIdentifier`（`cn.qifei.ime`）。
2. 运行时数据根目录改为 `sys._MEIPASS`（即 `Contents/Frameworks`），因为
   `Resources` 里的数据会被交叉链接到这里：
   - `config_client.py` / `config_server.py` 的 `BASE_DIR`
   - `qifei/paths.py` 的 `asset()` 优先查 `_MEIPASS`
3. 日志移出 bundle，写 `~/Library/Application Support/QifeiIME/logs`
   （见 `core/logger.py`）。**运行时往包内写文件会破坏 CodeResources，
   下次启动签名校验失败，TCC 授权又失效。**
4. 重新打包后如果系统里还留着旧条目，用
   `tccutil reset Accessibility cn.qifei.ime` 清掉，再让程序重新登记。

验证标准：

```bash
codesign -dv "启飞输入法.app"      # Identifier=cn.qifei.ime, Info.plist entries=N
codesign --verify --deep --strict "启飞输入法.app"   # 通过
```

之后客户端日志应出现：`辅助功能权限: 已授权`。

### 2. pynput 的 `darwin_intercept` 传进来的是裸 CGEventRef

**现象**

- 辅助功能已授权（日志 `已授权`），`pynput` 也不再打印 not trusted；
- 但按 Tab 毫无反应，日志里没有任何按键事件。

**根因**

```python
code = int(event.getIntegerValueField_(Quartz.kCGKeyboardEventKeycode))  # 错
```

`darwin_intercept(event_type, event)` 里的 `event` 是裸 `CGEventRef`，
**没有** `getIntegerValueField_` 这种 pyobjc 方法形式。每次按键都抛
`AttributeError: 'CGEventRef' object has no attribute 'getIntegerValueField_'`，
又被 `except Exception: return event` 静默吞掉 —— 表面上监听器「已启动」，
实际一个键都处理不了。

**修复**（`core/client/shortcut/shortcut_manager.py`）

```python
code = int(Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventKeycode))
...
Quartz.CGEventGetIntegerValueField(event, Quartz.kCGKeyboardEventAutorepeat)
```

修复后在源码上验证：合成 Tab 事件 → 日志出现
`[tab] 触发：开始录音` / `录音状态已更新: recording=True`。

### 3. 麦克风未授权时 CoreAudio 返回「静音」，不是报错

**现象**

- 录音能开始、能保存 WAV，识别结果只有一个 `。` / `.`；
- 自己用 sounddevice 录 5 秒，peak 和 RMS 都是 `0.000`（数字静音）；
- 设备列表里麦克风存在，系统输入音量不是 0。

**根因**

TCC 日志显示麦克风请求结果是 `authValue=1`（未授权/未决定）。
macOS 在未授权时**不会让 PortAudio 报错**，而是照常打开流、给全 0 数据。
所以「音频流已启动」不代表真的能收到声音。

**处理**

- 必须让**打包后的 App**（有独立 TCC 身份）在第一次开流时弹出
  「允许“启飞输入法”访问麦克风？」并点「允许」；
- 在终端里直接跑 Python 或从终端启动客户端，继承的是终端进程的授权，
  终端没授权麦克风时同样是静音，不能用来判断硬件好坏；
- 判断方法：`/usr/bin/log show --predicate 'process == "tccd"'`
  里看 `kTCCServiceMicrophone` 的 `authValue`（2=允许，1=未决定，0=拒绝）。

---

## 三、其他已修复的问题

| 问题 | 修复位置 |
| --- | --- |
| `keyboard` 库是 Windows 专用（macOS 上 import 就失败） | 新增 `core/tools/keyboard_compat.py`，macOS 走 pynput；所有调用点替换 |
| 模拟粘贴用 `ctrl+v` | macOS 使用 `cmd+v`（`llm_get_selection.py` 等按平台判断） |
| macOS 默认用「打字」上屏不可靠 | `text_output.py` 在 Darwin 上强制走剪贴板粘贴 |
| `os.startfile` 在 macOS 不存在 | 新增 `core/tools/open_path.py`（`open` / `xdg-open`） |
| 鼠标/键盘监听只有 win32_event_filter | 新增 darwin 分支：`pynput.Listener(darwin_intercept=...)` + `DARWIN_KEYCODES` 键码表 |
| 配置/日志目录是 Windows 风格 | `qifei/paths.py`、`config_override.py` 改为 `~/Library/Application Support/<APP_ID>` |
| 开机自启只支持注册表/启动目录 | `qifei/autostart.py` 增加 LaunchAgent（`~/Library/LaunchAgents/cn.qifei.ime.plist`） |
| 打包后 supervisor 找 `QifeiServer.exe` | `qifei/supervisor.py` 按平台拼可执行文件名 |
| 服务端自检端口误报「端口冲突」 | `_check_port()` 增加 `SO_REUSEADDR`（与 `websockets.serve()` 一致）；macOS 重启时旧连接在 TIME_WAIT 会导致裸 bind 失败 |
| 无 stdin 时 `input("按回车键退出")` 永久阻塞 | 仅在 `sys.stdin.isatty()` 时等待回车 |
| Python 3.9 下 `staticmethod` 对象不可调用 | `core/server/engines/factory.py` 用 `getattr(loader, '__func__', loader)` |
| 启动时扫描 `~/Downloads` 触发 TCC 弹窗并卡死 | `llmkit/registry.py` 的 `default_scan_dirs()` 移除 `~/Downloads` |
| 系统默认输入设备是 `TinyUSB DFU runtime`（只出静音） | 新增 `client.audio_device` 配置，用名称片段或序号指定真实麦克风 |
| 未检测到 FFmpeg 时录音保存为 WAV | 正常降级，不影响识别 |

---

## 四、排障工具箱（macOS）

### 1. `log` 命令的坑

zsh 有同名内建命令 `log`，直接写 `log show ...` 会报
`log:1: too many arguments` 且没有任何输出。**必须写 `/usr/bin/log`。**

```bash
/usr/bin/log show --last 10m --style compact --predicate 'process == "tccd"' \
  | grep -i -E "Qifei|Accessibility|Microphone"
```

关注字段：

- `AUTHREQ_ATTRIBUTION`：`responsible=` / `accessing=` 分别是哪个进程；
- `AUTHREQ_RESULT`：`authValue=2` 允许，`1` 未决定，`0` 拒绝；
- `static code for: identifier ..., type: 0/1`：0=签名身份，1=路径身份（异常）。

### 2. 设置面板真正存了哪些授权

```bash
/usr/bin/log show --last 10m --predicate 'process == "SecurityPrivacyExtension"' \
  | grep "kTCCServiceAccessibility" | grep -i qifei
```

输出形如 `new entry: kTCCServiceAccessibility cn.qifei.ime full`，
可以确认列表里到底是哪一条、状态是什么。

### 3. 用 AX API 读系统弹窗（本机终端已授权时可用）

```python
import ApplicationServices as AS
from AppKit import NSWorkspace
for app in NSWorkspace.sharedWorkspace().runningApplications():
    if 'universalAccessAuthWarn' in (app.executableURL().path() or ''):
        el = AS.AXUIElementCreateApplication(app.processIdentifier())
        err, wins = AS.AXUIElementCopyAttributeValue(el, 'AXWindows', None)
        # 递归读 AXTitle / AXValue / AXChildren
```

### 4. 不按键也能测快捷键

先用 `AXIsProcessTrusted()` 确认当前进程有辅助功能，然后合成事件：

```python
import Quartz
for down in (True, False):
    e = Quartz.CGEventCreateKeyboardEvent(None, 48, down)  # 48 = Tab
    Quartz.CGEventPost(Quartz.kCGHIDEventTap, e)
```

再对照客户端日志里是否出现 `[tab] 触发：开始录音`。
用这个方法可以区分「权限问题」和「拦截代码问题」。

### 5. 麦克风到底有没有声音

```python
import sounddevice as sd, numpy as np
s = sd.InputStream(device=1, channels=1, samplerate=16000); s.start()
a, _ = s.read(16000 * 3); s.stop()
print(float(np.abs(a).max()), float(np.sqrt((a**2).mean())))
```

`peak == 0.000` 基本就是 TCC 未授权或设备本身静音；真实麦克风至少有底噪。

### 6. 独立验证服务端 ASR（绕开麦克风与键盘）

向 `ws://127.0.0.1:6016` 发 JSON（`core/protocol.py::AudioMessage`）：

- `subprotocols=["binary"]`，否则握手 400；
- `data` 是 base64 的 **float32 / 16kHz / 单声道** PCM；
- 先发若干 `is_final=False` 的分片，最后发一个 `data=""`、`is_final=True`。

已验证通过：用 `say` 生成 TTS → afconvert 转 16k 单声道 → 发送，
返回逐字正确的「今天下午3点开会，请把项目进度报告发给我。」

---

## 五、构建与安装

```bash
cd /path/to/qifei-ime

# 只出 .app，不生成 dmg
QIFEI_SKIP_DMG=1 PYTHON="$PWD/.venv/bin/python" bash build/build_mac.sh

# 安装（ditto 保留签名与符号链接）
rm -rf "/Applications/启飞输入法.app"
ditto "dist/启飞输入法.app" "/Applications/启飞输入法.app"
open -n "/Applications/启飞输入法.app"
```

注意：

- **每次重新构建都会改变 ad-hoc 签名的 cdhash**，TCC 授权（辅助功能、
  麦克风）随之失效，需要在系统设置里重新授权一次。要长期避免，可以换成
  自签名证书（稳定 Team/identifier），但需要钥匙串操作。
- 构建后不要用 `codesign --deep` 反复重签已签好的包；`BUNDLE` 已经签过。
- `build_mac.sh` 会在收集阶段剔除 onnxruntime 的示例 `.onnx` 和
  sounddevice 的 Windows `.dll`；不要在签名后再删包内文件。

---

## 六、当前状态（2026-11-15 收尾）

已完成并验证：

- [x] 打包版服务端 ASR 端到端（TTS 音频 → WebSocket → 识别结果逐字正确）
- [x] 合规的 .app 布局与 ad-hoc 签名（`codesign --verify --deep --strict` 通过）
- [x] TCC 身份从路径身份纠正为签名身份 `cn.qifei.ime`
- [x] 辅助功能授权后日志出现 `辅助功能权限: 已授权`
- [x] Tab 触发录音（源码修复后验证：`[tab] 触发：开始录音`）
- [x] 上屏链路（`已复制文本到剪贴板` → `已发送粘贴命令` → 活动窗口 `textedit`）
- [x] 打包版已包含上述修复（21:20 构建）

尚未完成（需要在机器前操作）：

- [ ] 在麦克风弹窗点「允许」（没有它识别结果只有标点）
- [ ] 重新打包后，在「系统设置 → 隐私与安全性 → 设备控制和数据访问」
      中重新打开「启飞输入法」（cdhash 变了）
- [ ] 真人说话 + Tab 的最终验收（按 Tab → 说话 → 再按 Tab → 文字进入当前窗口）

**2026-11-15 收工时的实测状态**：打包版按 Tab 仍然没有任何反应，
日志里也没有 `[tab] 触发`。也就是说「源码下已验证的拦截修复」在打包环境
里还没有跑通，**下次开工要先定位这一环，而不是再去检查麦克风**。

下次开工的排查顺序（每步都能用日志一刀切干净）：

1. `/usr/bin/log show --last 5m --predicate 'process == "QifeiClient"'`
   确认是不是又回到 `This process is not trusted!`。
   若出现 → 仍 TCC 未授权，回到本文第二节第 1 条重走授权；
   若没有 → 权限已 OK，继续第 2 步。
2. 看 `~/Library/Application Support/QifeiIME/logs/client_latest.log`
   是否有 `辅助功能权限: 已授权`、`键盘监听器已启动（macOS darwin_intercept）`。
3. 有监听器但按 Tab 无日志 → 用第四节第 4 条的合成 Tab 事件再试一次：
   - 合成键也无反应 → 拦截器本身没跑起来（可能打包版 pynput/pyobjc
     的 darwin_intercept 未生效，或被 PyInstaller 收集成了别的分支）；
   - 合成键有反应、手按无反应 → 事件 tap 的权限/层级问题。
4. 顺便确认 `qifei.log` 里 GUI 拉起的是 `Contents/MacOS/QifeiClient`，
   而不是终端里跑的另一份客户端（两份客户端会抢同一个端口/设备）。

> macOS 26 的两个名称容易找错：
> - `辅助功能`（Accessibility）在隐私列表里叫 **「设备控制和数据访问」**；
> - 输入监控仍叫 **「输入监控」**，它就在上面那一项的下一行。
