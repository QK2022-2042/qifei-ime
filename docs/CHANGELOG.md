# 更新日志

## v2.15（发布版本号 2.0.4）

本轮两件事合并发版：**v2.14 继续深查「大模型设置」修掉远程后端模型名入口缺失**；**v2.15 针对「安装包下载被杀毒软件拦截」做只读内容体检、评估瘦身、补 PE 版本资源、把 exe 压缩打包放桌面**，发行版本升级到 2.0.4。

### 一、v2.14：远程 API / Ollama 缺「服务方模型名」输入入口（真实缺陷）

- **复现**：新增 `.tmp/verify_remote_model_input_v214.py`，真实离屏 Qt 页面 + 最小合法 GGUF 头，修复前 **30 项中 22 项失败** —— 配置 `backend=openai`、`model=deepseek-chat` 时，只要误点下拉里的本地 `.gguf`，`llm.model` 就被冲成 `qwen2.5-1.5b-instruct-q4_k_m`、`llm.model_path` 被塞成本地路径；远程后端根本没有填写服务方模型名的地方
- **修复**：`qifei/llm_page.py` 新增 `edit_remote_model` 输入框 / `lbl_remote_model` / `local_file_row` / `local_dir_row`；`_remote_model_text()` 只影响显示不写盘、`_on_remote_model()` 只写 `llm.model` 绝不碰 `llm.model_path`；`refresh()` 对 `openai` 传 `include_gguf=False`；`_on_model()` 对 `kind == 'gguf' and backend != 'local'` 直接 return；`_test()` 缺模型名时提示「还没填服务方模型名」；`_append_dir()` 按 `norm_path()` 去重并**返回新列表**（原地改列表会让调用方「长度变化」判据失效，导致配置写不进去）
- **错误提示**：`llmkit/connector.py::chat_test()` 的 `model_missing` 按 `backend.kind` 分流，远程后端不再把用户误导去选本地 `.gguf`
- **结果**：修复后 **30/30 `REMOTE_MODEL_INPUT_V214_OK`**；`.tmp/verify_remote_backend_v213.py` 旧断言按 `openai` / `ollama` 分支更新后 **26/26**；历史回归 v2.12 29/29、v2.11 20/20、换电脑 20/20、`tests/test_llm_kit.py` 全过、只读编译 335 个 `.py` 0 错误

### 二、v2.15：安装包只读体检与杀软误报定位

- 新增 `.tmp/inspect_installer_v215.py`，零副作用读 2.0.3 基线包：payload 314 文件 / 1196.52 MB，未压缩 1389.83 MB，其中 `.gguf` 1065.56 MB（76.67%）、`.dll` 225.54 MB（16.23%）、`.pyd` 66.96 MB、`.exe` 23.98 MB；无日志 / 缓存 / 备份 / 临时文件；内嵌 payload md5 与磁盘一致
- **误报诱因（全部来自 `installer_app.py` 真实代码，不是推测）**：未签名 exe + PyInstaller onefile 自解压、无 `VS_VERSION_INFO`、1.2GB 单文件内嵌多个 PE/DLL、`taskkill /F /IM`、`powershell -Command` + COM 建快捷方式、`winreg` 写 HKCU 卸载项、`uninstall.bat` 的 `reg delete` / `rmdir /s /q`、运行时解压大 payload
- **结论**：根治是 Authenticode 代码签名证书（本轮做不到）；可做的是补版本资源、zip 分发、SHA256 与说明、白名单申报

### 三、瘦身评估与落地（不削弱功能）

- 新增 `.tmp/measure_setup_compression.py` 实测：`zip-stored` 1207.66 MB（0.00%）、`zip-deflate6` 1207.42 MB（省 0.25 MB / 0.02%）、`zip-deflate1` 省 0.23 MB —— **外层压缩不能瘦身**，价值是分发形态；GGUF 是已量化权重、Setup 内部已压过一层
- 决策：不移除内置 1.04GB 模型（会破坏「第一次安装就有大模型可用」硬需求）、不删 `ggml-vulkan.dll`（不启用 `QIFEI_LLAMA_MINIMAL=1`，保留 GPU 后端能力）；只做无损瘦身
- `installer/build_installer.py` 新增 `SKIP_SUFFIXES` 排除 `.lib/.a/.exp/.pdb/.ilk/.idb/.pyc/.pyo/.bak/.old/.tmp/.part/.log`，非 GGUF 文件 `compresslevel=9`，GGUF 仍 `ZIP_STORED`
- `installer/file_version_info.txt` 新增 `VSVersionInfo`（CompanyName / ProductName / FileVersion 2.0.4.0 / LegalCopyright），`build_setup()` 传 `--version-file`
- **实测**：2.0.4 payload **312 文件 / 1195.83 MB**（2.0.3 是 314 / 1196.52 MB），构建日志「已排除 2 个开发期 / 临时文件」；Setup.exe **1265639733 bytes / 1207.01 MB**

### 四、exe 后压缩打包放桌面

- 新增 `tools/make_setup_zip.py`：桌面 `启飞输入法-2.0.4-安装包-压缩版.zip`，内含 exe、`SHA256SUMS.txt`、`校验与安装说明.txt`，`--check` 校验 CRC / 解压 exe 哈希 / 版本 / 误报指引
- 实测：源 exe 1265639733 bytes → 压缩包 1265383810 bytes（**省 255923 bytes / 0.020%**，48.6s），`SHA256 = 3b6bcdb7e9c4aaad6fd6b09cedffecd9d726313a9e20f49e7cf77bc208787e48`，**9/9 `SETUP_ZIP_OK`**

### 五、版本升级与真构建

- 三处 `VERSION` 全部 **2.0.4**（`qifei/brand.py`、`installer/installer_app.py`、`installer/build_installer.py`）；使用说明 / macOS 说明 / Mac 工具 / `build_mac.sh` / frozen 与真启动验证脚本全部跟随
- **构建环境坑**：`run_in_background` 模式下沙箱放行未生效，PyInstaller `find_binary_dependencies()` 的 `isolated.Python()` 启动子进程仍被拦（`PermissionError: [WinError 5]`，`BUILD_ALL_EXIT=1`）。前台放行环境先用 `isolated.Python()` 探针确认 `ISOLATED_PROBE_OK`（子进程 PID 与主进程不同），再前台重跑 `build_all.py --installer`，**124s / `BUILD_ALL_EXIT=0`**
- 构建日志实证 `Copying version information to EXE`；Windows API 真读 `GetFileVersionInfoSizeW = 1668`、`CompanyName=Qifei`、`FileVersion=2.0.4.0`、`ProductVersion=2.0.4.0`。体检脚本原先扫 ASCII 特征串误报「无版本资源」，已改为走 `version.dll` 真读 `RT_VERSION`

### 六、2.0.4 全套真验证

- `tools/verify_release.py` **49/49 `RELEASE_VERIFY_OK`**；只读体检 `INSPECT_INSTALLER_V215_OK`（内嵌 payload 与磁盘 md5 一致 `22d2187f…`）
- frozen 字节码 **新包 42/42 `FROZEN_RELOCATION_OK`**（34 条历史 + 8 条 v2.14 独有断言）；归档的 2.0.3 绿色版反证 **33/42**，失败 9 项正好是版本号 2.0.4 + 8 条 v2.14 断言
- 「另一台电脑」模拟 **16/16 `OTHERPC_OK`**；全新目录首次静默安装 `FRESH_INSTALL_EXIT=0`（312 文件）后 `.tmp/bundled_model_check.py` **16/16 `BUNDLED_MODEL_OK`**，随包 DLL + 内置 1.04GB 模型 `REAL_MODEL_RESULT=PASS`（加载 1.3s、直接生成、SSE、运行时对话）
- 真机覆盖安装 `COVER_INSTALL_EXIT=0`，**「保留 1 个用户模型，未覆盖」**，快捷方式 / 卸载项写回正式目录；覆盖后真实安装目录 frozen **42/42**、E2E **20/20 `REAL_INSTALL_E2E_OK`**（`server→auto`、真加载 8.0s、SSE 非空回答、大模型页自动选中 Qwen）、真启动 frozen GUI **7/7 `INSTALLED_GUI_OK`**（日志「本地模型预热：成功」、进程清理干净）
- 桌面 macOS 无大模型源码包同步到 2.0.4 并 `--check`；真实 `.app` / `.dmg` 仍必须在 macOS 主机 / CI runner 执行 `bash build/build_mac.sh`（PyInstaller 不支持 Windows → macOS 交叉编译）

### 七、交付后一致性复核（分发脚本两个真实缺陷）

交付后又做了一轮只读复核，发现并修掉两个只影响**分发说明 / 分发脚本**、不影响程序本体的问题：

- **说明文档里写死了 2.0.3 的 payload 文件数**。`tools/make_setup_zip.py::readme_text()` 原句为「payload 314 个文件」，而 2.0.4 无损瘦身后实际是 **312**。该文本会打进桌面压缩包的《校验与安装说明.txt》，实测已交付的旧包确实带 `314`（`DOC_HAS_314=True / DOC_HAS_312=False`）。修复：新增 `payload_entry_count()` 从 `installer/payload.zip` 真读 `namelist()` 数量，`readme_text()` 按计数动态生成；payload 不在时退化为「payload 全部为程序本体…」的不带数字措辞，绝不编造。`--check` 新增第 10 条断言「说明文档 payload 文件数 == 构建产物实测值」，以后版本升级 / 瘦身后会自动抓到脱节
- **反证先行**：用修好的脚本对旧包跑 `--check`，得 **9/10 `SETUP_ZIP_FAILED`**，唯一失败项正是「payload.zip 实测 312」——证明新断言不是摆设；重新生成后 **10/10 `SETUP_ZIP_OK`**，幂等复跑仍 10/10
- **重出压缩包时踩到跨卷归档 bug**：桌面在 `C:`、项目 `.tmp/trash` 在 `D:`，原 `Path.rename()` 直接把旧包挪过去会抛 `OSError: [WinError 17] 系统无法将文件移到不同的磁盘驱动器`（首次交付时恰好未触发同名归档）。修复：新包先写同目录 `.tmp`，完成后 `os.replace()` 原子替换；旧包先在本卷改名为 `.old`，新产品就位后再用 `shutil.move()` 跨卷归档（跨文件系统自动退化为 `copy2 + unlink`）。同时避免 1.2GB 压缩中途失败留下半截 zip 盖掉已交付物
- **内容未变实证**：源 exe 与包内 exe 均 `1265639733 bytes`，SHA256 仍是 `3b6bcdb7e9c4aaad6fd6b09cedffecd9d726313a9e20f49e7cf77bc208787e48`；说明文档 `DOC_HAS_312=True / DOC_HAS_314=False`；桌面无 `.tmp` / `.old` 残留，含旧数字的旧包归档在 `.tmp/trash` 可回看。**exe / payload / Setup 均未改动，无需重构建、无需重跑发版自检**；只读 `COMPILE_OK`、精确正则 `PRECISE_CHECK_OK=True`（动态分支输出 `payload 312 个文件`，降级分支不带数字）
- CHANGELOG 里仍出现的 `payload 314` 位于第二节「2.0.3 基线包体检数据」，是应保留的历史事实记录，不是产品文案

