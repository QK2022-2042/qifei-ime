# coding: utf-8
"""
大模型设置页

默认路径是「直连 llama.cpp」：程序自带 llama.cpp 动态库，
自己下载的 .gguf 选进来就能加载，不装 Ollama、不下载 llama-server.exe、不联网。

另外解决两件老问题：
1. 后端、地址、模型都在这里选，不用再去改 LLM/*.py；
2. 超时可以调，本地模型冷加载不再因为 2 秒超时直接失败。

Ollama 保留为可选项，只有本来就装了 Ollama 的用户才需要选它。
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import List, Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (QComboBox, QDoubleSpinBox, QFileDialog,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox,
                               QPushButton, QSpinBox, QVBoxLayout, QWidget)

import llmkit
from llmkit import connector as llm_connector

from .theme import C
from .widgets import Card, Divider, Page, SettingRow, StatusChip, ToggleSwitch

BACKENDS = [
    ('local', '直连 llama.cpp（推荐）',
     '程序自带 llama.cpp 动态库。选中 .gguf 就能用，不用装 Ollama、不用放 exe、不联网。'),
    ('openai', '远程 API',
     'DeepSeek / Moonshot / 智谱 等在线服务，需要联网和 API Key。'),
    ('ollama', '本机 Ollama（可选）',
     '只有本来就装了 Ollama 才选这个；会把 .gguf 注册进 Ollama，仍然不联网。'),
]

ENGINE_MODES = [
    ('auto', '自动（优先进程内）', '推荐'),
    ('native', '进程内直连', '用程序自带动态库，零安装'),
    # 安装包里没有这个 exe。保留给确实自己放了 exe 的高级用户，
    # 但 _on_engine() 会在选中时校验，找不到就不许把坏值写进配置。
    ('server', '外部 llama-server.exe（高级）', '安装包不含此 exe，需自行放置'),
]

KEEP_ALIVE_ITEMS = [
    ('常驻内存（推荐）', -1),
    ('30 分钟', '30m'),
    ('5 分钟', '5m'),
]


# ────────────────────────────────────────────────────────────
# 后台任务
#
# 不用 QThread：扫描目录可能很慢，用户关窗口时 QThread 还没结束，
# 析构会直接把进程带崩（Qt 的 QThread 不允许带活线程销毁）。
# 普通守护线程 + 信号回主线程，退出时不会拖住进程。
# ────────────────────────────────────────────────────────────
class Worker(QObject):
    done = Signal(object)
    stage = Signal(str)


def run_async(owner, job, on_done, on_stage=None) -> Worker:
    """在后台线程跑 job，结果通过信号送回主线程

    显式 QueuedConnection：emit 发生在工作线程，槽函数必须在主线程跑，
    否则会去碰 QWidget（Qt 只允许 GUI 线程操作控件）。
    """
    worker = Worker(owner)
    worker.done.connect(on_done, Qt.QueuedConnection)
    if on_stage is not None:
        worker.stage.connect(on_stage, Qt.QueuedConnection)

    def runner():
        try:
            result = job(worker.stage.emit if on_stage is not None else None)
        except Exception as exc:
            result = {'ok': False, 'message': f'{type(exc).__name__}: {exc}'}
        try:
            worker.done.emit(result)
        except RuntimeError:
            pass  # 窗口已关闭，Worker 被 Qt 销毁，结果丢弃即可

    threading.Thread(target=runner, daemon=True).start()
    return worker


# ────────────────────────────────────────────────────────────
# 页面
# ────────────────────────────────────────────────────────────
class LLMPage(Page):

    def __init__(self, window):
        super().__init__(
            '大模型',
            '自己下载的 .gguf 选进来就能用：程序自带 llama.cpp，不装 Ollama、不联网。')
        self.window = window
        self.store = window.store
        self._scan_worker: Optional[Worker] = None
        self._test_worker: Optional[Worker] = None
        self._import_worker: Optional[Worker] = None
        self._download_worker: Optional[Worker] = None
        self._models: List[llmkit.LocalModel] = []
        self._restoring = False

        self._build()

    # ---------- 构建 ----------

    def _build(self) -> None:
        page = self

        # 状态
        status = Card()
        status.add_title('当前状态', '改完设置点「测试连接」确认能通，再开始用。')
        row = QHBoxLayout()
        row.setSpacing(10)
        self.chip_backend = StatusChip('未检测', 'muted')
        self.chip_model = StatusChip('未选择模型', 'muted')
        row.addWidget(self.chip_backend)
        row.addWidget(self.chip_model)
        row.addStretch(1)
        self.btn_test = QPushButton('测试连接')
        self.btn_test.setObjectName('Primary')
        self.btn_test.clicked.connect(self._test)
        row.addWidget(self.btn_test)
        status.body().addLayout(row)

        self.lbl_test = QLabel('')
        self.lbl_test.setObjectName('RowDesc')
        self.lbl_test.setWordWrap(True)
        status.body().addWidget(self.lbl_test)

        status.add(Divider())
        self.sw_enabled = ToggleSwitch(
            checked=bool(self.store.get('client.llm_enabled', True)))
        self.sw_enabled.toggled.connect(self._on_enabled)
        status.add(SettingRow('启用大模型润色',
                              '关闭后所有角色都不再调用大模型，识别结果直接上屏。',
                              self.sw_enabled))
        page.add(status)

        # 后端
        backend_card = Card()
        backend_card.add_title(
            '后端', '默认直连 llama.cpp；Ollama 只是给已经装了的用户留的可选项。')

        self.combo_backend = QComboBox()
        for key, label, _desc in BACKENDS:
            self.combo_backend.addItem(label, key)
        self._select_data(self.combo_backend, self.store.get('llm.backend', 'local'))
        self.combo_backend.currentIndexChanged.connect(self._on_backend)
        backend_card.add(SettingRow('使用哪个后端', '', self.combo_backend))

        self.lbl_backend_desc = QLabel('')
        self.lbl_backend_desc.setObjectName('RowDesc')
        self.lbl_backend_desc.setWordWrap(True)
        backend_card.body().addWidget(self.lbl_backend_desc)
        backend_card.add(Divider())

        url_row = QHBoxLayout()
        url_row.setSpacing(8)
        self.edit_url = QLineEdit(self.store.get('llm.api_url', '') or '')
        self.edit_url.editingFinished.connect(self._on_url)
        url_row.addWidget(self.edit_url, 1)
        backend_card.body().addWidget(QLabel('服务地址'))
        backend_card.body().addLayout(url_row)

        self.edit_key = QLineEdit(self.store.get('llm.api_key', '') or '')
        self.edit_key.setEchoMode(QLineEdit.Password)
        self.edit_key.setPlaceholderText('远程 API 才需要填')
        self.edit_key.editingFinished.connect(self._on_key)
        backend_card.body().addWidget(QLabel('API Key'))
        backend_card.body().addWidget(self.edit_key)
        page.add(backend_card)

        # 模型
        model_card = Card()
        model_card.add_title(
            '模型', '直连 llama.cpp 用本机 .gguf；远程 API / Ollama 用服务方模型名。')

        # v2.14：远程 API / Ollama 后端必须有一个「服务方模型名」输入框。
        # 老界面只有下面那个列着本机 .gguf 的下拉框，而 llm.model 这个键
        # 在 local 下是 GGUF 文件名 stem、在远程下是 deepseek-chat /
        # qwen2.5:7b 这样的服务方模型名。用户切到远程后根本没有入口填它，
        # 误点一下下拉框里的本地 gguf 就把远程模型名冲成 GGUF 文件名，还写
        # 进一个远程后端用不上的 model_path，并 _apply() 重启客户端。
        # 这里把两条路径在界面上彻底分开：远程后端只显示输入框。
        remote_row = QHBoxLayout()
        remote_row.setSpacing(8)
        self.lbl_remote_model = QLabel('服务方模型名')
        self.edit_remote_model = QLineEdit()
        self.edit_remote_model.setMinimumWidth(320)
        self.edit_remote_model.setPlaceholderText(
            '例如 deepseek-chat / qwen2.5:7b')
        self.edit_remote_model.setToolTip(
            '填服务商给的模型名，不是本地 .gguf 文件名。\n'
            'DeepSeek 常填 deepseek-chat；Ollama 常填 qwen2.5:7b。\n'
            '这一项只写入 llm.model，不会动 llm.model_path。')
        self.edit_remote_model.editingFinished.connect(self._on_remote_model)
        remote_row.addWidget(self.lbl_remote_model)
        remote_row.addWidget(self.edit_remote_model, 1)
        model_card.body().addLayout(remote_row)

        # 本地 .gguf 相关控件整体装进容器：远程 API 后端下整块隐藏，
        # 避免用户面对一个全是本地文件的下拉框不知所措（历史反馈
        # 「只能选择下载的路径」）。Ollama 后端仍需要「导入到 Ollama」。
        self.local_file_row = QWidget()
        model_row = QHBoxLayout(self.local_file_row)
        model_row.setContentsMargins(0, 0, 0, 0)
        model_row.setSpacing(8)
        self.combo_model = QComboBox()
        self.combo_model.setMinimumWidth(320)
        self.combo_model.currentIndexChanged.connect(self._on_model)
        model_row.addWidget(self.combo_model, 1)

        self.btn_refresh = QPushButton('重新扫描')
        self.btn_refresh.setObjectName('Ghost')
        self.btn_refresh.clicked.connect(self.refresh)
        model_row.addWidget(self.btn_refresh)

        self.btn_import = QPushButton('选择模型文件（已下载好的）')
        self.btn_import.setObjectName('Primary')
        self.btn_import.setToolTip(
            '你已经把 .gguf 下载到别的盘 / 别的目录了？点这里直接选那个文件，\n'
            '不用重新下载，也不用拷进安装目录。\n'
            '选完会自动记住这个目录，下次启动下拉框里就能直接看到。')
        self.btn_import.clicked.connect(self._import)
        model_row.addWidget(self.btn_import)
        model_card.body().addWidget(self.local_file_row)

        self.lbl_model = QLabel('')
        self.lbl_model.setObjectName('RowDesc')
        self.lbl_model.setWordWrap(True)
        model_card.body().addWidget(self.lbl_model)

        self.local_dir_row = QWidget()
        dirs_row = QHBoxLayout(self.local_dir_row)
        dirs_row.setContentsMargins(0, 0, 0, 0)
        dirs_row.setSpacing(8)
        btn_dir = QPushButton('添加扫描目录')
        btn_dir.setObjectName('Ghost')
        btn_dir.clicked.connect(self._add_dir)
        dirs_row.addWidget(btn_dir)
        btn_open = QPushButton('打开模型目录')
        btn_open.setObjectName('Ghost')
        btn_open.clicked.connect(self._open_model_dir)
        dirs_row.addWidget(btn_open)
        self.btn_download = QPushButton('下载推荐润色模型')
        self.btn_download.setObjectName('Ghost')
        self.btn_download.setToolTip(
            '安装包已内置推荐润色模型，第一次装完就能用，通常不用点这里。\n'
            '只有模型损坏、想恢复出厂模型或给旧版本补模型时才需要人工下载：'
            '会替换 models/llm/ 里的同名文件（约 1.04GB，需要联网）。\n'
            '想换其他模型请用「选择模型文件」，或直接把 .gguf 拷进模型目录。')
        self.btn_download.clicked.connect(self._download_model)
        dirs_row.addWidget(self.btn_download)
        dirs_row.addStretch(1)
        model_card.body().addWidget(self.local_dir_row)
        page.add(model_card)

        # 推理参数（仅直连 llama.cpp 生效）
        eng = Card()
        eng.add_title('llama.cpp 推理参数', '只有「直连 llama.cpp」后端会用这些设置。')

        self.combo_engine = QComboBox()
        for key, label, _desc in ENGINE_MODES:
            self.combo_engine.addItem(label, key)
        self._select_data(self.combo_engine,
                          self.store.get('llm.engine_mode', 'auto'))
        self.combo_engine.currentIndexChanged.connect(self._on_engine)
        eng.add(SettingRow('推理引擎', '自动＝优先用程序自带动态库，失败才找外部 exe。',
                           self.combo_engine))
        eng.add(Divider())

        self.spin_ctx = QSpinBox()
        self.spin_ctx.setRange(512, 131072)
        self.spin_ctx.setSingleStep(512)
        self.spin_ctx.setValue(int(self.store.get('llm.context', 4096) or 4096))
        self.spin_ctx.valueChanged.connect(
            lambda v: self._save('llm.context', int(v)))
        eng.add(SettingRow('上下文长度', '越大越吃内存；超过模型训练长度会自动收敛。',
                           self.spin_ctx))
        eng.add(Divider())

        self.spin_gpu = QSpinBox()
        self.spin_gpu.setRange(0, 999)
        self.spin_gpu.setValue(int(self.store.get('llm.gpu_layers', 0) or 0))
        self.spin_gpu.valueChanged.connect(
            lambda v: self._save('llm.gpu_layers', int(v)))
        eng.add(SettingRow('GPU 卸载层数',
                           '0＝纯 CPU（最稳）；有独立显卡可填 99 全部卸载，速度更快。',
                           self.spin_gpu))
        page.add(eng)

        # 高级
        adv = Card()
        adv.add_title('连接与超时', '本地模型第一次加载要几十秒，超时别设太小。')

        self.spin_connect = QDoubleSpinBox()
        self.spin_connect.setRange(1, 120)
        self.spin_connect.setDecimals(0)
        self.spin_connect.setSuffix(' 秒')
        self.spin_connect.setValue(float(self.store.get('llm.connect_timeout', 5) or 5))
        self.spin_connect.valueChanged.connect(
            lambda v: self._save('llm.connect_timeout', float(v)))
        adv.add(SettingRow('连接超时', '连不上就尽快报错，一般 5 秒足够。', self.spin_connect))
        adv.add(Divider())

        self.spin_read = QDoubleSpinBox()
        self.spin_read.setRange(30, 3600)
        self.spin_read.setDecimals(0)
        self.spin_read.setSuffix(' 秒')
        self.spin_read.setValue(float(self.store.get('llm.read_timeout', 600) or 600))
        self.spin_read.valueChanged.connect(
            lambda v: self._save('llm.read_timeout', float(v)))
        adv.add(SettingRow('生成超时', '从发出请求到模型开始回话的等待上限。'
                                     '本地大模型建议 300 秒以上。', self.spin_read))
        adv.add(Divider())

        self.combo_keep = QComboBox()
        for label, value in KEEP_ALIVE_ITEMS:
            self.combo_keep.addItem(label, value)
        self._select_data(self.combo_keep, self.store.get('llm.keep_alive', -1))
        self.combo_keep.currentIndexChanged.connect(
            lambda _i: self._save('llm.keep_alive', self.combo_keep.currentData()))
        adv.add(SettingRow('模型常驻（Ollama）',
                           '仅 Ollama 后端有效；直连 llama.cpp 的模型本来就常驻进程内。',
                           self.combo_keep))
        adv.add(Divider())

        self.sw_unify = ToggleSwitch(
            checked=bool(self.store.get('llm.unify_roles', True)))
        self.sw_unify.toggled.connect(self._on_unify)
        adv.add(SettingRow('所有角色统一用这个模型',
                           '关掉后，LLM 目录里每个角色文件各自的模型设置才会生效。',
                           self.sw_unify))
        adv.add(Divider())

        self.sw_warmup = ToggleSwitch(
            checked=bool(self.store.get('llm.warmup_on_start', True)))
        self.sw_warmup.toggled.connect(
            lambda v: self._save('llm.warmup_on_start', bool(v)))
        adv.add(SettingRow('开机后自动预热模型',
                           '启动听写时就把模型读进内存，第一句话不用等。'
                           '内存吃紧的机器可以关掉。',
                           self.sw_warmup))
        page.add(adv)

        # 说明
        help_card = Card()
        help_card.add_title('怎么用自己下载的模型')
        text = QLabel(
            '1. 下载一个 .gguf 文件（HuggingFace、ModelScope 上都有），\n'
            '   放在任意盘任意目录都可以，不用拷进安装目录；\n'
            '2. 后端保持「直连 llama.cpp（推荐）」，点「选择模型文件（已下载好的）」\n'
            '   直接选中那个文件；「下载推荐润色模型」只在需要恢复出厂模型时才用；\n'
            '3. 点「测试连接」，看到「连接正常 · 首字 x.xs」就能用了。\n\n'
            '程序自带 llama.cpp 动态库，直接在进程里加载 .gguf 并起一个只监听 '
            '127.0.0.1 的本地接口：不用装 Ollama、不用放 llama-server.exe、'
            '不用联网、也不会往 Ollama 里复制一份模型。\n'
            '只在「大模型」页测试时会临时占用模型，测完即释放，'
            '正式听写由客户端独占加载。'
        )
        text.setObjectName('RowDesc')
        text.setWordWrap(True)
        help_card.body().addWidget(text)
        page.add(help_card)

        page.add_stretch()
        self._on_backend()

    # ---------- 小工具 ----------

    @staticmethod
    def _select_data(combo: QComboBox, value) -> None:
        index = combo.findData(value)
        if index >= 0:
            combo.setCurrentIndex(index)

    def _save(self, path: str, value, save: bool = True) -> None:
        self.store.set(path, value, save=save)

    def _on_engine(self, _index: int = 0) -> None:
        """选「外部 llama-server.exe」前先确认本机真的有这个 exe。

        v2.12：安装包从来不含 llama-server.exe，老版本允许把这个值直接
        写进配置，结果 server.ensure() 跳过进程内引擎去找不存在的 exe，
        表现就是「装完不识别语言大模型」。这里从设置入口堵住陷阱：
        找不到 exe 就明确提示并回落到 auto，绝不保存坏值。
        """
        mode = self.combo_engine.currentData()
        if mode == 'server':
            from llmkit import server as llm_server
            exe = str(self.store.get('llm.llama_exe', '') or '')
            if not llm_server.find_server_exe(exe):
                self.combo_engine.blockSignals(True)
                self._select_data(self.combo_engine, 'auto')
                self.combo_engine.blockSignals(False)
                self._save('llm.engine_mode', 'auto')
                QMessageBox.information(
                    self, '没找到外部 llama-server.exe',
                    '安装包自带进程内 llama.cpp 引擎，不包含外部 '
                    'llama-server.exe，也不需要你另外安装任何程序。\n\n'
                    '已保持「自动（优先进程内）」。如果你确实自己放了 '
                    'llama-server.exe，请把它放到安装目录的 runtime/llama/ '
                    '下再选这一项。')
                return
        self._save('llm.engine_mode', mode)

    def _apply(self) -> None:
        """把设置推给正在跑的听写客户端"""
        self.store.save()
        try:
            self.window.supervisor.restart_client()
        except Exception:
            pass

    def _host(self) -> str:
        """扫描/导入用的本机 Ollama 地址（只有 Ollama 后端才会用到）"""
        url = (self.edit_url.text() or '').strip()
        if self.combo_backend.currentData() == 'openai':
            return llmkit.APIConfig.DEFAULT_API_URLS['ollama']
        return url or llmkit.APIConfig.DEFAULT_API_URLS['ollama']

    def _current_backend(self):
        return llm_connector.from_settings(self.store.get('llm', {}))

    def _is_direct(self) -> bool:
        return self.combo_backend.currentData() == 'local'

    # ---------- 交互 ----------

    def _on_backend(self, *_args) -> None:
        backend = self.combo_backend.currentData()
        # v2.13：界面已经明确选过后端，就给这份配置打上「已迁移」标记。
        # 老实现只写 llm.backend，从不写 app.llm_backend_migrated，于是
        # 用户主动选了 Ollama 之后，下次启动 Store.reload() 里的 v2.8
        # 一次性迁移条件（not migrated and backend == 'ollama'）仍然成立，
        # 会把它静默改回 local —— 表现为「选好的后端重启就没了」。
        # save=False：紧接着 _save('llm.backend', ...) 会写盘，一次落两个值。
        self.store.set('app.llm_backend_migrated', True, save=False)
        self._save('llm.backend', backend)
        desc = next((d for k, _l, d in BACKENDS if k == backend), '')
        self.lbl_backend_desc.setText(desc)

        direct = backend == 'local'
        remote = backend == 'openai'
        use_ollama = backend == 'ollama'

        self.edit_key.setEnabled(remote)
        if direct:
            self.edit_url.setPlaceholderText(
                '留空即可（进程内服务 127.0.0.1:11500，一般不用填）')
        elif remote:
            self.edit_url.setPlaceholderText('https://api.deepseek.com/v1')
        else:
            self.edit_url.setPlaceholderText('http://127.0.0.1:11434')

        # 直连 llama.cpp 不需要 Ollama 的常驻设置，也不需要它的服务地址
        self.combo_keep.setEnabled(use_ollama)
        self.edit_url.setEnabled(not direct)
        self.combo_engine.setEnabled(direct)
        self.spin_ctx.setEnabled(direct)
        self.spin_gpu.setEnabled(direct)
        self.btn_import.setText(
            '导入到 Ollama' if use_ollama else '选择模型文件（已下载好的）')

        # v2.14：远程 API 后端只显示「服务方模型名」输入框，把本地 .gguf
        # 下拉框 / 文件按钮整块藏起来；Ollama 后端仍保留「导入到 Ollama」。
        # 这样用户切到远程 API 后不会再面对一个全是本机 gguf 的下拉框，
        # 也就不会误点一下把服务方模型名冲成 GGUF 文件名。
        self.lbl_remote_model.setVisible(not direct)
        self.edit_remote_model.setVisible(not direct)
        self.edit_remote_model.setEnabled(not direct)
        self.local_file_row.setVisible(not remote)
        self.local_dir_row.setVisible(not remote)
        self._sync_remote_model()

        # 从远程切回直连 llama.cpp 时，把 llm.model 恢复成 GGUF 文件名
        # stem（local 后端的既有约定）。只在真正不一致时才写盘，避免
        # 「打开一次设置页就静默改配置」这类 v2.12 已经修掉的回归。
        if direct:
            path = str(self.store.get('llm.model_path', '') or '').strip()
            if path and Path(path).is_file():
                stem = Path(path).stem
                if str(self.store.get('llm.model', '') or '') != stem:
                    self._save('llm.model', stem)
        self.refresh()

    def _remote_model_text(self) -> str:
        """远程后端输入框应该显示的服务方模型名。

        llm.model 在 local 下是 GGUF 文件名 stem、在远程下是服务方模型名，
        共用同一个键。从 local 切到远程时磁盘上往往还留着 GGUF stem，直接
        显示会让用户以为那就是远程模型名，点「测试连接」还会拿它去请求
        在线服务。这里按「当前值恰好等于 model_path 的 stem」识别并留空，
        只影响显示，不写盘、不清空用户配置。
        """
        if self.combo_backend.currentData() == 'local':
            return ''
        current = str(self.store.get('llm.model', '') or '').strip()
        path = str(self.store.get('llm.model_path', '') or '').strip()
        if path and current and current == Path(path).stem:
            return ''
        return current

    def _sync_remote_model(self) -> None:
        self.edit_remote_model.blockSignals(True)
        self.edit_remote_model.setText(self._remote_model_text())
        self.edit_remote_model.blockSignals(False)

    def _on_remote_model(self) -> None:
        """用户输入服务方模型名：只写 llm.model，绝不碰 llm.model_path。"""
        if self.combo_backend.currentData() == 'local':
            return
        text = (self.edit_remote_model.text() or '').strip()
        self._save('llm.model', text)
        self.chip_model.setText(text or '未填服务方模型名')
        self.chip_model.set_tone('info' if text else 'muted')

    def _on_url(self) -> None:
        self._save('llm.api_url', (self.edit_url.text() or '').strip())
        self.refresh()

    def _on_key(self) -> None:
        self._save('llm.api_key', (self.edit_key.text() or '').strip())

    def _on_unify(self, value: bool) -> None:
        self._save('llm.unify_roles', bool(value))
        self._apply()

    def _on_enabled(self, value: bool) -> None:
        self.store.set('client.llm_enabled', bool(value))
        self._apply()

    def _warning_for(self, path: str) -> str:
        """这个 .gguf 是不是不能拿来对话（没有 chat template）"""
        if not path:
            return ''
        for model in self._models:
            if (model.kind == 'gguf' and
                    llmkit.same_path(model.path, path)):
                return model.warning or ''
        return ''

    def _on_model(self, *_args, persist: Optional[bool] = None) -> None:
        """下拉框选中变化。

        v2.12：恢复选中（_restoring=True）时只刷界面，绝不写盘。老实现
        在这里无条件 _save()（Store.set 默认立即落盘），配合 _on_scan_done()
        里「没匹配上就 fallback 选内置模型」的逻辑，会把用户人工选好的
        .gguf 悄悄换成内置模型 —— 只要路径写法不同、安装目录是符号链接、
        或模型所在盘当时没挂载，打开一次设置页就会丢配置。
        persist 由「从未配置过模型」时的自动默认选择显式传 True。
        """
        data = self.combo_model.currentData()
        if not data:
            return
        if persist is None:
            persist = not self._restoring
        kind, value = data
        backend = self.combo_backend.currentData()

        # v2.14：本地 .gguf 下拉项只在「直连 llama.cpp」下才有意义。
        # 远程 API / Ollama 后端里 llm.model 是服务方模型名，llm.model_path
        # 根本不该存在；老实现无条件写这两个键，用户误点一下就把
        # deepseek-chat 冲成 GGUF 文件名，还 _apply() 重启客户端。
        # 即使界面已经把本地文件控件藏起来，这里仍要守住：脏配置、
        # 异步扫描竞态、或外部代码往下拉框塞项都不能突破。
        if kind == 'gguf' and backend != 'local':
            # 恢复选中（_restoring）时保持静默，别把扫描结果文案顶掉
            if not self._restoring:
                self.lbl_model.setText(
                    '当前后端不使用本地 .gguf。请在「服务方模型名」里填写'
                    '服务商给的模型名；要用本地模型请切回「直连 llama.cpp」。')
            return
        if kind == 'ollama' and backend != 'ollama':
            return

        if kind == 'gguf':
            self._save('llm.model_path', value, save=persist)
            self._save('llm.model', Path(value).stem, save=persist)
            warning = self._warning_for(value)
            tip = f'当前模型文件：{value}\n选中后由 llama.cpp 直接加载，无需注册。'
            if warning:
                tip += f'\n⚠ {warning}'
            self.lbl_model.setText(tip)
            self.chip_model.setText(Path(value).stem)
            self.chip_model.set_tone('warn' if warning else 'info')
        else:
            self._save('llm.model', value, save=persist)
            self.lbl_model.setText(f'当前 Ollama 模型：{value}')
            self.chip_model.setText(value)
            self.chip_model.set_tone('info')
            # 下拉里选了已安装的 Ollama 模型，输入框也跟着显示
            self._sync_remote_model()
        self.btn_import.setEnabled(True)
        if persist:
            self._apply()

    def _auto_pick_index(self) -> int:
        """自动默认选择：优先随包推荐对话模型，其次第一个带 chat template 的 gguf。

        绝不落到 ASR / embedding 之类的「非对话模型」上；一个都没有就
        返回 -1，宁可让用户自己点「选择模型文件（已下载好的）」。
        """
        bundled = llmkit.bundled_chat_model()
        preferred = Path(bundled).name.lower() if bundled else ''
        if preferred:
            for i in range(self.combo_model.count()):
                kind, value = self.combo_model.itemData(i)
                if kind == 'gguf' and Path(value).name.lower() == preferred:
                    return i
        chat_ready = {llmkit.norm_path(m.path) for m in self._models
                      if m.kind == 'gguf' and m.chat_ready}
        for i in range(self.combo_model.count()):
            kind, value = self.combo_model.itemData(i)
            if kind == 'gguf' and llmkit.norm_path(value) in chat_ready:
                return i
        return -1

    def _add_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, '选择模型所在目录', str(Path.home()))
        if not path:
            return
        # v2.14：用规范化路径判重。老实现是原始字符串 `if path not in dirs`，
        # 同一个目录带不带尾部分隔符 / 盘符大小写不同就会重复写进配置，
        # 换电脑后还残留一堆失效的绝对路径。
        before = list(self.store.get('llm.model_dirs', []) or [])
        dirs = self._append_dir(before, path)
        if len(dirs) != len(before):
            self._save('llm.model_dirs', dirs)
        self.refresh()

    @staticmethod
    def _append_dir(dirs: list, path: str) -> list:
        """把一个目录加进列表，按规范化路径去重。

        必须返回**新列表**、不改动入参：调用方靠
        `len(after) != len(before)` 判断「有没有变化、要不要写盘」。
        原地 append 会让两边指向同一个对象，长度永远相等，于是配置
        一个字也写不进去（v2.14 自检抓到过这个坑）。
        """
        out = list(dirs)
        key = llmkit.norm_path(path)
        if key and not any(llmkit.norm_path(str(d)) == key for d in out):
            out.append(path)
        return out

    def _open_model_dir(self) -> None:
        target = Path(llmkit.app_root()) / 'models' / 'llm'
        target.mkdir(parents=True, exist_ok=True)
        try:
            from core.tools.open_path import open_path
            open_path(target)
        except Exception:
            pass

    # ---------- 下载推荐润色模型 ----------

    def _download_model(self) -> None:
        """人工下载 / 重新下载推荐润色模型（唯一需要联网的可选动作）

        安装包已经内置这个模型，第一次装完就能用；启动路径不会自动调用下载。
        模型已就绪时再点，会先明确确认「是否覆盖重下」，确认后才传 force=True；
        想换其他模型走「选择模型文件」或直接拷贝 .gguf，不走这里。
        """
        if self._download_worker is not None:
            return
        dest = llmkit.model_download.target()
        ready = dest.is_file() and llmkit.model_download.validate(dest)[0]

        if ready:
            # 已内置时默认选「否」，避免误触把用户人工换过的模型覆盖掉
            reply = QMessageBox.question(
                self, '推荐模型已内置',
                f'内置润色模型已经在：\n{dest}\n\n'
                '第一次安装就有，通常不需要重新下载。\n'
                '只有文件损坏、想恢复出厂模型、或想重新取一份时才需要。\n\n'
                '继续会替换现有文件（约 1.04GB，需要联网，下载完成前润色不可用）。\n'
                '这一步完全由你手动触发；覆盖安装不会替换你人工换过的模型。\n\n'
                '仍然要重新下载吗？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
            if reply != QMessageBox.Yes:
                self.refresh()
                return
            force = True
        else:
            reply = QMessageBox.question(
                self, '下载推荐润色模型',
                f'将下载约 1.04GB 的中文润色模型到：\n{dest}\n\n'
                '这一步需要联网，只做一次；下载完推理完全离线，不装 Ollama。\n'
                '离线环境也可以把模型包解压到同一目录。\n\n现在开始下载？',
                QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
            if reply != QMessageBox.Yes:
                return
            force = False

        self.btn_download.setEnabled(False)
        self.lbl_model.setText('正在人工下载推荐润色模型…')

        def job(stage=None) -> dict:
            ok, message = llmkit.model_download.download(
                force=force, on_stage=stage)
            return {'ok': ok, 'message': message, 'force': force}

        self._download_worker = run_async(
            self, job, self._on_download_done,
            on_stage=lambda text: self.lbl_model.setText(text))

    def _on_download_done(self, result: dict) -> None:
        self._download_worker = None
        self.btn_download.setEnabled(True)
        if not isinstance(result, dict):
            result = {'ok': False, 'message': str(result)}
        ok = bool(result.get('ok'))
        message = str(result.get('message') or '')
        if not ok:
            self.lbl_model.setText(f'下载失败：{message}')
            QMessageBox.warning(self, '下载失败', message)
            return
        dest = llmkit.model_download.target()
        backend = self.combo_backend.currentData()
        if backend == 'local':
            self._save('llm.model_path', str(dest))
            self._save('llm.model', dest.stem)
            prefix = ('已重新下载并恢复为默认模型' if result.get('force')
                      else '下载完成，已设为默认模型')
            self.lbl_model.setText(f'{prefix}。\n{message}')
        else:
            # v2.13：远程 API / Ollama 后端只是把文件备在本机，不能拿它
            # 覆盖 llm.model —— 那个键在远程模式下是服务方的模型名。
            self.lbl_model.setText(
                f'推荐模型文件已就位：{dest}\n{message}\n'
                '当前后端不使用本地 .gguf，模型名保持原样；'
                '想用这个文件请切回「直连 llama.cpp（推荐）」。')
        self._apply()
        self.refresh()

    # ---------- 扫描 ----------

    def refresh(self) -> None:
        if self._scan_worker is not None:
            return
        self.btn_refresh.setEnabled(False)
        self.lbl_model.setText('正在扫描本机模型…')

        # Qt 控件只能在主线程读，参数先取好再交给后台线程
        host = self._host()
        extra_dirs = list(self.store.get('llm.model_dirs', []) or [])
        # 配置里记过的模型目录也扫：用户把模型搬了家、或把整个绿色版
        # 拷到另一台电脑后，旧路径的父目录常常正是现在放模型的地方。
        configured = str(self.store.get('llm.model_path', '') or '').strip()
        if configured:
            extra_dirs = self._append_dir(extra_dirs, str(Path(configured).parent))
        # 直连 llama.cpp 时完全不碰 Ollama：不查端口、不显示「Ollama 未运行」
        backend = self.combo_backend.currentData()
        include_ollama = backend == 'ollama'
        # v2.14：远程 API 后端根本不用本地 .gguf。老实现不传 include_gguf
        # （默认 True），discover() 照样把本机模型塞满 combo_model，用户
        # 一选就写坏 llm.model / llm.model_path。Ollama 后端保留 gguf 列表，
        # 因为「导入到 Ollama」需要它；但 _on_model() 已按后端守住写盘。
        include_gguf = backend != 'openai'

        def job(_stage=None) -> dict:
            return llmkit.discover(host, extra_dirs,
                                   include_gguf=include_gguf,
                                   include_ollama=include_ollama)

        self._scan_worker = run_async(self, job, self._on_scan_done)

    def _on_scan_done(self, result: dict) -> None:
        self._scan_worker = None
        self.btn_refresh.setEnabled(True)
        if not result.get('ok', True):
            self.lbl_model.setText(f'扫描失败：{result.get("message") or "未知错误"}')
            return

        installed = result.get('installed') or []
        files = result.get('files') or []
        self._models = list(installed) + list(files)

        backend = self.combo_backend.currentData()
        current = self.store.get('llm.model', '')
        model_path = self.store.get('llm.model_path', '')

        self._restoring = True
        self.combo_model.blockSignals(True)
        self.combo_model.clear()
        for model in self._models:
            if model.kind == 'ollama':
                self.combo_model.addItem(model.display, ('ollama', model.name))
            else:
                self.combo_model.addItem(model.display, ('gguf', model.path))
        # v2.12：配置里记着、但这次没扫到的模型（符号链接路径写法不同、
        # 目录没加进扫描、外置盘暂时没挂载……）也必须作为一条下拉项出现。
        # 否则下面的匹配会失败，老逻辑会当成「没保存过选择」而自动挑内置
        # 模型并写盘，等于悄悄覆盖用户人工选好的模型。
        configured_file = bool(model_path) and Path(model_path).is_file()
        if backend == 'local' and configured_file:
            matched = any(
                self.combo_model.itemData(i)[0] == 'gguf' and
                llmkit.same_path(self.combo_model.itemData(i)[1], model_path)
                for i in range(self.combo_model.count())
            )
            if not matched:
                self.combo_model.addItem(
                    f'{Path(model_path).stem}（当前配置 · 未加入扫描目录）',
                    ('gguf', model_path))
        self.combo_model.blockSignals(False)

        index = -1
        for i in range(self.combo_model.count()):
            kind, value = self.combo_model.itemData(i)
            # 用 same_path：统一大小写 / 斜杠，并解析符号链接 / junction。
            # 安装目录是链接时，配置里的 C:\... 和扫描出的 D:\... 指向
            # 同一个文件，只比字符串会「扫到了却选不中」。
            if (kind == 'gguf' and model_path and
                    llmkit.same_path(value, model_path)):
                index = i
                break
            if kind == 'ollama' and value == current:
                index = i
                break
        if index >= 0:
            # 恢复选中：_restoring=True → _on_model() 只刷界面，不写盘
            self.combo_model.setCurrentIndex(index)
        elif (backend == 'local' and not model_path and
              self.combo_model.count() > 0):
            # v2.13：必须限定 backend == 'local'。discover() 不管后端是
            # 什么都返回本机 .gguf 列表，远程 API / Ollama 模式下下拉框
            # 照样被本地模型填满；不限定的话，index 匹配失败就会自动挑
            # 内置模型并 _on_model(persist=True) 写盘，把用户填的
            # deepseek-chat / qwen2.5:7b 冲成 GGUF 文件名，还塞进一个
            # 根本用不上的 model_path，并 _apply() 重启客户端。
            # 这是 v2.9 就定下的约束（「切到远程 API / Ollama 不会被改
            # 回去」），v2.12 重写本方法时漏掉了。
            # 另外只有「从来没配置过模型」才允许自动选并落盘；已经配置过
            # （哪怕路径暂时失效）就保持原值，由下面的提示引导用户手选。
            pick = self._auto_pick_index()
            if pick >= 0:
                self.combo_model.blockSignals(True)
                self.combo_model.setCurrentIndex(pick)
                self.combo_model.blockSignals(False)
                self._on_model(persist=True)
        self._restoring = False

        native_ok = bool(result.get('native_ok'))
        native_message = str(result.get('native_message') or '')
        if backend == 'openai':
            self.chip_backend.setText('远程 API 模式')
            self.chip_backend.set_tone('info')
        elif backend == 'ollama':
            serving = bool(result.get('serving'))
            self.chip_backend.setText('Ollama 已连接' if serving else 'Ollama 未运行')
            self.chip_backend.set_tone('ok' if serving else 'warn')
        else:
            self.chip_backend.setText('llama.cpp 就绪' if native_ok else '动态库缺失')
            self.chip_backend.set_tone('ok' if native_ok else 'err')

        warning = self._warning_for(model_path) if backend == 'local' else ''
        if backend == 'local':
            chosen = Path(model_path).name if model_path else ''
            self.chip_model.setText(chosen or '未选择模型文件')
            self.chip_model.set_tone(
                ('warn' if warning else ('info' if chosen else 'muted')))
        else:
            # v2.14：远程后端显示「服务方模型名」，不能用 current —— 它
            # 可能是从 local 切过来时残留的 GGUF 文件名 stem。
            shown = self._remote_model_text()
            self.chip_model.setText(shown or '未填服务方模型名')
            self.chip_model.set_tone('info' if shown else 'muted')

        if backend == 'local':
            non_chat = sum(1 for m in files if not m.chat_ready)
            tip = f'发现 {len(files)} 个 .gguf 文件；选中即由 llama.cpp 直接加载，不用注册。'
            if non_chat:
                tip += f'\n其中 {non_chat} 个没有对话模板（已标「非对话模型」），不适合润色。'
            if not native_ok:
                tip += f'\n注意：{native_message}'
            if not files:
                tip += ('\n本机还没扫到 .gguf。点「选择模型文件（已下载好的）」'
                        '直接挑你已经下载好的那个文件，'
                        f'或把它拷进 {Path(llmkit.app_root()) / "models" / "llm"}；'
                        '模型放在其他盘也可以点「添加扫描目录」。')
            if model_path and not Path(model_path).is_file():
                tip += (f'\n⚠ 原来配置的模型文件已不存在：{model_path}\n'
                        '这份设置可能来自另一台电脑 / 另一个安装目录，'
                        '请在下拉框重新选一个，或点「选择模型文件（已下载好的）」。')
            elif configured_file and not any(
                    llmkit.same_path(m.path, model_path) for m in files):
                tip += ('\n当前模型不在扫描目录里，已作为「当前配置」保留在下拉框，'
                        '不会被自动替换。想让它每次都自动出现，'
                        '可点「添加扫描目录」把所在目录加进来。')
            if warning:
                tip += f'\n⚠ 当前选中的模型：{warning}'
            self.lbl_model.setText(tip)
        elif backend == 'ollama':
            self.lbl_model.setText(
                f'Ollama 已就绪 {len(installed)} 个，发现 {len(files)} 个待导入的 gguf。'
                '服务方模型名请填在上方输入框，或在下拉里选已安装的模型。'
                if (installed or files) else
                '没找到模型。点「导入到 Ollama」选一个 .gguf，'
                '或在上方「服务方模型名」里填已有模型名。'
            )
        else:
            self.lbl_model.setText(
                '远程 API 模式：在上方「服务方模型名」填服务商给的模型名'
                '（例如 deepseek-chat）。不需要本地 .gguf，'
                '也不会自动改你填的模型名。')

    # ---------- 测试 ----------

    def _test(self) -> None:
        if self._test_worker is not None:
            return
        # v2.14：远程后端先把输入框内容落到配置。用户可能没让输入框失焦
        # 就直接点「测试连接」，不先同步就会拿旧模型名去请求在线服务。
        if self.combo_backend.currentData() != 'local':
            self._on_remote_model()
        backend = self._current_backend()
        if backend.kind != 'local' and not backend.model:
            QMessageBox.information(
                self, '还没填服务方模型名',
                '请在「服务方模型名」输入框里填写服务商给的模型名，'
                '例如 deepseek-chat（远程 API）或 qwen2.5:7b（Ollama）。\n'
                '这一项不是本地 .gguf 文件名，也不需要选择模型文件。')
            return
        if backend.kind == 'local' and not backend.model_path:
            QMessageBox.information(self, '还没选模型文件',
                                    '请点「选择模型文件」选一个本地 .gguf。')
            return

        self.btn_test.setEnabled(False)
        self.lbl_test.setText('正在连接…首次加载模型可能要几十秒，请稍候。')

        def job(_stage=None) -> dict:
            # 本地后端先把服务/模型拉起来，再发测试对话，
            # 和听写客户端跑的是同一条 ensure() 路径
            if backend.is_local:
                ok, message = llm_connector.ensure(backend)
                if not ok:
                    return {'ok': False, 'message': message}
            return llm_connector.chat_test(backend)

        self._test_worker = run_async(self, job, self._on_test_done)

    def _on_test_done(self, result: dict) -> None:
        self._test_worker = None
        self.btn_test.setEnabled(True)
        # 界面进程不常驻模型：测完就释放，正式听写给客户端独占，
        # 避免 GUI + 客户端各加载一份大模型把内存吃满。
        if self._is_direct():
            try:
                llmkit.server.shutdown()
            except Exception:
                pass
        if result.get('ok'):
            first = result.get('first_token_ms') or 0
            total = result.get('total_ms') or 0
            raw = result.get('text') or ''
            text = raw.replace('\n', ' ')[:40]
            detail = f'首字 {first / 1000:.1f}s · 总耗时 {total / 1000:.1f}s'
            if text:
                detail += f' · 回复：{text}'
            if '<asr_text>' in raw or 'language ' in raw:
                # 链路是通的，但选的是 ASR 专用模型，别让用户以为润色能用
                self.lbl_test.setText(
                    f'链路正常，但模型返回了语音识别格式（{text}）。'
                    '这个 .gguf 是 ASR / 补全专用模型，不能润色；'
                    '请换一个带 -Instruct 的对话模型。')
                self.lbl_test.setStyleSheet(f'color: {C["danger"]};')
                self.chip_backend.setText('模型类型不对')
                self.chip_backend.set_tone('warn')
            else:
                self.lbl_test.setText(f'连接正常。{detail}')
                self.lbl_test.setStyleSheet(f'color: {C["success"]};')
                self.chip_backend.setText('连接正常')
                self.chip_backend.set_tone('ok')
        else:
            self.lbl_test.setText(f'连接失败：{result.get("message") or "未知错误"}')
            self.lbl_test.setStyleSheet(f'color: {C["danger"]};')
            self.chip_backend.setText('连接失败')
            self.chip_backend.set_tone('err')

    # ---------- 导入 / 选择 ----------

    def _import(self) -> None:
        """点「选择模型文件」必须真的弹出文件选择框。

        老实现先看下拉框当前项：只要下拉框里已经有一个 gguf，就直接沿用
        它，根本不弹框。用户把自己下载好的千问模型放在别的目录时，这个
        按钮点了没反应，界面看起来就只剩「下载推荐润色模型」一条路 ——
        这正是「只能选择下载的路径」的来源。下拉框只负责切换已扫描到的
        模型，这个按钮专门负责从任意位置新选一个。
        """
        if self._import_worker is not None:
            return

        backend = self.combo_backend.currentData()
        if backend == 'openai':
            QMessageBox.information(
                self, '远程 API 模式',
                '远程 API 后端不需要本地模型文件。要用本地 .gguf，请先切回「直连 llama.cpp」。')
            return

        # 初始目录：优先当前模型所在目录，其次下载目录，最后家目录
        start_dir = str(Path.home())
        configured = str(self.store.get('llm.model_path', '') or '').strip()
        if configured and Path(configured).is_file():
            start_dir = str(Path(configured).parent)
        elif (Path.home() / 'Downloads').is_dir():
            start_dir = str(Path.home() / 'Downloads')

        path, _ = QFileDialog.getOpenFileName(
            self, '选择已经下载好的 .gguf 模型文件', start_dir,
            'GGUF 模型 (*.gguf)')
        if not path:
            return

        if backend == 'local':
            # 记住这个目录：下次启动自动扫描到，换电脑也不用再手选一次
            # v2.14：和 _add_dir() 一样按规范化路径去重；没变化就不写盘
            before = list(self.store.get('llm.model_dirs', []) or [])
            dirs = self._append_dir(before, str(Path(path).parent))
            if len(dirs) != len(before):
                self._save('llm.model_dirs', dirs)
            # 直连 llama.cpp：不需要注册、不需要 Ollama、不复制文件，
            # 只记下路径，选中它就能加载。
            self._save('llm.model_path', path)
            self._save('llm.model', Path(path).stem)
            self.lbl_model.setText(f'已选定模型文件：{path}')
            self.chip_model.setText(Path(path).name)
            self.chip_model.set_tone('info')
            self._apply()
            self.refresh()
            return

        self.btn_import.setEnabled(False)
        self.lbl_model.setText('正在导入…大模型要复制进 Ollama 的存储，可能需要几分钟。')

        # 控件参数在主线程取好，后台只做文件读取 + subprocess
        host = self._host()
        exe = self.store.get('llm.ollama_exe', '') or ''

        def job(stage) -> dict:
            ok, message = llmkit.import_gguf(
                path, host=host, exe=exe,
                on_stage=stage or (lambda text: None),
            )
            return {'ok': ok, 'message': message}

        self._import_worker = run_async(
            self, job, self._on_import_done,
            on_stage=lambda text: self.lbl_model.setText(text),
        )

    def _on_import_done(self, result: dict) -> None:
        self._import_worker = None
        self.btn_import.setEnabled(True)
        message = result.get('message') or ''
        if result.get('ok'):
            self._save('llm.model', message)
            self._sync_remote_model()   # v2.14：导入出的模型名同步进输入框
            self.lbl_model.setText(f'导入成功：{message}，已设为当前模型。')
            self._apply()
            self.refresh()
        else:
            self.lbl_model.setText(f'导入失败：{message}')
