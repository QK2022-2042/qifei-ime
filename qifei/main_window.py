# coding: utf-8
"""
主窗口：概览 / 快捷键 / 语音模型 / 设置 / 关于

所有改动都会立刻写入 config.json，并在需要时重启后台进程生效。
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSize, Qt, QThread, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import (QButtonGroup, QComboBox, QDoubleSpinBox,
                               QFileDialog, QFrame, QHBoxLayout, QLabel,
                               QLineEdit, QMessageBox, QProgressBar,
                               QPushButton, QScrollArea, QSizePolicy,
                               QStackedWidget, QVBoxLayout, QWidget)

from . import autostart, brand, paths
from .llm_page import LLMPage
from .model_manager import (MODELS, DownloadCancelled, ModelDownloader,
                            detect_installed, is_ready, missing_files)
from .store import Store, store
from .theme import C
from .widgets import (BrandMark, Card, Divider, KeyCap, LevelMeter, Page,
                      SettingRow, StatusChip, ToggleSwitch)

NAV_ITEMS = [
    ('overview', '概览'),
    ('hotkey', '快捷键'),
    ('model', '语音模型'),
    ('llm', '大模型'),
    ('general', '设置'),
    ('about', '关于'),
]


# 页面骨架与状态标签在 widgets 里，大模型页也要用

# ────────────────────────────────────────────────────────────
# 引擎选择卡
# ────────────────────────────────────────────────────────────
class EngineCard(QFrame):
    """可选择、可点击的引擎卡片"""

    clicked = Signal(str)

    def __init__(self, spec, installed: bool, selected: bool, parent=None):
        super().__init__(parent)
        self.spec = spec
        self._selected = selected
        self.setCursor(Qt.PointingHandCursor)
        self.setObjectName('EngineCard')

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(6)

        top = QHBoxLayout()
        top.setSpacing(8)
        name = QLabel(spec.name)
        name.setStyleSheet('font-size: 14px; font-weight: 600;')
        top.addWidget(name)

        badge = QLabel(spec.badge)
        badge.setStyleSheet(
            f'background: {C["primary_soft"]}; color: {C["primary"]};'
            'border-radius: 7px; padding: 2px 8px; font-size: 11px; font-weight: 600;'
        )
        top.addWidget(badge)
        top.addStretch(1)

        state = QLabel('已安装' if installed else spec.tag)
        state.setStyleSheet(
            f'color: {C["success"] if installed else C["text_muted"]}; font-size: 11px;'
        )
        top.addWidget(state)
        self.state_label = state
        layout.addLayout(top)

        desc = QLabel(spec.desc)
        desc.setObjectName('RowDesc')
        desc.setWordWrap(True)
        layout.addWidget(desc)

        self._apply_style()

    def _apply_style(self) -> None:
        if self._selected:
            self.setStyleSheet(
                f'QFrame#EngineCard {{ background: {C["primary_soft"]};'
                f'border: 1px solid {C["primary"]}; border-radius: 12px; }}'
            )
        else:
            self.setStyleSheet(
                f'QFrame#EngineCard {{ background: {C["surface"]};'
                f'border: 1px solid {C["border"]}; border-radius: 12px; }}'
                f'QFrame#EngineCard:hover {{ border-color: {C["border_strong"]}; }}'
            )

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self._apply_style()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.spec.key)
        super().mouseReleaseEvent(event)


# ────────────────────────────────────────────────────────────
# 下载线程
# ────────────────────────────────────────────────────────────
class DownloadThread(QThread):
    stage = Signal(str)
    progress = Signal(int, int)
    done = Signal(bool, str)

    def __init__(self, spec, root: Path, mirror: str, parent=None):
        super().__init__(parent)
        self.spec = spec
        self.downloader = ModelDownloader(root, mirror)

    def run(self) -> None:
        try:
            ok, message = self.downloader.install(
                self.spec,
                on_stage=lambda s: self.stage.emit(s),
                on_progress=lambda d, t: self.progress.emit(d, t),
            )
        except DownloadCancelled:
            ok, message = False, '已取消'
        except Exception as exc:
            ok, message = False, str(exc)
        self.done.emit(ok, message)

    def cancel(self) -> None:
        self.downloader.cancel()


# ────────────────────────────────────────────────────────────
# 主窗口
# ────────────────────────────────────────────────────────────
class MainWindow(QWidget):

    def __init__(self, supervisor, indicator, parent=None):
        super().__init__(parent)
        self.setObjectName('Root')
        self.setWindowTitle(f'{brand.BRAND_CN} · 设置')
        self.resize(940, 660)
        self.setMinimumSize(880, 600)

        self.store: Store = store()
        self.supervisor = supervisor
        self.indicator = indicator
        self._download_thread: Optional[DownloadThread] = None
        self._engine_cards: dict[str, EngineCard] = {}
        self._selected_engine = self.store.get('server.model_type', 'sensevoice')

        self._build()
        self._connect_supervisor()
        self._refresh_engine_states()
        self._refresh_status()

    # ---------- 构建 ----------

    def _build(self) -> None:
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        root.addWidget(self._build_sidebar())

        self.stack = QStackedWidget()
        self.pages = {
            'overview': self._build_overview(),
            'hotkey': self._build_hotkey(),
            'model': self._build_model(),
            'llm': LLMPage(self),
            'general': self._build_general(),
            'about': self._build_about(),
        }
        for key, _label in NAV_ITEMS:
            self.stack.addWidget(self.pages[key])
        root.addWidget(self.stack, 1)

    def _build_sidebar(self) -> QWidget:
        side = QFrame()
        side.setObjectName('Sidebar')
        side.setFixedWidth(212)

        layout = QVBoxLayout(side)
        layout.setContentsMargins(16, 22, 16, 18)
        layout.setSpacing(6)

        brand_row = QHBoxLayout()
        brand_row.setSpacing(10)
        brand_row.addWidget(BrandMark(38))
        text_box = QVBoxLayout()
        text_box.setSpacing(0)
        name = QLabel(brand.BRAND_CN)
        name.setObjectName('BrandName')
        tag = QLabel(brand.BRAND_TAGLINE)
        tag.setObjectName('BrandTag')
        text_box.addWidget(name)
        text_box.addWidget(tag)
        brand_row.addLayout(text_box, 1)
        layout.addLayout(brand_row)
        layout.addSpacing(18)

        self._nav_group = QButtonGroup(self)
        self._nav_group.setExclusive(True)
        for index, (key, label) in enumerate(NAV_ITEMS):
            button = QPushButton(label)
            button.setObjectName('NavButton')
            button.setCheckable(True)
            button.setCursor(Qt.PointingHandCursor)
            button.clicked.connect(lambda _=False, i=index: self.stack.setCurrentIndex(i))
            self._nav_group.addButton(button, index)
            layout.addWidget(button)
            if index == 0:
                button.setChecked(True)

        layout.addStretch(1)

        self.side_status = QLabel('后台未启动')
        self.side_status.setObjectName('BrandTag')
        self.side_status.setWordWrap(True)
        layout.addWidget(self.side_status)

        version = QLabel(f'v{brand.VERSION}')
        version.setObjectName('BrandTag')
        layout.addWidget(version)
        return side

    # ---------- 概览 ----------

    def _build_overview(self) -> Page:
        page = Page('概览', '启飞输入法在后台常驻，任何输入框里都能说话上屏。')

        hero = Card()
        hero.add_title('当前状态')
        row = QHBoxLayout()
        row.setSpacing(10)
        self.chip_server = StatusChip('模型未就绪', 'muted')
        self.chip_client = StatusChip('听写未启动', 'muted')
        self.chip_engine = StatusChip('—', 'muted')
        for chip in (self.chip_server, self.chip_client, self.chip_engine):
            row.addWidget(chip)
        row.addStretch(1)
        hero.body().addLayout(row)

        self.status_hint = QLabel('正在启动…')
        self.status_hint.setObjectName('RowDesc')
        self.status_hint.setWordWrap(True)
        hero.body().addWidget(self.status_hint)
        page.add(hero)

        guide = Card()
        guide.add_title('怎么用', '三个动作覆盖所有场景')

        def usage(keys, title, desc):
            row = QHBoxLayout()
            row.setSpacing(8)
            for text in keys:
                row.addWidget(KeyCap(text, 'primary'))
            row.addSpacing(6)
            text = QLabel(f'{title}　<span style="color:{C["text_muted"]}">{desc}</span>')
            text.setTextFormat(Qt.RichText)
            text.setObjectName('RowTitle')
            row.addWidget(text, 1)
            guide.body().addLayout(row)

        usage(['Tab'], '按一下开始录音，再按一下结束', '适合长段口述')
        usage(['按住 Space'], '按住说话，松开结束', '对讲机式，最快')
        usage(['Space'], '录音中按一下暂停，再按一下继续', '想思路时用')
        page.add(guide)

        tip = Card()
        tip.add_title('小提示')
        tip_text = QLabel(
            '· 识别结果默认去掉句尾逗号句号，可在设置里调整\n'
            '· 热词文件 hot.txt 支持音素模糊匹配，专业术语不再听错\n'
            '· 所有语音都在本机处理，不联网、不上传'
        )
        tip_text.setObjectName('RowDesc')
        tip_text.setWordWrap(True)
        tip.body().addWidget(tip_text)
        page.add(tip)
        page.add_stretch()
        return page

    # ---------- 快捷键 ----------

    def _build_hotkey(self) -> Page:
        page = Page('快捷键', '所有按键都在系统层拦截，不会影响其它软件的正常输入。')

        card = Card()
        card.add_title('录音触发方式')

        self.sw_tab = ToggleSwitch(checked=self.store.shortcut('tab').get('enabled', True))
        self.sw_space = ToggleSwitch(checked=self.store.shortcut('space').get('enabled', True))
        self.sw_x2 = ToggleSwitch(checked=self.store.shortcut('x2').get('enabled', True))
        self.sw_caps = ToggleSwitch(checked=self.store.shortcut('caps_lock').get('enabled', False))

        card.add(SettingRow('Tab 键 · 切换录音',
                            '按一下开始，再按一下结束；录音中按空格可暂停。', self.sw_tab))
        card.add(Divider())
        card.add(SettingRow('空格键 · 按住说话',
                            '空闲时按住录音、松开结束；轻点仍是普通空格。', self.sw_space))
        card.add(Divider())
        card.add(SettingRow('鼠标侧键 X2 · 按住说话',
                            '适合右手不离鼠标的场景。', self.sw_x2))
        card.add(Divider())
        card.add(SettingRow('CapsLock · 按住说话',
                            '老用户习惯保留，默认关闭以免误触。', self.sw_caps))
        page.add(card)

        timing = Card()
        timing.add_title('触发灵敏度')
        self.combo_threshold = QComboBox()
        for label, value in [('灵敏（0.15 秒）', 0.15), ('较快（0.20 秒）', 0.20),
                             ('标准（0.25 秒）', 0.25), ('稳妥（0.30 秒）', 0.30),
                             ('很稳（0.40 秒）', 0.40)]:
            self.combo_threshold.addItem(label, value)
        current = float(self.store.get('client.threshold', 0.3))
        best = min(range(self.combo_threshold.count()),
                   key=lambda i: abs(self.combo_threshold.itemData(i) - current))
        self.combo_threshold.setCurrentIndex(best)
        self.combo_threshold.currentIndexChanged.connect(self._on_threshold)
        timing.add(SettingRow('长按判定阈值',
                              '按住时间超过该值才算录音，低于则视为普通按键。'
                              '觉得空格打字有延迟就往灵敏调。',
                              self.combo_threshold))
        page.add(timing)

        note = Card()
        note.add_title('两种录音模式的区别')
        note_text = QLabel(
            '切换模式（Tab）：按一下开始，可以慢慢想、慢慢说，说完再按一下结束。\n'
            '长按模式（空格 / 鼠标侧键）：按住说，松开立刻上屏，节奏最快。\n'
            '暂停：录音中按一下空格，麦克风暂停采集，再按一下继续，整段合成一句话。'
        )
        note_text.setObjectName('RowDesc')
        note_text.setWordWrap(True)
        note.body().addWidget(note_text)
        page.add(note)

        self.sw_tab.toggled.connect(lambda v: self._toggle_shortcut('tab', v))
        self.sw_space.toggled.connect(lambda v: self._toggle_shortcut('space', v))
        self.sw_x2.toggled.connect(lambda v: self._toggle_shortcut('x2', v))
        self.sw_caps.toggled.connect(lambda v: self._toggle_shortcut('caps_lock', v))

        page.add_stretch()
        return page

    # ---------- 语音模型 ----------

    def _build_model(self) -> Page:
        page = Page('语音模型', '模型完全离线运行。选择引擎后可一键下载到本机。')

        engine_card = Card()
        engine_card.add_title('选择引擎', '按电脑性能挑一个即可，随时可以换。')
        installed = detect_installed(self.store.model_root)
        for key, spec in MODELS.items():
            card = EngineCard(spec, key in installed, key == self._selected_engine)
            card.clicked.connect(self._select_engine)
            self._engine_cards[key] = card
            engine_card.add(card)
        page.add(engine_card)

        path_card = Card()
        path_card.add_title('模型存放位置')
        row = QHBoxLayout()
        row.setSpacing(8)
        self.edit_root = QLineEdit(str(self.store.model_root))
        self.edit_root.setReadOnly(True)
        row.addWidget(self.edit_root, 1)
        btn_browse = QPushButton('更改目录')
        btn_browse.setObjectName('Ghost')
        btn_browse.clicked.connect(self._choose_root)
        row.addWidget(btn_browse)
        btn_open = QPushButton('打开')
        btn_open.setObjectName('Ghost')
        btn_open.clicked.connect(self._open_root)
        row.addWidget(btn_open)
        path_card.body().addLayout(row)

        hint = QLabel('可以指向已有的模型目录，程序会自动检测其中可用的引擎。')
        hint.setObjectName('RowDesc')
        hint.setWordWrap(True)
        path_card.body().addWidget(hint)
        page.add(path_card)

        download_card = Card()
        download_card.add_title('下载与安装')
        self.lbl_download = QLabel('')
        self.lbl_download.setObjectName('RowDesc')
        self.lbl_download.setWordWrap(True)
        download_card.body().addWidget(self.lbl_download)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(True)
        self.progress.setVisible(False)
        download_card.body().addWidget(self.progress)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        self.btn_download = QPushButton('下载并安装')
        self.btn_download.setObjectName('Primary')
        self.btn_download.clicked.connect(self._start_download)
        buttons.addWidget(self.btn_download)
        self.combo_mirror = QComboBox()
        self.combo_mirror.addItem('GitHub 直连', 'github')
        self.combo_mirror.addItem('国内加速镜像', 'ghproxy')
        self.combo_mirror.setCurrentIndex(
            1 if self.store.get('app.download_mirror') == 'ghproxy' else 0
        )
        buttons.addWidget(self.combo_mirror)
        buttons.addStretch(1)
        download_card.body().addLayout(buttons)
        page.add(download_card)

        lang_card = Card()
        lang_card.add_title('识别语言')
        self.combo_lang = QComboBox()
        for label, value in [('自动识别', 'auto'), ('中文', 'chinese'),
                             ('英文', 'english'), ('日文', 'japanese')]:
            self.combo_lang.addItem(label, value)
        index = self.combo_lang.findData(self.store.get('client.language', 'auto'))
        self.combo_lang.setCurrentIndex(max(0, index))
        self.combo_lang.currentIndexChanged.connect(self._on_language)
        lang_card.add(SettingRow('默认语言', '中文场景建议保持自动识别。', self.combo_lang))
        page.add(lang_card)

        page.add_stretch()
        self._refresh_download_hint()
        return page

    # ---------- 设置 ----------

    def _build_general(self) -> Page:
        page = Page('设置', '改动立即保存，需要时自动重启后台生效。')

        start_card = Card()
        start_card.add_title('启动与常驻')
        self.sw_autostart = ToggleSwitch(checked=autostart.is_enabled())
        self.sw_indicator = ToggleSwitch(checked=bool(self.store.get('app.show_indicator', True)))
        start_card.add(SettingRow('开机自动启动', '登录 Windows 后静默运行，随叫随到。',
                                  self.sw_autostart))
        start_card.add(Divider())
        start_card.add(SettingRow('显示悬浮状态条', '录音时在屏幕底部显示胶囊提示。',
                                  self.sw_indicator))
        page.add(start_card)

        out_card = Card()
        out_card.add_title('输出方式')
        self.sw_paste = ToggleSwitch(checked=bool(self.store.get('client.paste', False)))
        self.sw_punc = ToggleSwitch(checked=bool(self.store.get('client.save_audio', True)))
        self.sw_trad = ToggleSwitch(checked=bool(self.store.get('client.traditional_convert', False)))
        out_card.add(SettingRow('用粘贴方式输出',
                                '微信、WPS、Office 等程序会自动切换粘贴模式；此处可全局开启。',
                                self.sw_paste))
        out_card.add(Divider())
        out_card.add(SettingRow('保存录音文件', '按年月归档原始音频，方便回听与纠错。',
                                self.sw_punc))
        out_card.add(Divider())
        out_card.add(SettingRow('输出繁体中文', '需要繁体输入时开启。', self.sw_trad))
        page.add(out_card)

        tool_card = Card()
        tool_card.add_title('数据与维护')
        tools = QHBoxLayout()
        tools.setSpacing(8)
        for text, callback in [
            ('热词表', lambda: self._open_path(paths.app_root() / 'hot.txt')),
            ('替换规则', lambda: self._open_path(paths.app_root() / 'hot-rule.txt')),
            ('日志目录', lambda: self._open_path(paths.log_dir())),
            ('录音目录', lambda: self._open_path(paths.app_root())),
        ]:
            button = QPushButton(text)
            button.setObjectName('Ghost')
            button.clicked.connect(callback)
            tools.addWidget(button)
        tools.addStretch(1)
        tool_card.body().addLayout(tools)
        page.add(tool_card)

        restart_card = Card()
        restart_card.add_title('重启后台')
        row = QHBoxLayout()
        row.setSpacing(8)
        btn_restart = QPushButton('重启听写服务')
        btn_restart.setObjectName('Primary')
        btn_restart.clicked.connect(self._restart_all)
        row.addWidget(btn_restart)
        row.addStretch(1)
        restart_card.body().addLayout(row)
        page.add(restart_card)

        self.sw_autostart.toggled.connect(self._on_autostart)
        self.sw_indicator.toggled.connect(
            lambda v: self.store.set('app.show_indicator', bool(v)))
        self.sw_paste.toggled.connect(self._on_paste)
        self.sw_punc.toggled.connect(
            lambda v: self.store.set('client.save_audio', bool(v), save=True))
        self.sw_trad.toggled.connect(
            lambda v: self.store.set('client.traditional_convert', bool(v)))

        page.add_stretch()
        return page

    # ---------- 关于 ----------

    def _build_about(self) -> Page:
        page = Page('关于', brand.BRAND_SLOGAN)

        card = Card()
        head = QHBoxLayout()
        head.setSpacing(14)
        head.addWidget(BrandMark(56))
        text_box = QVBoxLayout()
        text_box.setSpacing(2)
        name = QLabel(brand.BRAND_CN)
        name.setStyleSheet('font-size: 18px; font-weight: 600;')
        version = QLabel(f'版本 {brand.VERSION}')
        version.setObjectName('RowDesc')
        text_box.addWidget(name)
        text_box.addWidget(version)
        head.addLayout(text_box, 1)
        card.body().addLayout(head)
        page.add(card)

        detail = Card()
        detail.add_title('技术说明')
        body = QLabel(
            f'{brand.BRAND_CN} 基于开源的 CapsWriter-Offline 二次开发，'
            '语音识别完全在本机完成，不联网、不上传音频。\n\n'
            '语音引擎：SenseVoice / Paraformer / Fun-ASR-Nano / Qwen3-ASR\n'
            '运行架构：服务端负责模型推理，客户端负责按键与麦克风，界面负责设置与状态。\n\n'
            f'{brand.COPYRIGHT}'
        )
        body.setObjectName('RowDesc')
        body.setWordWrap(True)
        detail.body().addWidget(body)

        link = QPushButton('打开项目主页')
        link.setObjectName('Link')
        link.setCursor(Qt.PointingHandCursor)
        link.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(brand.HOMEPAGE)))
        detail.body().addWidget(link, 0, Qt.AlignLeft)
        page.add(detail)
        page.add_stretch()
        return page

    # ---------- 交互 ----------

    def _toggle_shortcut(self, key: str, enabled: bool) -> None:
        self.store.set_shortcut_enabled(key, enabled)
        self.supervisor.restart_client()

    def _on_threshold(self, _index: int) -> None:
        value = float(self.combo_threshold.currentData() or 0.25)
        self.store.set('client.threshold', value)
        for item in self.store.get('client.shortcuts', []):
            if item.get('key') == 'space':
                # 空格轻点是普通输入，阈值比全局再低一点，打字更跟手
                item['threshold'] = max(0.15, round(value * 0.85, 2))
        self.store.save()
        self.supervisor.restart_client()

    def _on_language(self, _index: int) -> None:
        self.store.set('client.language', self.combo_lang.currentData())
        self.supervisor.restart_client()

    def _on_paste(self, value: bool) -> None:
        self.store.set('client.paste', bool(value))
        self.supervisor.restart_client()

    def _on_autostart(self, value: bool) -> None:
        ok = autostart.set_enabled(bool(value))
        self.store.set('app.autostart', bool(value))
        if not ok:
            QMessageBox.warning(self, '设置失败', '无法写入开机启动项，请检查系统权限。')

    def _select_engine(self, key: str) -> None:
        self._selected_engine = key
        for name, card in self._engine_cards.items():
            card.set_selected(name == key)
        self.store.set('server.model_type', key)
        self._refresh_download_hint()

    def _choose_root(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, '选择模型目录', str(self.store.model_root)
        )
        if not path:
            return
        self.store.set_model_root(path)
        self.edit_root.setText(path)
        self._refresh_engine_states()
        self._refresh_download_hint()
        self.supervisor.restart_all()

    def _open_root(self) -> None:
        root = self.store.model_root
        root.mkdir(parents=True, exist_ok=True)
        self._open_path(root)

    @staticmethod
    def _open_path(path: Path) -> None:
        try:
            from core.tools.open_path import open_path
            open_path(path if path.exists() else path.parent)
        except Exception:
            pass

    def _refresh_engine_states(self) -> None:
        root = self.store.model_root
        installed = detect_installed(root)
        for key, card in self._engine_cards.items():
            ok = key in installed
            card.state_label.setText('已安装' if ok else card.spec.tag)
            card.state_label.setStyleSheet(
                f'color: {C["success"] if ok else C["text_muted"]}; font-size: 11px;'
            )

    def _refresh_download_hint(self) -> None:
        spec = MODELS.get(self._selected_engine)
        if spec is None:
            return
        root = self.store.model_root
        missing = missing_files(spec, root)
        if not missing:
            self.lbl_download.setText(f'「{spec.name}」已安装，可直接使用。')
            self.btn_download.setText('重新下载')
        else:
            self.lbl_download.setText(
                f'「{spec.name}」需要下载 {spec.tag}，安装到 {root}'
            )
            self.btn_download.setText('下载并安装')

    def _start_download(self) -> None:
        if self._download_thread and self._download_thread.isRunning():
            self._download_thread.cancel()
            self.btn_download.setEnabled(False)
            self.lbl_download.setText('正在取消…')
            return

        spec = MODELS.get(self._selected_engine)
        if spec is None:
            return
        mirror = self.combo_mirror.currentData()
        self.store.set('app.download_mirror', mirror)

        self.progress.setVisible(True)
        self.progress.setValue(0)
        self.progress.setFormat('准备中…')
        self.btn_download.setText('取消下载')

        thread = DownloadThread(spec, self.store.model_root, mirror, self)
        thread.stage.connect(lambda text: self.lbl_download.setText(text))
        thread.progress.connect(self._on_progress)
        thread.done.connect(self._on_download_done)
        self._download_thread = thread
        thread.start()

    def _on_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self.progress.setRange(0, 0)
            return
        self.progress.setRange(0, 100)
        percent = int(done * 100 / total)
        self.progress.setValue(percent)
        self.progress.setFormat(
            f'{percent}%　{done / 1048576:.0f} / {total / 1048576:.0f} MB'
        )

    def _on_download_done(self, ok: bool, message: str) -> None:
        self.btn_download.setEnabled(True)
        self.btn_download.setText('下载并安装')
        self.progress.setVisible(False)
        self.lbl_download.setText(message)
        self._refresh_engine_states()
        self._refresh_download_hint()
        if ok:
            self.store.set('server.model_type', self._selected_engine)
            self.supervisor.restart_all()

    def _restart_all(self) -> None:
        self.store.save()
        self.supervisor.restart_all()

    # ---------- 状态 ----------

    def _connect_supervisor(self) -> None:
        self.supervisor.serverState.connect(self._on_server_state)
        self.supervisor.clientState.connect(self._on_client_state)

    def _on_server_state(self, state: str, message: str) -> None:
        if state == 'ready':
            self.chip_server.setText('模型已就绪')
            self.chip_server.set_tone('ok')
        elif state == 'starting':
            self.chip_server.setText('模型加载中')
            self.chip_server.set_tone('warn')
        else:
            self.chip_server.setText('模型未运行')
            self.chip_server.set_tone('err')
        self.status_hint.setText(message)
        self._refresh_side_status()

    def _on_client_state(self, state: str, message: str) -> None:
        if state in ('starting', 'ready'):
            self.chip_client.setText('听写已开启')
            self.chip_client.set_tone('ok')
        else:
            self.chip_client.setText('听写未运行')
            self.chip_client.set_tone('err')
        if state == 'down':
            self.status_hint.setText(message)
        self._refresh_side_status()

    def _refresh_status(self) -> None:
        spec = MODELS.get(self._selected_engine)
        if spec and is_ready(spec, self.store.model_root):
            self.chip_engine.setText(spec.name)
            self.chip_engine.set_tone('info')
        else:
            self.chip_engine.setText('未安装模型')
            self.chip_engine.set_tone('warn')

    def _refresh_side_status(self) -> None:
        server_ok = '已就绪' in self.chip_server.text()
        client_ok = '已开启' in self.chip_client.text()
        if server_ok and client_ok:
            self.side_status.setText('● 听写运行中')
        elif server_ok:
            self.side_status.setText('● 模型就绪，等待听写')
        else:
            self.side_status.setText('● 后台未就绪')

    # ---------- 窗口事件 ----------

    def closeEvent(self, event) -> None:
        if self.store.get('app.minimize_to_tray', True):
            event.ignore()
            self.hide()
        else:
            event.accept()

    def show_page(self, key: str) -> None:
        keys = [item[0] for item in NAV_ITEMS]
        if key in keys:
            index = keys.index(key)
            self.stack.setCurrentIndex(index)
            self._nav_group.button(index).setChecked(True)