### 八、仍然成立的边界

- 未签名 exe 的杀软误报只能**降低风险**，不能保证所有第三方杀软不报；根治需代码签名证书
- 真实物理「另一台电脑」仍需目标机器安装新 Setup；人工按住快捷键说话并确认上屏仍需真人操作

## v2.13（发布版本号 2.0.3）

本轮主线：**交付 2.0.2 后再深查一轮设置页剩余的写盘 / 弹窗路径，发现并修掉「远程 API / Ollama 后端被本地模型配置污染」和「用户主动选的后端重启被打回」两个真实回归**，并按约定把发行版本升级到 2.0.3。

- **根因一：`_on_scan_done()` 自动默认选择漏了 `backend == 'local'` 条件**。v2.9 的 CHANGELOG 就写过「只在 `backend=local` 时填，切到远程 API / Ollama 不会被改回去」，v2.12 重写本方法时把这个条件丢了。而 `refresh()` 调 `llmkit.discover()` 时没有传 `include_gguf=False` —— `discover()` 不管后端是什么都返回本机 `scan_gguf()` 结果，所以远程后端下模型下拉框照样被内置 `.gguf` 填满。用真实离屏 Qt 页面复现：配置 `backend=openai`、`model=deepseek-chat`、`model_path=''`，光是打开一次设置页，`llm.model` 就被冲成 `qwen2.5-1.5b-instruct-q4_k_m`、`llm.model_path` 被塞成本地 gguf 路径，并 `_apply()` 重启客户端；`ollama` 后端同样中招。修复：自动默认选择分支改为 `elif backend == 'local' and not model_path and ...`，只有「直连 llama.cpp」且从未配置过模型时才自动选并落盘；远程 API 提示同步改为「不需要本地模型文件，也不会自动改你填的模型名」
- **根因二：`Store.reload()` 的 v2.8 一次性迁移会把用户主动选的 Ollama 打回 `local`**。老实现只在 `backend == 'ollama'` 的分支里写 `app.llm_backend_migrated`，于是默认 `local` 的配置永远没有这个标记；用户后来在界面里主动选回 Ollama，下次启动仍满足 `not migrated and backend == 'ollama'`，被静默改回 `local`。修复：`Store.reload()` 改为「没有标记就补标记」，只有真正从老版继承下来的 ollama 默认值才做迁移；`LLMPage._on_backend()` 在界面明确选过后端时也补一次标记（`save=False`，与紧接着的 `_save('llm.backend', ...)` 一次落盘）。保留 v2.8 对老配置的迁移语义，且只迁一次
- **根因三：`_on_download_done()` 在远程后端点「下载推荐润色模型」会覆盖 `llm.model`**。这个键在远程模式下是服务方的模型名。修复：只在 `backend == 'local'` 时写 `llm.model_path` / `llm.model`；其他后端只提示文件已就位、模型名保持原样、并引导切回「直连 llama.cpp（推荐）」
- **专项验证**：新增 `.tmp/verify_remote_backend_v213.py`，用真实离屏 Qt 页面 + 最小合法 GGUF 头，覆盖 openai / ollama 两种后端、用户主动选 Ollama 后重启、下载完成回调、老配置迁移语义、以及 `local` 全新用户仍自动选中推荐模型的对照，**25/25 `REMOTE_BACKEND_V213_OK`**。修复前同脚本复现出 7 项失败（`llm.model` / `model_path` / 内存 Store 被改写、ollama 后端被打回 local），证明不是理论推测
- **回归**：`.tmp/verify_llm_settings_v212.py` 29/29、`.tmp/verify_v211_recognition.py` 20/20、`.tmp/qt_relocation_check.py` 20/20、`tests/test_llm_kit.py` 全过、只读 `compile()` 334 个 `.py` 0 错误 `COMPILE_OK`
- **版本同步**：`qifei/brand.py`、`installer/installer_app.py`、`installer/build_installer.py`、`tools/make_mac_source_pack.py`（默认输出 / ZIP_ROOT / 先读我 / 版本断言）、`build/build_mac.sh`、`docs/macOS 构建与安装说明.md`、`docs/启飞输入法使用说明.md`，以及 frozen / 真启动验证脚本全部跟随 2.0.3
- **frozen 断言扩到 34 项**：`.tmp/verify_frozen_relocation.py` 补 5 条 v2.13 独有断言（`llm_backend_migrated` 出现在设置页 / 「当前后端不使用本地 .gguf」/「想用这个文件请切回」/「推荐模型文件已就位」/「也不会自动改你填的模型名」），并先用 2.0.2 旧包反证区分度
- **重出 2.0.3 正式包并全套真验证**：归档 2.0.2 旧 Setup 与绿色版后，`build_all.py --installer` 7m13s 退出码 0，产出 `dist/启飞输入法-2.0.3-安装包.exe`（1207.7 MB / 1,266,328,059 bytes）/ payload 1196.5 MB / 314 文件；随包使用说明在源码 / 绿色版 / payload 三处字节一致（14530 bytes / md5 `ff35d60f62a9cc43186c73f6d7dca0d9`）。`tools/verify_release.py` **49/49 `RELEASE_VERIFY_OK`**；frozen 断言**新包 34/34、归档的 2.0.2 旧包 28/34**（失败 6 项正好是版本号 2.0.3 + 5 条 v2.13 独有断言）；新包模拟「另一台电脑」16/16；新包随包 DLL + 内置 1.04GB 模型 `REAL_MODEL_RESULT=PASS`（加载 1.3s、直接生成、SSE「测试成功。」、运行时对话）
- **全新目录首次安装实测（用户最早的硬需求）**：2.0.3 Setup 静默装到全新目录，314 文件、退出码 0，内置模型 1,117,320,736 bytes 且 `GGUF` 魔数正确；`.tmp/bundled_model_check.py` **16/16 `BUNDLED_MODEL_OK`**（模型大小 / 魔数 / md5 与源文件一致、`validate()` 命中对话模板、`Store.reload()` 自动填 `model_path` / `model` 且 `backend=local`、随包 DLL 真实推理 PASS）。修掉验证脚本自身一处假失败：产品约定 `llm.model` 存 `Path(...).stem`，旧断言拿带 `.gguf` 的完整文件名比较。首次安装命令曾被 SIGTERM 中断成半成品、后台任务状态又被沙箱审计标成 `failed`，最终以 `install.log` / 关键文件 / 模型大小与魔数等真实文件系统证据判定成功，未采信任务状态
- **真机覆盖安装与覆盖后全套验证**：同一 Setup 覆盖安装回正式目录 23s 退出码 0（解压 313 文件、保留 1 个用户模型、快捷方式与卸载项写回正式目录，顺带恢复此前临时目录静默安装污染的注册表项）。随后在**真实安装目录**复跑：frozen **34/34**、另一台电脑模拟 **16/16**、`.tmp/verify_real_install_e2e.py` **20/20 `REAL_INSTALL_E2E_OK`**（`server.ensure()` 在坏值下真加载 1GB 模型 4.4s 并回落进程内引擎、SSE 流式非空回答、大模型页自动选中 Qwen）、真启动 frozen GUI **7/7 `INSTALLED_GUI_OK`**（进程起得来、`engine_mode` 自愈为 `auto`、模型仍是 `chat_ready=True` 的对话模型、日志「本地模型预热：成功」、本次启动无新加载/依赖错误、进程清理干净）、安装目录随包 DLL + 1GB 模型 `REAL_MODEL_RESULT=PASS`（加载 1.2s、SSE「测试成功。」、`LLMProcessor`「OK」）
- **v2.13 迁移标记的真机实证**：真启动前用户真实主配置 `app.llm_backend_migrated` 是 `None`（默认 `local` 配置从没写过标记），真启动冻结 GUI 后变成 `True`；`engine_mode` 从脚本临时写回的坏值 `server` 自愈为 `auto`，`model_path` 仍是用户已选的 `D:\Programs\QifeiIME\models\llm\qwen2.5-1.5b-instruct-q4_k_m.gguf`（1,117,320,736 bytes，`chat_ready=True`）。这条直接证明「没标记就补标记」的修复进了本机装的那个 exe，而不是只停在源码
- **桌面 macOS 无大模型源码包同步到 2.0.3**：重生成 `~/Desktop/启飞输入法-2.0.3-macOS源码包.zip`（2.27 MB / 375 文件）并 `--check` **6/6 `MAC_SOURCE_PACK_OK`**（0 个模型 / 二进制 / 缓存、0 个构建产物目录、关键文件齐全、包内 `qifei/brand.py` 是 2.0.3、CRC 通过、包体 < 60MB）；包内 `docs/CHANGELOG.md` / 使用说明随定稿同步。真实 `.app` / `.dmg` 仍必须在 macOS 主机或 macOS CI runner 上执行 `bash build/build_mac.sh`；PyInstaller 不支持 Windows → macOS 交叉编译，Windows 上不伪造可安装的 Mac 包

## v2.12（发布版本号 2.0.2）

本轮主线：**系统复查「大模型设置」整条链路，把会导致用户模型被悄悄改掉、以及装到别的电脑识别不了的真实错误全部修掉**，并把发行版本升级到 2.0.2。

- **版本号统一升级到 2.0.2**：`qifei/brand.py`、`installer/installer_app.py`、`installer/build_installer.py` 三处 `VERSION` 同步；`installer/build_installer.py` docstring、`build/build_mac.sh`、`docs/macOS 构建与安装说明.md`、`docs/启飞输入法使用说明.md`、`tools/make_mac_source_pack.py`（默认输出 / ZIP_ROOT / 先读我 / 版本断言）、`tools/verify_release.py` 注释、`.tmp/verify_frozen_relocation.py`、`.tmp/verify_launch_installed_gui.py`、`.tmp/verify_newpack_otherpc.py` docstring 全部跟随
- **根因一：符号链接 / junction 让「同一个文件」被判成两个路径**。`norm_path()` 原先用 `os.path.abspath()`，不解析链接；而 `app_root()` 与 `scan_gguf()` 都用 `resolve()`。真机安装目录 `C:\Users\admin\AppData\Local\Programs\QifeiIME` 是指向 `D:\Programs\QifeiIME` 的链接，配置里存链接写法时，扫描结果（真实路径）与配置字符串不相等。真启动 frozen GUI 后确实观察到 `model_path` 被从 `C:\...` 改写成 `D:\Programs\...`。修复：`norm_path()` 改用 `os.path.realpath()`（对不存在的路径也能安全返回规范化绝对路径），新增 `llmkit.same_path()`（realpath 字符串比较 + `os.stat()` 的 `(st_dev, st_ino)` 文件 identity 兜底）并导出；`qifei/store.py::reload()`、`qifei/llm_page.py` 的路径比较与 `_warning_for()` 全部改用它
- **根因二：打开一次设置页就会把用户人工选好的模型悄悄换成内置模型**。`_on_scan_done()` 在配置路径没匹配上扫描结果时无条件 fallback 选内置模型并调 `_on_model()`，而 `Store.set()` 默认立即写盘 —— 只要路径写法不同、安装目录是链接、或模型所在盘当时没挂载，用户选择就丢了。修复：`_save()` 支持 `save=False`、`_on_model()` 支持 `persist`，恢复选中（`_restoring=True`）只刷界面绝不写盘；当前配置的模型文件真实存在但没被扫到时，作为「当前配置 · 未加入扫描目录」加入下拉框并选中；只有「从来没有配置过 `model_path`」才自动挑默认对话模型并持久化
- **根因三：自动默认选择可能落到 ASR / 非对话模型**。新增 `_auto_pick_index()`：优先随包推荐对话模型，其次第一个 `chat_ready=True` 的 `.gguf`；一个都没有就返回 `-1` 等用户手选，不再退回内置模型或误选 ASR 模型
- **根因四：设置页仍能直接选「外部 llama-server.exe」这个安装包根本不提供的模式**。`combo_engine` 原先直接 `_save('llm.engine_mode', ...)`，能把必然失败的坏值写进当前会话。修复：改连 `_on_engine()`，选中 `server` 时先调 `llmkit.server.find_server_exe()` 校验；找不到就弹窗说明「安装包自带进程内 llama.cpp 引擎，不包含外部 llama-server.exe，也不需要你另外安装任何程序」，回落到 `auto` 并只保存 `auto`。该选项文案同步标为「（高级）/ 安装包不含此 exe，需自行放置」
- **界面提示**：当前模型不在扫描目录里时明确写出「已作为『当前配置』保留在下拉框，不会被自动替换」，并引导用「添加扫描目录」让它每次自动出现
- **专项验证**：新增 `.tmp/verify_llm_settings_v212.py`，用真实离屏 Qt 页面 + Windows `mklink /J` junction + 最小合法 GGUF 头，覆盖路径等价、用户模型不被覆盖、链接写法不被改写、自动默认选择不误选非对话模型、`server` 陷阱、带 `llm_engine_migrated=True` 的脏配置自愈、`_warning_for()` 链接路径匹配，29/29 `LLM_SETTINGS_V212_OK`。旧 `norm_path()` 反证 `OLD_norm_path_equal=False` / 新实现 `True`。回归：`tests/test_llm_kit.py` 全过、`.tmp/verify_v211_recognition.py` 20/20、`.tmp/qt_relocation_check.py` 20/20、只读 `compile()` 322 个 `.py` 0 错误 `COMPILE_OK`
- **frozen 断言扩到 29 项并先反证**：`.tmp/verify_frozen_relocation.py` 补 11 条 v2.12 独有断言（`same_path` / `realpath` / `_auto_pick_index` / `_on_engine` / 「当前配置 · 未加入扫描目录」/「不会被自动替换」/「安装包不含此 exe」/ 客户端 registry 同步）+ 版本 2.0.2。用 2.0.1 旧包跑基线得 **17/29**，失败的 12 项正好全是新断言与版本号 —— 既证明断言有区分度，也证明修复只存在于源码、必须重出包
- **修真启动验证脚本的过严断言**：`.tmp/verify_launch_installed_gui.py` 原先要求 `model_path` 前后字符串完全一致，在链接安装目录上会假失败。改为 `llmkit.same_path()` 判同一物理文件 + 文件存在 + `scan_gguf()` 复核它仍是 `chat_ready=True` 的对话模型
- **重出 2.0.2 正式包并全套真验证**：归档 `dist/启飞输入法-2.0.1-安装包.exe` 后跑 `build_all.py --installer`（7m0s，退出码 0），产出 `启飞输入法-2.0.2-安装包.exe`（1207.7 MB）/ payload 1196.5 MB / 314 文件；随包使用说明在源码 / 绿色版 / payload 三处字节一致（14128 bytes / md5 `02a8d99a8e37b3242ec9b406cc57ed47`）。`tools/verify_release.py` **49/49 `RELEASE_VERIFY_OK`**；frozen 断言**新包 29/29、归档的 2.0.1 旧包 17/29**（失败 12 项正好是版本号 + 11 条 v2.12 独有断言）；新包模拟「另一台电脑」16/16；`tests/test_llm_real_model.py` 用安装版随包 DLL + 内置 1.04GB 模型 `REAL_MODEL_RESULT=PASS`（加载 1.3s、直接生成、SSE「测试成功。」、运行时对话）。真机静默覆盖安装 24s 退出码 0（解压 313 文件、保留 1 个用户模型、快捷方式与卸载项写回正式目录）后：真实安装目录 frozen 29/29、另一台电脑 16/16、`.tmp/verify_real_install_e2e.py` **20/20 `REAL_INSTALL_E2E_OK`**、真启动 frozen GUI **7/7 `INSTALLED_GUI_OK`**（日志「本地模型预热：成功」，本次启动无新加载/依赖错误）
- **顺带修两个验证脚本自身的缺陷**：`verify_real_install_e2e.py` 不再要求用户真实主配置恰好还留着 `server` 坏值（前几轮已把主配置自愈成 `auto`，改为在临时配置副本上复现，脚本可重复运行）；`verify_launch_installed_gui.py` 补 `sys.path` 设置，让从 `.tmp/` 运行时 `import llmkit` 能生效
- **桌面 macOS 无大模型源码包同步到 2.0.2**：`tools/make_mac_source_pack.py` 的默认输出 / ZIP_ROOT / 先读我 / 版本断言，以及 `build/build_mac.sh`、`docs/macOS 构建与安装说明.md` 全部跟随版本升级，重生成 `~/Desktop/启飞输入法-2.0.2-macOS源码包.zip` 并跑 `--check` 自检。真实 `.app` / `.dmg` 仍必须在 macOS 主机或 macOS CI runner 上执行 `bash build/build_mac.sh`；PyInstaller 不支持 Windows → macOS 交叉编译，Windows 上不伪造可安装的 Mac 包

## v2.11（发布版本号 2.0.1）

- **修「选择模型文件」不弹文件框的 UI 断点**：`qifei/llm_page.py::_import()` 重写为始终调用 `QFileDialog.getOpenFileName()`，保存选中路径与其父目录到扫描目录，按钮文案改为「选择模型文件（已下载好的）」，用户已经下载放好的 `.gguf` 有明确入口
- **任意名称的对话模型都能被自动识别**：`llmkit/registry.py::resolve_model_path()` 在无内置模型 / 按文件名找不到时，扫描候选目录挑第一个 `chat_ready=True` 的 `.gguf`；只有 ASR / embedding / 纯补全模型时不误选，返回空串等用户手选
- **`engine_mode='server'` 每次启动都自愈**：`Store.reload()` 不再依赖一次性的 `app.llm_engine_migrated` 标记，只要本机找不到 `llama-server.exe` 就把坏值改回 `auto`，带旧标记的老配置也能恢复
- **2.0.1 正式包与真机闭环**：`build_all.py --installer` 重出 `启飞输入法-2.0.1-安装包.exe`；`tools/verify_release.py` 49/49 `RELEASE_VERIFY_OK`、frozen 18/18、新包模拟「另一台电脑」16/16、随包 DLL + 随包内置模型真实推理 PASS；真机静默覆盖安装后新增 `.tmp/verify_real_install_e2e.py`（真实配置副本 + 真实安装目录 + 真实 1GB 模型）20/20 `REAL_INSTALL_E2E_OK`；`.tmp/verify_launch_installed_gui.py` 真启动 frozen GUI，确认 exe 起得来、真实主配置 `server` 被自愈为 `auto`、本次启动无新加载错误。该脚本第二版暴露 `model_path` 符号链接路径被改写，直接引出 v2.12 的路径比较修复

## v2.10（发布版本号 2.0.0）

本轮主线：**修掉「装到别的电脑上就不识别语言大模型」**，并把发行版本升级到 2.0.0、补齐 macOS 出包链路。

- **版本号统一升级到 2.0.0**：`qifei/brand.py`、`installer/installer_app.py`、`installer/build_installer.py` 三处 `VERSION` 由 `1.0.0` 改为 `2.0.0`；Setup 文件名随之变成 `启飞输入法-2.0.0-安装包.exe`。`docs/启飞输入法使用说明.md` 里的安装包名（3 处）、`docs/macOS 构建与安装说明.md`、`readme.md` 同步。`tools/verify_release.py` 不再硬编码 Setup 文件名，改为从 `installer/build_installer.py` 动态读版本号并新增 2 项断言：三处版本号一致、Setup 文件名与版本号一致——以后升级版本漏改一处会直接自检失败
- **根因定位：不是模型没扫到，是配置里的旧绝对路径**：`%APPDATA%\QifeiIME\config.json` 把 `llm.model_path` 写死成 `C:\Users\<旧用户名>\AppData\Local\Programs\QifeiIME\models\llm\xxx.gguf`。换电脑、换盘符、换用户名、把安装目录整体拷到别处后，这个路径必然失效；而客户端走 `connector.from_settings()` 不经过 `Store`，界面扫描能列出新目录下的 `.gguf`，真正去加载时却仍拿旧路径，于是表现成「能看到下载/选择路径的入口，就是不识别」。真机日志里还有第二个叠加故障：`engine_mode='server'` 会跳过 `_start_native()` 强制找外部 `llama-server.exe`，安装包里没有这个 exe，日志明确报「本地模型预热跳过：也没找到 llama-server.exe」
- **四层自愈，客户端不经过 GUI 也能恢复**：`llmkit/registry.py` 新增 `norm_path()`（统一大小写/斜杠/绝对相对）与 `resolve_model_path()`（失效路径按文件名在 `app_root()/models/llm`、`app_root()/models`、旧配置父目录、`extra_dirs`、默认扫描目录里重定位，最后退回随包内置模型）；`qifei/store.py::reload()` 把修复结果写回 `config.json` 并只迁移一次；`llmkit/connector.py::from_settings()` / `from_role()` 在构造后端时自行调 `_resolved_path()`；`llmkit/server.py::ensure()` 在真正加载前再兜一次，找不到模型时给出「这份配置可能来自另一台电脑」的可操作中文提示
- **`engine_mode='server'` 不再是死路**：`Store.reload()` 检测到该模式且本机找不到 `llama-server.exe` 时一次性迁移为 `auto`（写 `app.llm_engine_migrated` 标记，不反复改用户选择）；`server.ensure()` 运行时也做同样回落，并把「已自动改用程序自带的进程内引擎」写进返回说明，不静默降级
- **界面不再让人以为「只能选下载路径」**：`qifei/llm_page.py` 的路径比较全改用 `llmkit.norm_path()`（原先大小写/斜杠差异会导致「扫到了却没选中」）；扫描时把旧 `model_path` 的父目录也加进 `extra_dirs`，方便绿色版整体搬家后仍找得到；没扫到模型时明确提示「点『选择模型文件』直接挑一个 / 把 `.gguf` 拷进 `models\llm` / 模型在别的盘就点『添加扫描目录』」，旧路径失效时单独显示警告，不再只说「一键下载推荐润色模型」
- **新增换电脑专项验证**：`.tmp/qt_relocation_check.py` 构造 `D:\OldPC\Users\zhangsan\...` 的脏配置 + `engine_mode='server'`，模拟 frozen 安装目录，覆盖 `norm_path` / `resolve_model_path` / `Store` 写回 / `from_settings` / `from_role` / `server.ensure()` 回落 / 真实 Qt 大模型页自动选中 Qwen 对话模型 / 干净空配置回归，20/20 `RELOCATION_OK`；`.tmp/qt_fresh_machine_check.py` 干净机器 12/12 `FRESH_MACHINE_OK`。两者都证明：全新空配置本来就正常，坏的是「带着旧配置换机器」
- **macOS 出包链路（不假装能跨平台编译）**：PyInstaller 不支持 Windows → macOS 交叉编译，`.app` / `.dmg` 只能在 Mac 主机或 macOS CI runner 上产出，签名/公证同理。本轮新增 `build/qifei_mac.spec`（不含 `models/`、只收 `.dylib`、排除 `winreg` / `_win32` 等 Windows 专属模块）、`build/build_mac.sh`（平台硬检查 → 生成 `icns` → `QIFEI_BUNDLE_MODEL=0` 构建 → 组装 `.app` → 硬断言包内 0 个 `.gguf` / `.onnx` / `.dll` / `.exe` → 可选签名 → `hdiutil` 打 dmg）、`docs/macOS 构建与安装说明.md`（含「Mac 侧仍需处理的跨平台代码」清单与验证清单）
- **桌面无大模型源码包**：`tools/make_mac_source_pack.py` 白名单收集项目源码，硬排 `.gguf` / `.onnx` / `.dll` / `.exe` / `.pyd` / `.zip` / `__pycache__` / `dist` / `.tmp` / `.venv` / `logs` / `models` 等，输出 `~/Desktop/启飞输入法-2.0.0-macOS源码包.zip`；`--check` 做 CRC、脏文件、关键文件、版本号、包体 <60MB 六项断言。实测 2.26 MB / 375 个文件，6/6 `MAC_SOURCE_PACK_OK`。修掉 `build_mac.sh` 依赖检查段的 heredoc / `|| {` 语法错误，`bash -n` 与 `qifei_mac.spec` 编译均通过
- **修复必须进包才算修完**：源码修复是 11/9 做的，而 `dist/QifeiIME` 里的 exe 还是 11/7 构建、内部 `VERSION` 仍是 1.0.0，且 dist 下没有 2.0.0 Setup。因此本轮重跑 `build/build_all.py --installer`（7m3s 退出码 0）重出正式包，产出 `启飞输入法-2.0.0-安装包.exe`（1207.7 MB）/ payload 1196.5 MB / 314 文件，让「其他电脑」拿到的是带自愈逻辑的 2.0.0，而不是恰好在本机能用的旧包
- **只验源码不算验「其他电脑」：新增两层面向新包的验证**。① `.tmp/verify_frozen_relocation.py` 纯内存读 PyInstaller `PYZ`，从 `qifei.store` / `qifei.llm_page` / `llmkit.registry` / `llmkit.connector` / `llmkit.server` 字节码里查新函数（`norm_path` / `resolve_model_path` / `_resolved_path`）、新文案与 `VERSION='2.0.0'`。**先用归档的 11/7 旧包反证**：旧包 0/15、新包 15/15 `FROZEN_RELOCATION_OK`，证明修复真进了 exe 而不是只在源码里。第一版断言有 2 项误通过（旧包本来就有「选择模型文件」按钮和「进程内引擎」docstring），改用本轮独有文案才收紧到 0/15。② `.tmp/verify_newpack_otherpc.py` 用**本次新构建包的真实目录** `dist/QifeiIME` 当 `app_root`，配一台不存在机器上的失效配置（`E:\OldMachine\...` + `engine_mode=server`），模拟 frozen 跑 `Store` / `from_settings` / `discover` / 真实 Qt 大模型页，16/16 `OTHERPC_OK`：自动选中内置 Qwen、状态 `llama.cpp 就绪`
- **随包推理闭环**：用 `dist/QifeiIME/_internal/.../llama/bin` 的随包动态库 + `dist/QifeiIME/models/llm/` 的随包内置模型跑 `tests/test_llm_real_model.py`：加载 1.3s、直接生成「我是阿里云开发的一款智能机器人…」、SSE「测试成功。」、`LLMProcessor` 运行时对话，`REAL_MODEL_RESULT=PASS`。这是「装到别的电脑第一次打开就能用大模型」的最终验证
- **本轮交付态**：`tools/verify_release.py` **49/49 `RELEASE_VERIFY_OK`**（新增 2 项版本一致性断言，47→49）、frozen 15/15、其他电脑 16/16、真实模型 PASS、`.tmp/qt_relocation_check.py` 20/20、`.tmp/qt_fresh_machine_check.py` 12/12、`tests/test_llm_kit.py` 全过、只读 `compile()` 335 个 `.py` 0 错误、桌面源码包 6/6。桌面 `启飞输入法-2.0.0-macOS源码包.zip`（2.26 MB / 375 文件）与 `dist/启飞输入法-2.0.0-安装包.exe` 两份交付物均已就位

## v2.9

- **产品开箱即用：随产品提供中文润色模型**：`Qwen2.5-1.5B-Instruct Q4_K_M`（1.04GB，带官方 chat template）放到 `models/llm/` 后自动识别并设为默认模型，设置页与听写客户端都不用手选。真实模型端到端验证通过：进程内加载 1.4s，直接生成「我是阿里云开发的一款智能机器人…」、SSE 流式「测试成功。」、`LLMProcessor` 运行时对话「OK」，`REAL_MODEL_RESULT=PASS`
- **一键下载 / 校验**：新增 `llmkit/model_download.py` 与 `tools/download_llm_model.py`，多源（ModelScope / hf-mirror / HuggingFace）、断点续传、`.part` 临时文件、文件大小 + `GGUF` 魔数 + 元数据 + chat template 四重校验；设置中心「大模型」页新增「下载推荐润色模型」按钮（唯一需要联网的可选动作，带进度反馈），命令行等价 `python tools/download_llm_model.py --check / --force`
- **主包保持干净**：1GB 模型不进 `build/qifei.spec` 的 datas，主包只多带约 86MB 的 llama.cpp 动态库；模型可单独分发，离线机器把 `.gguf` 拷到 `models/llm/` 即可，推理全程离线
- **模型类型识别**：`GgufInfo.has_chat_template` 从 GGUF 元数据判断是不是可对话的 Instruct 模型。ASR / embedding / 纯补全模型在下拉里标「（非对话模型）」，自动选中时跳过，选中后明确提示；测试连接若返回 `language .../ <asr_text>`，直接告知「模型类型不对」而不是误报连接正常
- **修掉默认选中漏洞**：磁盘配置里只要存在 `model_path` 键（哪怕为空）就不自动填推荐模型，导致第一次保存设置后永远不自动选中；改为只看当前值是否为空，且只在 `backend=local` 时填，切到远程 API / Ollama 不会被改回去
- **新增自检**：`test_gguf_chat_template` 验证无模板模型被标注；离屏 Qt 验证全新用户自动选中推荐模型、ASR 模型标注、下载按钮存在
- **真实打包核验通过**：在临时目录跑完整 `QIFEI_BUILD_TARGET=all` PyInstaller 构建（2m49s，退出码 0），`QifeiIME.exe` / `QifeiClient.exe` / `QifeiServer.exe` 三个产物齐全，目录 284MB，其中 llama 动态库 87MB / 21 个文件；确认无 `.gguf` 混入，构建日志只有两条无害的 Qt 翻译告警。另用独立的 frozen 探测 exe 验证 `sys._MEIPASS` 下 `dll_dir()` 正确指向 `_internal/core/server/engines/llama/bin`、`native.available()` 为真、`model_download.target()` 落在程序目录 `models/llm/`、`CREATE_NO_WINDOW` 生效
- **修掉下载按钮闪黑框**：设置中心是 `console=False`，`model_download` 起 `curl` 时补上 `CREATE_NO_WINDOW` + `stdin=DEVNULL`，与 `registry.py` / `server.py` 的子进程处理一致
- **包内容更干净**：`build/qifei.spec` 改为逐文件收集 llama 动态库，不再把 `llama-quantize.exe`、`ggml-rpc.dll` 和目录说明 `.txt` 塞进分发包；新增 `QIFEI_LLAMA_MINIMAL=1` 纯 CPU 精简开关，去掉约 58MB 的 `ggml-vulkan.dll`（动态库 86.2MB → 28.7MB），精简后的 DLL 集合已用真实 `.gguf` 验证能加载并生成。默认仍保留 GPU 后端，`gpu_layers > 0` 不受影响
- **离线装模型不用猜目录**：`build/build_all.py::prepare_runtime_files()` 与 `installer/installer_app.py::ensure_dirs()` 都补建 `models/llm`（安装包 GUI / 命令行两条路径都走后者），完全离线的机器把 `.gguf` 拷进来就能被自动识别；已用临时目录验证两条路径的目录契约
- **修掉文档里的过时体积**：旧文档写「主包约 126MB / 软件二进制约 100MB / 安装包约 120MB」，那是 v2.8 把 87MB llama 动态库打进包之前的数字；改为不含模型、只带动态库的准确表述
- **正式分发产物重新出包**：此前 `dist/QifeiIME`(10/29)、`installer/payload.zip`(10/30)、`启飞输入法-1.0.0-安装包.exe`(10/30) 全是旧版，不含直连 llama.cpp 与开箱即用模型。旧产物先整体移到 `.tmp/trash/prebuild-20261106-110846/` 保留回滚，再跑 `build_all.py --installer`（4m31s，退出码 0）。现在 `dist/` 只有三件干净产物：应用目录 285MB（三个 exe + 21 个 llama 动态库）、安装包 150MB、独立模型补包 1.04GB。核对：payload 293 个文件、无 `.gguf` / 无 `models/` 条目；Setup.exe 内嵌的 payload 与源文件 md5 完全一致；`dist/QifeiIME/models/llm/` 已预建且为空
- **完全离线机器也能开箱即用**：新增 `tools/make_llm_model_pack.py`，把推荐润色模型打成 `启飞输入法-LLM润色模型包.zip`（ZIP_STORED，GGUF 已量化无需再压缩，打包解压都快，带 CRC 校验与 `--check`）。解压到安装目录就落到 `models/llm/` 被自动选中，不进主安装包。已实测解压 → `validate()` 通过 → `bundled_chat_model()` 命中
- **发版零副作用自检**：新增 `tools/verify_release.py`，把「正式出包后核对什么」固化成一条命令（`python tools/verify_release.py`）。**不运行 Setup.exe**——静默安装会写桌面 / 开始菜单快捷方式和 HKCU 卸载项；改为读 onefile 内嵌内容：比对 `payload.zip` 与源文件字节一致、用 `marshal.loads()` 确认内嵌 `installer_app` 是含 `models/llm` 目录契约的新版、把 payload 解压到临时目录模拟全新安装 + 覆盖安装并验证用户已下载的模型不被删。另核对 `dist/` 顶层只允许三件产物（白名单，多一个旧 exe / 旧 zip 就报错）、payload 无 `.gguf` / 无 `models/` 条目 / 21 个动态库齐全 / 三个 exe 齐全 / 随包说明是新版。实测 5 组 21 项全绿，`RELEASE_VERIFY_OK`，退出码 0。真机安装实测后自检再补 3 项（payload 里必须有 `onnxruntime` capi 与 `sentencepiece` 扩展、解压后 capi 在位），共 24 项，防止「自检 PASS 但识别子进程退出」的漏打包再次混进正式包
- **真机安装实测抓出并修掉「服务端起不来」的硬缺陷**：装到本机后模型检查通过，识别子进程却报 `ModuleNotFoundError: No module named 'onnxruntime'`，服务端随即退出。根因是 SenseVoice 引擎（`core/server/engines/sensevoice_onnx`）由 factory 动态导入、走自定义 ONNX 推理而不走 `sherpa_onnx`，`onnxruntime` / `sentencepiece` 既没写进 `requirements-server.txt`，也没在 `build/qifei.spec` 的 `SERVER_HIDDEN` / binaries / datas 里收集；而 `QifeiServer.exe --selftest` 的依赖清单恰好也没查这两个，一路漏检成 `PASS`。修复：补齐依赖声明，用 `collect_submodules` + `collect_dynamic_libs` + `collect_data_files` 显式收集，自检增加这两项依赖和「当前模型类型的模型文件是否齐全」检查——依赖齐全但没下载模型不再误报 PASS
- **修掉「完整构建必失败、单目标却正常」的打包坑**：重出正式包时 `build_all.py --installer` 连续两次在第二个 `Analysis` 处被拦：`[safe-delete][SAFE_DELETE_BULK_CONFIRM_REQUIRED]` 指向 `base_library.zip`。查 PyInstaller 源码确认 `build_main.py` 构造 `base_library.zip` 写死了「先删再建」（`Always remove the file before the build`），而 spec 里三个 `Analysis` 共用同一个 `CONF['workpath']`，后两个必然 `os.remove()` 前一个留下的文件——不是偶发，是 `all` 目标的必然冲突；单目标只有一个 `Analysis` 所以能过。改 `build/qifei.spec`：三个目标各分一个独立工作子目录，`COLLECT` 只依赖 `distpath` 不受影响。另外给 `tools/verify_release.py` 补 3 项服务端推理库随包检查（共 24 项），防止漏打包再次混进正式包。自检的「模拟安装」也改为只落盘必要文件、其余按清单核对——全量解压 300+ 文件再 `rmtree` 收尾，在受管控环境里会把自检自己拖到超时
- **正式包按修复后的配置重出，并在本机真覆盖安装实测通过**：有缺陷的旧三件产物整体备份到 `.tmp/trash/prebuild-20261107-1055/`，重跑 `build_all.py --installer`（6m40s，退出码 0）。新产物：`dist/QifeiIME` 322.6MB（三个 exe，服务端 9.2MB，含 `onnxruntime` capi 与 `sentencepiece` 扩展、21 个 llama 动态库、空 `models/llm/`）、`installer/payload.zip` 130.6MB / 309 文件、`启飞输入法-1.0.0-安装包.exe` 164.8MB、独立模型补包 1.04GB。Setup.exe 内嵌 payload 与源文件字节一致（136980063 bytes / md5 `427a72b2d7e2`），`tools/verify_release.py` 5 组 24 项全绿。真机覆盖安装 `Setup.exe --silent --dir ... --no-launch` 退出码 0，37 个用户模型文件（含两个 `.gguf`、SenseVoice 三件套、下载缓存）全部原样保留；安装版 `QifeiServer.exe --selftest` 报 `onnxruntime 1.31.1` / `sentencepiece 0.2.3` / `模型文件齐全（sensevoice）` / `SELFTEST PASS`；真启动服务端 4.18s 引擎加载成功、`ASR 服务就绪`、6016 端口可连、日志无加载失败，15 项安装验证全绿（`INSTALL_VERIFY_OK`）。最后一环用产品自己的 WebSocket 协议把 `models/demo.mp3` 发给安装版服务端，真实识别出「财联社1月7日点，从国家邮政局了解到…邮政快递业业务年均增长10%。」（`E2E_ASR_OK`），彻底闭环「装完能用」
- **补完「装完能用」的用户可见一环，并修掉配置分叉隐患**：复查发现 `config_override.py` 在 `APPDATA` 为空时退化到的 `C:\Users\admin\QifeiIME\config.json` 仍是旧的 `qwen_asr` + `models/llm`，与 `%APPDATA%\QifeiIME\config.json` 分叉；备份到 `.tmp/trash/config-fallback-20261107/` 后同步 `server` 段，两份现完全一致，任何启动上下文都不会把模型路径带偏。随后新增 `.tmp/install_gui_full.py --llm-page`，在单条命令内真启动安装版 GUI：窗口标题是「启飞输入法 · 设置」而非「首次设置」下载页，`supervisor` 自动拉起 `QifeiServer.exe`、探测 6016 就绪后再拉 `QifeiClient.exe`，概览从「模型加载中 / 听写未启动」刷新为「模型已就绪 / 听写已启动 / SenseVoice 小模型」；真实点击「大模型」导航，界面显示绿标 `llama.cpp 就绪` + `qwen2.5-1.5b-instruct-q4_k_m.gguf`、润色开关为开、后端选中「直连 llama.cpp（推荐）」，`GUI_FULL_OK` 10/10，截图留在 `.tmp/install_gui_ready.png` 与 `.tmp/install_gui_llm_page.png`。验证脚本还有个判读坑：`server_latest.log` 是追加写的，全文搜「加载失败」会命中修复前遗留的报错而误报失败，改为按启动前文件偏移只读本次新增字节。`docs/打包与安装核验报告.html` 新增第八节记录，最终复跑 `tools/verify_release.py` 仍是 24/24。至此只剩「人工按住快捷键说话并确认上屏」这一项需真人操作

- **策略变更：安装包内置中文润色大模型，第一次安装就有大模型可用**（用户明确要求，推翻此前「主包保持干净」的设计）。`build_all.py` 新增 `bundle_chat_model()`，把 `models/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf`（1,117,320,736 bytes）复制进绿色版；`installer/build_installer.py` 不再整目录跳过 `models`，改为**白名单只放行这一个条目**（ASR 权重、下载缓存、日志仍不进包），并对它用 `ZIP_STORED`——GGUF 已量化，deflate 压不动还要白等几分钟；打包结束断言模型确实在 payload 里且清单只有它。构建时设 `QIFEI_BUNDLE_MODEL=0` 可退回小包策略。**代价**：安装包从 164.8MB 增至约 1.2GB
- **升级路径不能断：覆盖安装不冲掉用户人工换过的模型**。`installer/installer_app.py` 抽出统一的 `extract_payload()`，GUI 线程与 `--silent` 共用：`models/` 下目标已存在的文件一律跳过，程序本体（exe / DLL / 说明）照常覆盖，否则升级等于没升。用几字节的迷你 payload 实测：全新安装落地内置模型，覆盖安装时用户换过的同名模型、其他 `.gguf` 原样保留，程序本体被更新 → `EXTRACT_POLICY_OK`。安装器界面文案同步改为「已内置中文润色模型，装完即可用；语音模型首次启动时按引导下载」
- **发版自检断言整体反转**：`tools/verify_release.py` 从「无 .gguf 混入 / 无 models/ 条目 / 解压结果不含 .gguf」改成「**恰好内置一份推荐模型**」，并新增大小与源文件一致、`ZIP_STORED`、GGUF 魔数、模型名与 `llmkit/registry.py::BUNDLED_CHAT_MODEL` 一致、frozen Setup 里确实有 `extract_payload`、模拟全新安装落地模型 / 覆盖安装不覆盖用户模型 / 仍更新程序本体等检查。1GB 级 payload 改为源文件分块 md5 比对、只读 4 字节魔数、不落盘模型，避免自检自己把内存和 rmtree 拖爆
- 文档同步：`docs/启飞输入法使用说明.md`（随包）、`docs/本地大模型如何接入.md`、`docs/常见问题.md`、`docs/角色功能如何使用.md`、`readme.md`。明确区分「已内置的润色大模型」与「首次启动仍需下载的语音识别模型」，避免用户以为装完还要下 1GB
- **正式包按「内置模型」策略重出**：`build_all.py --installer` 8m55s 退出码 0（PyInstaller all 353s）。产物：`dist/QifeiIME` 1.4GB（三个 exe + 21 个 llama 动态库 + onnxruntime/sentencepiece + 内置 1,117,320,736 bytes 模型）、`installer/payload.zip` 1,254,301,625 bytes / 1196.2MB / 310 文件、`启飞输入法-1.0.0-安装包.exe` 1207.0MB。payload 里 `models/` 恰好只有 `models/llm/qwen2.5-1.5b-instruct-q4_k_m.gguf` 一个条目且用 `ZIP_STORED`；随包使用说明在源码 / 绿色版 / payload 三处字节一致（13419 bytes / md5 `a3af5fcdb1e9`）
- **首次安装开箱即用实测**：新 Setup 静默装到全新目录，310 文件、退出码 0，`install.log` 无用户模型跳过；`.tmp/bundled_model_check.py` 16/16 `BUNDLED_MODEL_OK`——内置模型大小 / 魔数 / 与源文件 md5 `8e5111fdbc5c` 全一致，`model_download.validate()` 报「qwen2.5-1.5b-instruct · qwen2 · Q4_K_M · ctx 32768 · 对话模板 ✓」，`registry.bundled_chat_model()` 命中，`Store.reload()` 自动填 `model_path` / `model` 且 `backend=local`；再用**随包** llama DLL 跑 `tests/test_llm_real_model.py`，加载 1.3s、直接生成、SSE「测试成功。」、`LLMProcessor`「OK」，`REAL_MODEL_RESULT=PASS`
- **覆盖安装真机实测抓出并修掉第三个硬缺陷**：输入法常驻托盘，`QifeiServer.exe` 的 multiprocessing worker 会锁住 exe；第一次覆盖安装直接 `PermissionError: [Errno 13] Permission denied: ...\QifeiServer.exe`，退出码 1，还留下「`QifeiIME.exe` / `QifeiClient.exe` 已更新、`QifeiServer.exe` 仍是旧版」的半更新状态。修复：`installer_app.py` 新增 `stop_running_apps()`，GUI 与 `--silent` 在解压前用 `taskkill /F /IM` 清掉三个 exe 的全部进程、等句柄释放；`extract_payload()` 对 `PermissionError` 等待重试一次，仍失败给出可照做的中文提示。`tools/verify_release.py` 增加「frozen Setup 含 `stop_running_apps`」断言，防止改了源码忘重打包
- **带运行中输入法的覆盖安装通过**：故意先启动 3 个 `QifeiServer.exe` 锁文件，再用最终 Setup 静默覆盖安装，退出码 0，`install.log` 明确记录「已结束正在运行的输入法：QifeiServer.exe」与「解压 309 个文件…（保留 1 个用户模型，未覆盖）」，安装后无残留进程、6016 端口释放、卸载项指回正式目录，`COVER_WITH_RUNNING_OK` 8/8。随后 `install_verify_final.py --after` 17/17 `INSTALL_VERIFY_OK`：37 个用户模型文件逐个比对一致，`QifeiServer.exe --selftest` PASS（onnxruntime 1.31.1 / sentencepiece 0.2.3 / 模型文件齐全），服务端 3.91s 加载成功、6016 可连
- **发版自检扩到 34 项**：`tools/verify_release.py` 断言整体反转并新增「恰好内置一份推荐模型 / 大小与源文件一致 / ZIP_STORED / GGUF 魔数 / 模型名与 registry 一致 / 全新安装落地 / 覆盖安装不覆盖用户换过的同名模型 / 仍更新程序本体 / frozen Setup 含 `extract_payload` 与 `stop_running_apps`」等。1.2GB payload 改为源文件分块 md5 比对、模型只读 4 字节魔数不落盘，实测 22s 跑完 34/34 `RELEASE_VERIFY_OK`
- **补上「模型已内置后人工重新下载」的入口，清掉策略反转后的残留文案**：复查发现 `qifei/llm_page.py::_download_model()` 还是旧策略——模型已就绪时只弹「模型已就绪」就返回，命令行有 `--force`、界面却没有，用户想人工重下 / 恢复出厂模型 / 给旧版补模型时没有入口；方法 docstring、`llmkit/model_download.py`、`llmkit/registry.py`、`tools/download_llm_model.py` 里还写着「主包不含 1GB 模型」「主包约 126MB」「变大 8 倍」，与「安装包内置模型」直接冲突。改为：已内置时弹「推荐模型已内置」，明确说明会替换现有文件（约 1.04GB、需要联网）、**默认选「否」**，只有人工点「是」才以 `force=True` 覆盖重下；模型缺失时保持原来的首次下载（默认「是」）；重下完成文案区分「已重新下载并恢复为默认模型」。按钮 tooltip 与随包说明同步改为「已内置，通常不用点这里；想换其他模型请用选择模型文件 / 直接拷 `.gguf`」。启动路径不会自动调用下载。`.tmp/qt_force_download_check.py` 离屏 Qt 12/12 验证三种场景（选否不联网 / 选是 `force=True` / 模型缺失 `force=False`），并静态断言源码里只有一处 `model_download.download()` 调用点、无过时文案
- **发版自检加 5 项 frozen 断言，专抓「改了源码忘重出包」**：`tools/verify_release.py` 纯内存解析 `QifeiIME.exe` 内嵌的 `PYZ.pyz`（`PYZ\0` + magic + TOC 偏移 → marshal TOC → zlib 解压的 marshal code，不落盘、不删临时文件），取出 `qifei.llm_page` 字节码，核对「推荐模型已内置」人工确认、重下完成文案、`force` 参数确实存在、旧文案「主包不含」已清除。自检从 34 项扩到 39 项；重出包前先跑基线，39 项中 35 通过、4 项如预期失败（正好是这 4 条），证明确实能抓到旧 exe，而不是一路绿灯。定位 `force` 时还踩到一个字节码坑：Python 3.11+ 把 `download(force=force, on_stage=stage)` 的关键字名编译成 `co_consts` 里的 `('force', 'on_stage')` 元组，`force` 本身是闭包变量（cellvar / freevar），查 `co_names` 查不到；断言改为直接查这个关键字元组
- **按修复后的设置页重出正式包，并真机覆盖安装复验**：旧 Setup.exe / payload.zip 备份到 `.tmp/trash/prebuild-20261107-1548/`，绿色版由 `build_all.py::clean()` 自动归档到 `.tmp/trash/QifeiIME-20261107-155016/`。`build_all.py --installer` 10m50s 退出码 0（PyInstaller all 452s）。新产物：`dist/QifeiIME` 1.4GB、`installer/payload.zip` 1,254,302,464 bytes / 1196.2MB / 310 文件 / md5 `7ca75e23c35b`、`启飞输入法-1.0.0-安装包.exe` 1207.0MB；随包使用说明在源码 / 绿色版 / payload 三处字节一致（13636 bytes / md5 `df87e66c020c`）。发版自检 39/39 `RELEASE_VERIFY_OK`；用归档的旧 GUI exe 反验，四条新断言旧包全 False、新包全 True，确认有区分度。真机覆盖安装 5m4s 退出码 0（解压 309 文件、保留 1 个用户模型），`install_verify_final.py --after` 17/17 `INSTALL_VERIFY_OK`（37 个用户模型文件逐个一致、服务端 4.20s 加载成功、6016 可连），安装版 GUI `GUI_FULL_OK` 10/10；安装目录 `QifeiIME.exe` 与 dist 的 md5 一致且四项新断言全 True，无残留进程，HKCU 卸载项指回正式目录
- **图标重新设计：换掉旧 CapsWriter 的深底红「Cw」，统一成品牌蓝「声起字落」**。旧 `assets/icon.ico` 只有单尺寸 128×128、深底红色 `Cw`（CapsWriter 缩写），而 `qifei/app.py` / `installer/installer_app.py` 的 fallback 画的是蓝底白色声波，两套视觉语言脱节。新设计：品牌蓝斜向渐变（`#7BA2FF → #3D6BFF → #2A45B8`）圆角方形 + 白色圆头声波（说出来）+ 底部输入基线与细长光标（落成文字）+ 左上淡高光。新增 `tools/make_icon.py`，用 Pillow + numpy 逐尺寸绘制、4× 超采样 + LANCZOS，**8 个尺寸分级绘制**（16 / 20 / 24 用 3 根粗条，32 是过渡档，≥48 才画光标和高光），不是从 256 一张缩下来；输出 `assets/icon.ico`（手写 32bpp DIB 容器，兼容 PyInstaller `IconFile` / Qt ICO 插件 / Windows 外壳）、`icon.png`、`icon-preview.png`。两处 fallback 同步成同款，`--check` 顺带守住绿色版随包 ico 一致。验证：`tools/make_icon.py --check` 22/22 `ICON_CHECK_OK`（8 个 DIB 头、Pillow 全尺寸可读、圆角透明、PyInstaller 可解析、dist 两处同步），`.tmp/icon_verify.py` 8/8 `ICON_VERIFY_OK`（Qt 读到 8 尺寸、`ExtractIconEx` 提取成功、GUI 与安装器 fallback 对 ICO 像素 MAE 3.31）。`assets/BUILD_GUIDE.md` 补了生成命令与「改完图标必须重出包」的说明。**生效范围**：源码与绿色版运行时窗口 / 托盘图标已换；三个 exe 的 PE 文件图标和 `Setup.exe` 内嵌图标仍是旧的，必须 `build_all.py --installer` 重出包才会更新（见下条）。顺带修掉一个一直存在的缺陷：`installer/build_installer.py::build_setup()` 从没给 PyInstaller 传 `--icon`，Setup.exe 用的是 PyInstaller 默认图标、与产品无关；现补上 `--icon assets/icon.ico`
- **按新图标重出正式包，发版自检扩到 47 项**：`tools/verify_release.py` 新增 8 项图标断言——payload 内 `assets/icon.ico` 与 `_internal/assets/icon.ico` 与源码字节一致、随包 ico 是 8 尺寸 32bpp DIB、`build_installer.py` 确实传了 `--icon`，以及**四个 exe 的 PE `RT_ICON` 资源**逐字节等于新 ico 的 8 个 DIB。原理：PyInstaller 把 ICO 各尺寸条目原样写进 exe 的 `RT_ICON`，用 `LoadLibraryEx(LOAD_LIBRARY_AS_DATAFILE)` + `EnumResourceNames(RT_ICON)` 就能零副作用读出来，比只查源码可靠。重出前跑基线：47 项中 40 通过、7 项失败，失败项正好全是这 7 条（三个产品 exe 各只有 1 个旧 RT_ICON、Setup.exe 是 7 个 PyInstaller 默认图标、payload 里是旧 ico），证明断言有区分度。旧 Setup / payload 备份到 `.tmp/trash/prebuild-20261107-205148/`，`build_all.py --installer` **11m53s 退出码 0**（PyInstaller all 511s，日志明确出现 `--icon ...assets\icon.ico` 与 `Copying icon to EXE`）。新产物：`installer/payload.zip` 1,254,632,098 bytes / 1196.5 MB / 314 文件 / md5 `5d719de09de`、`启飞输入法-1.0.0-安装包.exe` 1207.7 MB；payload 比上轮多 4 个文件，是 `icon.png` / `icon-preview.png` 各进了 `assets/` 与 `_internal/assets/`。重出后 **47/47 `RELEASE_VERIFY_OK`**，四个 exe 的 RT_ICON 全部 8 个且与新 ico 逐字节一致；`make_icon.py --check` 22/22、`icon_verify.py` 8/8 同绿
- **新图标包真机覆盖安装复验通过**：新 Setup `--silent --dir ... --no-launch` **6m28s 退出码 0**，解压 313 文件、保留 1 个用户模型（37 个模型文件逐个一致）、服务端 4.11s 加载成功、6016 可连、`SELFTEST PASS`。`.tmp/install_verify_final.py --after` **21/21 `INSTALL_VERIFY_OK`**（新增 4 项图标断言：安装目录 `assets/icon.ico` 与源码字节一致、三个 exe 的 PE 图标 = 新 ico 的 8 个 DIB）；安装版 GUI **10/10 `GUI_FULL_OK`**；HKCU 卸载项 / `DisplayIcon` / 桌面快捷方式 `IconLocation` 都指回正式目录，且快捷方式指向的 exe 其 `RT_ICON` = 8。`docs/打包与安装核验报告.html` 新增第十节记录本轮、原「未做与待决定」顺延为第十一节，`.tmp/pkgcheck/packaging-report.html` 同步。**最终：47/47 RELEASE_VERIFY_OK · 21/21 INSTALL_VERIFY_OK · 10/10 GUI_FULL_OK**，图标已随正式安装包交付，第一次安装看到的就是新设计

## v2.8

- **不装 Ollama，直接用 llama**：默认后端改成「直连 llama.cpp」。项目自带的 `llama.dll` / `ggml.dll` / `ggml-base.dll` 通过 ctypes 在**进程内**直接加载 `.gguf`，不需要安装 Ollama、不需要下载 `llama-server.exe`、不联网、不往 Ollama 里复制模型
- **内嵌 OpenAI 兼容服务**：`llmkit/native.py` 负责 chat template / tokenize / decode / sampler 流式生成，`llmkit/server.py` 用标准库 `http.server` 挂出 `127.0.0.1:11500` 的 `/health`、`/v1/models`、`/v1/chat/completions`（SSE）。上层 `connector` / `http_client` / `LLMProcessor` 一行不改，流式解析、分级超时、冷启动重试全部复用
- **彻底去掉 Ollama 依赖**：直连模式下不查 11434、不调 `ollama create`、不 `ollama serve`、不 `pull`；`discover(include_ollama=False)` 扫描瞬间返回，也不再误报「Ollama 未运行」。Ollama 降为「可选」后端，只在用户显式选中时才启用
- **默认配置统一**：`config.json` 没有 `llm` 段时，界面 / 角色加载器 / 处理器共用 `connector.default_settings()`（直连 llama.cpp），不会偷偷退回 Ollama；老配置里 `backend=ollama` 首次打开设置页时一次性迁移
- **界面改造**：「大模型」页把「直连 llama.cpp（推荐）」置顶为默认，新增推理引擎、上下文长度、GPU 卸载层数设置；导入 `.gguf` 只记路径，不再注册；测试连接后主动释放 GUI 进程里的模型，避免与客户端各加载一份
- **打包**：`build/qifei.spec` 把 `core/server/engines/llama/bin` 的动态库打进 GUI / Client 包，`llmkit.native` 加入 hiddenimports；`dll_dir()` 支持 `sys._MEIPASS`、`QIFEI_LLAMA_DLL_DIR` 等多种定位
- **动态库缺失时不静默降级**：明确报出缺失原因，并允许回落到放在 `runtime/llama/` 的外部 `llama-server.exe`
- **直连链路加固**：自检新增不依赖真实模型的 ctypes ABI 校验（结构体布局、batch 指针、chat template 格式化）和打包布局 DLL 定位；`os.add_dll_directory` 的 cookie 改为常驻引用，去掉多余的 `devices` 覆盖，与同版本 DLL 的 ASR 绑定保持一致
- **真实 GGUF 端到端验证通过**：用 1.4GB 的真实模型跑通「进程内加载 → 生成 → 内嵌 HTTP → SSE → LLMProcessor 运行时对话」，并修掉两个只有真实模型才暴露的硬伤：
  - `ggml_backend_load_all()` 只枚举**当前工作目录**下的 `ggml-*.dll`，CWD 是程序根目录时会静默注册 0 个后端，模型加载直接报 `no backends are loaded`。改用 `ggml_backend_load_all_from_path(bin)` 显式传目录，并在注册数为 0 时明确报错
  - `llama_batch_init()` 只按容量分配内存、`n_tokens` 仍是 0，`LlamaNative._decode()` 漏填导致 `llama_decode` 报 `n_tokens == 0`；已补上
- **新增可选真实模型自检**：`python tests/test_llm_real_model.py` 自动找 `models/llm/*.gguf`，没有模型时跳过；常规自检增加「ggml 后端注册数 ≥ 1」断言，防止再次静默失败

## v2.7

- **大模型接入重做**：设置中心新增「大模型」页，后端、地址、模型、超时都在界面上配，不用再改 `LLM/*.py`
- **本地模型可直接导入**：自己下载的 `.gguf` 文件选进来即可注册使用（自动生成 Modelfile 并 `ollama create`），不再必须联网 `ollama pull`；Ollama 没启动时自动拉起
- **新增内置 llama.cpp 后端**：不想装 Ollama 时，把 `llama-server.exe` 放进 `runtime/llama/`，直接加载 `.gguf`
- **超时修复**：原来所有请求写死 2 秒超时，本地模型冷加载必然失败。现在连接超时（5s）与生成超时（600s）分离，并支持冷启动自动重试一次
- **模型常驻与预热**：`keep_alive=-1` 常驻内存，客户端启动时后台预热，第一句话不用等
- **零 SDK 依赖**：LLM 请求改由 `llmkit`（纯标准库 `http.client`）实现，不再需要 `openai` / `ollama` 两个可选包；本地地址强制绕过系统代理
- **报错说人话**：模型没装时直接列出本机已有模型，并在界面上给出可照做的提示，而不是抛一串 SDK 异常
- **新增自检**：`python tests/test_llm_kit.py` 覆盖「本地 gguf 导入 → 注册 → 识别 → 对话」全链路，以及流式解析、模型缺失、超时分层、运行时对话，全程不需要真实 Ollama / 不联网
- **连接超时设置真正生效**：界面里调的两个超时现在都会透传到运行时 HTTP 客户端（此前连接超时被写死 5 秒，改了没用）；角色文件里的 `timeout` 不再被误当成连接超时
- **修复预热与探测**：构造客户端时把地址错当成了 provider，导致启动预热必然失败；同时给预热足额的生成超时
- **设置页稳定性**：「大模型」页扫描改用普通守护线程 + 跨线程信号，关窗口时不再因活着的 `QThread` 让进程静默崩溃；「导入模型文件」按钮不再被已注册模型锁死，可连续导入多个文件

## v2.6

- **GPU 预加速**：服务端新配置（默认关闭），适用于 Nvidia 独显，录音开始时主动锁定显存高频率，大幅降低模型转录延迟至 0.1s
- **标点处理优化**：仅对 8 词以内的结果去除末尾标点，长句不再误伤；可通过配置 config_client.py 指定某些程序强制去除标点
- **带延迟的附加回车**：用于同花顺等场景，输入股票名后等待 0.5s 自动回车切换，可在 config_client.py 配置
- **角色功能简化**：角色功能简化为单一开关；默认角色改用 DeepSeek API，方便新用户填入密钥后直接体验
- **ITN 增强**：连续多数字转换更鲁棒
- **活动窗口日志**：每次识别后通过 debug 日志输出当前活动窗口名，方便用户配置 paste_apps
- **标点模型容错**：取消强制检查，加载失败时静默回退，避免启动受阻
- **热词使用体验**：右键菜单点击「热词」直接打开 hot.txt 文件，不再弹窗
- **重启菜单**：托盘右键新增「重启」选项，便于修改配置后快速重启生效
- **集成显卡兼容**：加入调试性配置项，集显出问题时可通过禁用 feature 解决
- **Windows Terminal 适配**：支持隐藏 Windows Terminal 控制台窗口

## v2.5

- **引入 [Qwen3-ASR-1.7B](https://github.com/HaujetZhao/Qwen3-ASR-GGUF)**：140ms 极速推理，准确率夯爆。Decoder Vulkan 加速默认打开，需占 1.6GB 显存。显卡空闲时，会降低显存频率，冷启动转录延迟升至 300ms。若用管理员权限运行 `nvidia-smi -lmc 9000` 锁定显存不降频，实测 RTX5050 转录延迟可降至 100ms
- **集成 Force Aligner**：辅助 Qwen3-ASR 支持时间戳，按需加载、超时释放，仅文件转录时占用资源
- **热词别名**：热词支持用 `|` 分隔定义多个别名，
- **角色别名**：`name` 用 `|` 分隔定义多个别名，解决 ASR 对角色名识别不准的问题
- **移除纠错历史**：热词别名已能覆盖纠错历史的需求，移除 `hot-rectify.txt` 及相关逻辑
- **文件转录支持热词**：文件转录现在也可以使用热词功能
- **语言配置**：`config_client.py` 新增 `language` 选项，支持指定识别目标语言
- **架构重构**：进行了大量重构，方便后续维护
- **日志优化**：只保留一份日志文件

## v2.4

- **改进 [Fun-ASR-Nano-GGUF](https://github.com/HaujetZhao/Fun-ASR-GGUF) 模型，使 Encoder 支持通过 DML 用显卡（独显、集显均可）加速推理，Encoder 和 CTC 默认改为 FP16 精度，以便更好利用显卡算力**，短音频延迟最低可降至 200ms 以内。
  - 若用管理员权限运行 `nvidia-smi -lmc 9000` 锁定显存不降频，实测 RTX5050 转录延迟可降至 100ms
- 服务端 Fun-ASR-Nano 使用单独的热词文件 hot-server.txt ，只具备建议替换性，而客户端的热词具有强制替换性，二者不再混用
- 可以在句子的开头或结尾说「逗号、句号、回车」，自动转换为对应标点符号，支持说连续多个回车。
- Fun-ASR-Nano 加入采样温度，避免极端情况下的因贪婪采样导致的无限复读
- 服务端字母拼写合并处理

## v2.3

- **引入 [Fun-ASR-Nano-GGUF](https://github.com/HaujetZhao/Fun-ASR-GGUF) 模型支持，推理更轻快**
- 重构了大文件转录逻辑，采用异步流式处理
- 优化中英混排空格
- 增强了服务端对异常断连的清理逻辑

## v2.2

- **改进热词检索**：将每个热词的前两个音素作为索引进行匹配，而非只用首音素索引。
- **UDP广播和控制**：支持将结果 UDP 广播，也可以通过 UDP 控制客户端，便于做扩展。
- **Toast窗口编辑**：支持对角色输出的 Toast 窗口内容进行编辑。
- **多快捷键**：支持设置多个听写键，以及鼠标快捷键，通过 pynput 实现。
- **繁体转换**：支持输出繁体中文，通过 zhconv 实现。

## v2.1

- **更强的模型**：内置多种模型可选，速度与准确率大幅提升。
- **更准的 ITN**：重新编写了数字 ITN 逻辑，日期、分数、大写转换更智能。
- **RAG 检索增强**：热词识别不再死板，支持音素级的 fuzzy 匹配，就算发音稍有偏差也能认出。
- **LLM 角色系统**：集成大模型，支持润色、翻译、写作等多种自定义角色。
- **纠错检索**：可记录纠错历史，辅助LLM润色。
- **托盘化运行**：新增托盘图标，可以完全隐藏前台窗口。
- **完善的日志**：全链路日志记录，排查问题不再抓瞎。


## v1.0 

- 通过分段识别和去重，实现了支持无限时长语音的转写
- 客户端支持转写音视频文件为 srt 字幕，只需将音视频文件拖动到客户端 exe 上打开即可

## v0.6 


- 新增日记功能，将每日的录音结果保存在一个 Markdown 文件中
- 新增关键词日记功能，每日的以关键词开头的录音结果会保存在特别的 Markdown 文件中
- 新建录音文件夹的时候，会复制一个 Python 辅助脚本，用于清理没有被 Markdown 文件引用的附件，这样一来，通过编辑 Markdown 日记就可以清理不需要保存的录音
- 新增定义录音文件保存目录
- 默认保存48000采样率高品质录音录音，如果用户安装了 FFmpeg 则保存为 mp3 格式，否则保存为 wav 格式
- 输入方式改为模拟 Ctrl + V 粘贴，粘贴完后恢复剪贴板内容
- 使用 rich 库输出彩色文字，尽量在各种终端达到一致的显示效果

## v0.5 

- 修改热词文件后，不用重启客户端，就可以动态更新热词了。

## v0.4

- 为客户端加入了三种热词功能：中文、英文、自定义
- 改进了对中文数字的搜索，当数字的左侧或者右侧有英文时，就一定会被选中。
- 改进了中英空格排版，能够正常输出 iPhone 4s 这样的词语。


## v0.3 

- 客户端当音频设备名不可 utf-8 解码时，不再闪退
- 客户端添加配置可以编辑修改，要消除识别结果末尾哪些标点
- 客户端添加配置可以修改快捷键
- 客户端添加配置可以修改快捷键触发的时间阈值
- 客户端连接中断会自动进行重试
- 客户端提示当前所用的快捷键
- 服务端对识别结果，中英文混排进行空格校正
- 当地址无法被绑定时提示问题，而不是直接闪退


## 起源

**2020年10月**，因手机上的语音输入法很好用，但电脑上却没有足够好用的语音输入法，我手写了一个工具 **[CapsWriter](https://github.com/HaujetZhao/CapsWriter)**，通过长按大写锁定键录音，松开后，调用阿里云的一句话识别 API，识别后上屏。12月的时候，还加入图形化界面。但当时大学宿舍的校园网经常抽风，没有稳定的网络环境，转录延迟飘忽不定。有时候说完话了，等了好几秒，才发现 WiFi 没有连接，毁心态。但是当时并没有识别率能满足要求的离线中文 ASR 模型。

后来 Whisper 发布，中文识别率确实不错，但是模型延迟很高，无法满足本地语音输入。

到了**2023年5月**，在B站看到了 [极客湾](https://space.bilibili.com/25876945) 的视频 [我们做了个能对话的AI派蒙，免费给大家玩！](https://www.bilibili.com/video/BV1bm4y117ba/) ，他们实现的效果非常好，其中提到 ASR 模型用了阿里 [FunASR](https://github.com/modelscope/FunASR) 团队发布的开源 [Paraformer](https://www.modelscope.cn/models/iic/speech_paraformer-large_asr_nat-zh-cn-16k-common-vocab8358-tensorflow1) 模型，于是就拿来测试了下，果然识别准确率很棒、延迟超低，于是弃坑 **[CapsWriter](https://github.com/HaujetZhao/CapsWriter)** ，立马新开了 **[CapsWriter-Offline](https://github.com/HaujetZhao/CapsWriter-Offline)** ，用 [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) 调用 Paraformer 进行转录，效果非常好。

**2025年12月09日**，我看到阿里 [FunASR](https://github.com/modelscope/FunASR) 团队开源了 [Fun-ASR-Nano](https://www.modelscope.cn/models/FunAudioLLM/Fun-ASR-Nano-2512) 模型，拿来一测，果然准确率又上一个台阶，在 sherpa_onnx 支持后，我赶紧更新了 CapsWriter-Offline v2.1，一次性加入了 SenseVoice-Small 和 Fun-ASR-Nano 模型的支持。但问题是当时 sherpa_onnx 是用 ONNX 实现的 Fun-ASR-Nano，速度有些慢。

我研究了 Fun-ASR-Nano 的架构，发现它的 Decoder 是 LLM 架构，而推理 LLM 最快的是 [LLama.cpp](https://github.com/ggml-org/llama.cpp) 。虽然我编程能力差，但刚好此时，Google 推出了 Antigravity AI 编程 IDE，里面有 Gemini 和 Claude 模型，在他们的帮助下，我成功写出了 [Fun-ASR-GGUF](https://github.com/HaujetZhao/Fun-ASR-GGUF)，用 onnx 和 gguf 格式混合运行 Fun-ASR-Nano，用 [LLama.cpp](https://github.com/ggml-org/llama.cpp) 加速它的 LLM Decoder 部分，在我的笔记本上实现了最快的推理速度：

| 设备 | RTF |
|------|-----|
| GPU RTX5050  | 0.025 |
| CPU U9-285H  | 0.1 |

**2026年01月21日**，[Qwen3-TTS](https://www.modelscope.cn/collections/Qwen/Qwen3-TTS) 开源发布了，生成效果极其优异，让我特别想要，但官方版本推理速度很慢，于是基于 [Fun-ASR-GGUF](https://github.com/HaujetZhao/Fun-ASR-GGUF) 的加速推理经验，在 Antigravity 的帮助下，我又实现了 [Qwen3-TTS-GGUF](https://github.com/HaujetZhao/Qwen3-TTS-GGUF) ，也是用 [LLama.cpp](https://github.com/ggml-org/llama.cpp) 加速它的 LLM Decoder 部分。

**2026年01月28日**，间隔没几天，[Qwen3-ASR](https://www.modelscope.cn/collections/Qwen/Qwen3-ASR) 开源发布了，本来没抱太大预期的，但下载 1.7B 一测后，又给我震惊了，准确率竟比 Fun-ASR-Nano 还上一个台阶，能吊打闭源模型！于是在 [Qwen3-TTS-GGUF](https://github.com/HaujetZhao/Qwen3-TTS-GGUF) 经验的帮助下，我马不停蹄地实现了 [Qwen3-ASR-GGUF](https://github.com/HaujetZhao/Qwen3-ASR-GGUF) 加速推理，实现了最快的推理速度：

| 设备 | RTF |
|------|-----|
| GPU RTX5050  | 0.05 |
| CPU U9-285H  | 0.2 |

这就是 CapsWriter-Offline 大致的来路。总体就是这几个组件：

- 模型推理
- 热词替换
- 按键监听与录音

当前只有 Windows 的打包，是因为我只有 Windows 电脑，没有 Linux 与 MacOS 的需求。在 Vibe Coding 时代，我相信需求的小伙伴在 Claude Code 的帮助下，也能在其系统上做出类似功能的实现。
