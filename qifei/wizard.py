# coding: utf-8
"""
首次配置向导

和常见输入法一样，第一次运行走一遍：欢迎 → 麦克风 → 语音模型 → 快捷键 → 完成。
每一步都可以跳过，设置项随时能在主窗口里改。
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QPoint, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (QComboBox, QDialog, QFrame, QHBoxLayout, QLabel,
                               QProgressBar, QPushButton, QStackedWidget,
                               QVBoxLayout, QWidget)

from . import autostart, brand, paths
from .model_manager import MODELS, detect_installed, is_ready, missing_files
from .store import store
from .theme import C
from .widgets import BrandMark, Card, KeyCap, LevelMeter, ToggleSwitch

STEP_TITLES = ['欢迎', '麦克风', '语音模型', '快捷键', '完成']


class MicTester(QWidget):
    """麦克风选择 + 实时电平"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._stream = None
        self._level = 0.0
        self._available = True

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.combo = QComboBox()
        layout.addWidget(self.combo)

        self.meter = LevelMeter()
        layout.addWidget(self.meter)

        self.hint = QLabel('对着麦克风说句话，下面的电平会跟着跳动。')
        self.hint.setObjectName('RowDesc')
        layout.addWidget(self.hint)

        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)
        self._timer.start()

        self._load_devices()

    # ---------- 设备 ----------

    def _load_devices(self) -> None:
        try:
            import sounddevice as sd
            devices = sd.query_devices()
        except Exception:
            self._available = False
            self.combo.addItem('未检测到可用的麦克风', -1)
            self.combo.setEnabled(False)
            self.hint.setText('系统没有找到录音设备，可先跳过，稍后在设置里重新检测。')
            return

        default_index = -1
        try:
            import sounddevice as sd
            default_index = sd.default.device[0]
        except Exception:
            pass

        self.combo.addItem('系统默认麦克风', -1)
        for index, device in enumerate(devices):
            if device.get('max_input_channels', 0) < 1:
                continue
            name = device.get('name', f'设备 {index}')
            self.combo.addItem(f'{name}', index)
            if index == default_index:
                self.combo.setCurrentIndex(self.combo.count() - 1)

        saved = int(store().get('app.mic_device', -1))
        position = self.combo.findData(saved)
        if position >= 0:
            self.combo.setCurrentIndex(position)
        self.combo.currentIndexChanged.connect(lambda _: self.restart())

    def restart(self) -> None:
        self.stop()
        if not self._available:
            return
        try:
            import sounddevice as sd
            index = self.combo.currentData()
            device = None if index in (None, -1) else int(index)
            self._stream = sd.InputStream(
                samplerate=16000, channels=1, device=device,
                blocksize=1024, callback=self._callback,
            )
            self._stream.start()
        except Exception:
            self._stream = None
            self.hint.setText('无法打开该麦克风，换一个设备试试，或检查系统麦克风权限。')

    def _callback(self, indata, frames, time_info, status) -> None:
        try:
            peak = float(abs(indata).max())
        except Exception:
            peak = 0.0
        self._level = max(self._level * 0.7, min(1.0, peak * 3.2))

    def _tick(self) -> None:
        self.meter.set_level(self._level)
        self._level *= 0.92

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None

    def selected_device(self) -> int:
        value = self.combo.currentData()
        return -1 if value in (None, -1) else int(value)


class WizardDownloadThread(QThread):
    stage = Signal(str)
    progress = Signal(int, int)
    done = Signal(bool, str)

    def __init__(self, spec, root: Path, mirror: str, parent=None):
        super().__init__(parent)
        from .model_manager import ModelDownloader
        self.spec = spec
        self.downloader = ModelDownloader(root, mirror)

    def run(self) -> None:
        ok, message = self.downloader.install(
            self.spec,
            on_stage=lambda s: self.stage.emit(s),
            on_progress=lambda d, t: self.progress.emit(d, t),
        )
        self.done.emit(ok, message)


class SetupWizard(QDialog):
    """首次运行向导"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('Root')
        self.setWindowTitle(f'{brand.BRAND_CN} · 首次设置')
        self.setWindowFlags(Qt.Dialog | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.resize(760, 560)

        self.store = store()
        self._download: Optional[WizardDownloadThread] = None
        self._drag_pos: Optional[QPoint] = None
        self._engine = self.store.get('server.model_type', 'sensevoice')

        self._build()
        self._goto(0)

    # ---------- 结构 ----------

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.shell = QFrame()
        self.shell.setObjectName('WizardShell')
        self.shell.setStyleSheet(
            f'QFrame#WizardShell {{ background: {C["surface"]};'
            f'border: 1px solid {C["border"]}; border-radius: 18px; }}'
        )
        outer.addWidget(self.shell)

        layout = QVBoxLayout(self.shell)
        layout.setContentsMargins(36, 26, 36, 24)
        layout.setSpacing(14)

        # 顶部：品牌 + 步骤
        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(BrandMark(30))
        title = QLabel(brand.BRAND_CN)
        title.setStyleSheet('font-size: 15px; font-weight: 600;')
        top.addWidget(title)
        top.addStretch(1)

        self.step_labels: list[QLabel] = []
        for index, name in enumerate(STEP_TITLES):
            dot = QLabel(str(index + 1))
            dot.setObjectName('StepDot')
            dot.setFixedSize(22, 22)
            dot.setAlignment(Qt.AlignCenter)
            self.step_labels.append(dot)
            top.addWidget(dot)
            if index < len(STEP_TITLES) - 1:
                line = QFrame()
                line.setFixedSize(18, 1)
                line.setStyleSheet(f'background: {C["border"]};')
                top.addWidget(line)
        layout.addLayout(top)

        # 内容
        self.stack = QStackedWidget()
        self.stack.addWidget(self._page_welcome())
        self.stack.addWidget(self._page_mic())
        self.stack.addWidget(self._page_model())
        self.stack.addWidget(self._page_hotkey())
        self.stack.addWidget(self._page_done())
        layout.addWidget(self.stack, 1)

        # 底部按钮
        bottom = QHBoxLayout()
        bottom.setSpacing(8)
        self.btn_skip = QPushButton('跳过')
        self.btn_skip.setObjectName('Link')
        self.btn_skip.clicked.connect(self._skip)
        bottom.addWidget(self.btn_skip)
        bottom.addStretch(1)
        self.btn_back = QPushButton('上一步')
        self.btn_back.setObjectName('Ghost')
        self.btn_back.clicked.connect(lambda: self._goto(self.stack.currentIndex() - 1))
        bottom.addWidget(self.btn_back)
        self.btn_next = QPushButton('下一步')
        self.btn_next.setObjectName('Primary')
        self.btn_next.clicked.connect(self._next)
        bottom.addWidget(self.btn_next)
        layout.addLayout(bottom)

    def _page(self, title: str, subtitle: str) -> tuple[QWidget, QVBoxLayout]:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(12)
        h1 = QLabel(title)
        h1.setStyleSheet('font-size: 19px; font-weight: 600;')
        layout.addWidget(h1)
        sub = QLabel(subtitle)
        sub.setObjectName('Sub')
        sub.setWordWrap(True)
        layout.addWidget(sub)
        return page, layout

    # ---------- 各步内容 ----------

    def _page_welcome(self) -> QWidget:
        page, layout = self._page(
            f'欢迎使用 {brand.BRAND_CN}',
            '三分钟完成配置，之后在任何输入框里按住空格就能说话上屏。'
        )
        layout.addStretch(1)
        card = Card()
        for chip_text, title, desc in [
            ('离线', '完全离线', '语音识别在本机完成，不联网、不上传音频'),
            ('随处', '随处可用', '微信、Word、浏览器、代码编辑器，光标在哪就输到哪'),
            ('两种', '两种节奏', 'Tab 按一下开始、再按一下结束；空格按住说话、松开即上屏'),
        ]:
            row = QHBoxLayout()
            row.setSpacing(12)
            chip = KeyCap(chip_text, 'primary')
            chip.setFixedWidth(48)
            row.addWidget(chip)
            text = QLabel(f'{title}　<span style="color:{C["text_muted"]}">{desc}</span>')
            text.setTextFormat(Qt.RichText)
            text.setWordWrap(True)
            row.addWidget(text, 1)
            card.body().addLayout(row)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _page_mic(self) -> QWidget:
        page, layout = self._page('选择麦克风', '选一个能听清你说话的设备，看到电平跳动就说明正常。')
        card = Card()
        self.mic = MicTester()
        card.body().addWidget(self.mic)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    def _page_model(self) -> QWidget:
        page, layout = self._page(
            '下载语音模型',
            '模型体积较大，建议在网络空闲时下载。已装过模型可以直接指定目录。'
        )
        card = Card()
        self.combo_engine = QComboBox()
        for key, spec in MODELS.items():
            self.combo_engine.addItem(f'{spec.name}　{spec.tag}', key)
        index = self.combo_engine.findData(self._engine)
        self.combo_engine.setCurrentIndex(max(0, index))
        self.combo_engine.currentIndexChanged.connect(self._on_engine_changed)
        card.add(self.combo_engine)

        self.lbl_model = QLabel('')
        self.lbl_model.setObjectName('RowDesc')
        self.lbl_model.setWordWrap(True)
        card.body().addWidget(self.lbl_model)

        self.progress = QProgressBar()
        self.progress.setVisible(False)
        card.body().addWidget(self.progress)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_download = QPushButton('开始下载')
        self.btn_download.setObjectName('Primary')
        self.btn_download.clicked.connect(self._toggle_download)
        row.addWidget(self.btn_download)
        btn_dir = QPushButton('已有模型，选目录')
        btn_dir.setObjectName('Ghost')
        btn_dir.clicked.connect(self._choose_dir)
        row.addWidget(btn_dir)
        row.addStretch(1)
        card.body().addLayout(row)
        layout.addWidget(card)

        self.lbl_root = QLabel('')
        self.lbl_root.setObjectName('Mono')
        self.lbl_root.setWordWrap(True)
        layout.addWidget(self.lbl_root)
        layout.addStretch(1)
        self._refresh_model_hint()
        return page

    def _page_hotkey(self) -> QWidget:
        page, layout = self._page('确认快捷键', '默认已经调好，保留默认即可。')
        card = Card()
        self.sw_tab = ToggleSwitch(checked=self.store.shortcut('tab').get('enabled', True))
        self.sw_space = ToggleSwitch(checked=self.store.shortcut('space').get('enabled', True))
        self.sw_x2 = ToggleSwitch(checked=self.store.shortcut('x2').get('enabled', True))

        for keys, title, desc, switch in [
            (['Tab'], '按一下开始录音，再按一下结束', '适合长段口述', self.sw_tab),
            (['Space'], '按住说话，松开结束', '轻点仍然是普通空格', self.sw_space),
            (['X2'], '鼠标侧键按住说话', '右手不离鼠标时使用', self.sw_x2),
        ]:
            row = QHBoxLayout()
            row.setSpacing(8)
            for key in keys:
                row.addWidget(KeyCap(key, 'primary'))
            text = QLabel(f'{title}　<span style="color:{C["text_muted"]}">{desc}</span>')
            text.setTextFormat(Qt.RichText)
            row.addWidget(text, 1)
            row.addWidget(switch)
            card.body().addLayout(row)
        layout.addWidget(card)

        tip = QLabel('录音过程中按一下空格可以暂停，再按一下继续。')
        tip.setObjectName('RowDesc')
        layout.addWidget(tip)
        layout.addStretch(1)
        return page

    def _page_done(self) -> QWidget:
        page, layout = self._page('一切就绪', '点击完成后，启飞输入法会在后台常驻。')
        card = Card()
        self.sw_autostart = ToggleSwitch(checked=True)
        row = QHBoxLayout()
        row.addWidget(QLabel('开机自动启动'))
        row.addStretch(1)
        row.addWidget(self.sw_autostart)
        card.body().addLayout(row)

        note = QLabel(
            '完成后可以在托盘图标右键打开设置；\n'
            '如果发现快捷键没反应，请确认本程序已获得麦克风权限。'
        )
        note.setObjectName('RowDesc')
        note.setWordWrap(True)
        card.body().addWidget(note)
        layout.addWidget(card)
        layout.addStretch(1)
        return page

    # ---------- 流程 ----------

    def _goto(self, index: int) -> None:
        index = max(0, min(len(STEP_TITLES) - 1, index))
        if self.stack.currentIndex() == 1 and index != 1:
            self.mic.stop()
        self.stack.setCurrentIndex(index)

        for position, label in enumerate(self.step_labels):
            if position < index:
                label.setObjectName('StepDotDone')
            elif position == index:
                label.setObjectName('StepDotActive')
            else:
                label.setObjectName('StepDot')
            label.setText(str(position + 1))
            # objectName 变了必须重新 polish，否则 QSS 选择器不会生效
            label.style().unpolish(label)
            label.style().polish(label)
            label.update()

        if index == 1:
            self.mic.restart()
        self.btn_back.setEnabled(index > 0)
        self.btn_skip.setVisible(index < len(STEP_TITLES) - 1)
        self.btn_next.setText('完成' if index == len(STEP_TITLES) - 1 else '下一步')

    def _persist_mic(self) -> None:
        """把选中的麦克风同时写进界面配置和客户端配置

        config_client 的 audio_device 才是真正控制采集的设备；
        界面里的 app.mic_device 只用于下次打开向导时回显。
        """
        index = self.mic.selected_device()
        self.store.set('app.mic_device', index, save=False)
        self.store.set('client.audio_device', None if index < 0 else index,
                       save=False)

    def _next(self) -> None:
        index = self.stack.currentIndex()
        if index == 1:
            self._persist_mic()
        if index == 2 and self._download and self._download.isRunning():
            self._download.requestInterruption()
        if index == len(STEP_TITLES) - 1:
            self._finish()
            return
        self._goto(index + 1)

    def _skip(self) -> None:
        index = self.stack.currentIndex()
        if index == len(STEP_TITLES) - 2:
            self._finish()
        else:
            self._goto(index + 1)

    def _finish(self) -> None:
        self._persist_mic()
        self.store.set('app.autostart', self.sw_autostart.isChecked(), save=False)
        self.store.set('app.first_run_done', True, save=False)
        self.store.set('app.show_indicator', True, save=False)
        for key, switch in (('tab', self.sw_tab), ('space', self.sw_space), ('x2', self.sw_x2)):
            for item in self.store.get('client.shortcuts', []):
                if item.get('key') == key:
                    item['enabled'] = switch.isChecked()
        self.store.save()
        if self.sw_autostart.isChecked():
            autostart.set_enabled(True)
        if self.mic:
            self.mic.stop()
        self.accept()

    # ---------- 模型 ----------

    def _on_engine_changed(self, _index: int) -> None:
        self._engine = self.combo_engine.currentData()
        self._refresh_model_hint()

    def _refresh_model_hint(self) -> None:
        spec = MODELS.get(self._engine)
        root = self.store.model_root
        self.lbl_root.setText(f'模型目录：{root}')
        if spec and is_ready(spec, root):
            self.lbl_model.setText(f'「{spec.name}」已经可用，无需再次下载。')
            self.btn_download.setText('已安装')
            self.btn_download.setEnabled(False)
        else:
            missing = missing_files(spec, root) if spec else []
            self.lbl_model.setText(
                f'{spec.desc}\n需要下载 {spec.tag}，共 {len(missing)} 个模型文件。'
            )
            self.btn_download.setText('开始下载')
            self.btn_download.setEnabled(True)

    def _choose_dir(self) -> None:
        from PySide6.QtWidgets import QFileDialog
        path = QFileDialog.getExistingDirectory(self, '选择模型目录', str(self.store.model_root))
        if not path:
            return
        self.store.set_model_root(path)
        self._refresh_model_hint()

    def _toggle_download(self) -> None:
        if self._download and self._download.isRunning():
            self._download.requestInterruption()
            self.btn_download.setEnabled(False)
            self.lbl_model.setText('正在取消…')
            return

        spec = MODELS.get(self._engine)
        if spec is None:
            return
        self.progress.setVisible(True)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setFormat('准备中…')
        self.btn_download.setText('取消下载')

        thread = WizardDownloadThread(spec, self.store.model_root,
                                      self.store.get('app.download_mirror', 'github'), self)
        thread.stage.connect(lambda text: self.lbl_model.setText(text))
        thread.progress.connect(self._on_progress)
        thread.done.connect(self._on_download_done)
        self._download = thread
        thread.start()

    def _on_progress(self, done: int, total: int) -> None:
        if total <= 0:
            self.progress.setRange(0, 0)
            return
        self.progress.setRange(0, 100)
        percent = int(done * 100 / total)
        self.progress.setValue(percent)
        self.progress.setFormat(f'{percent}%　{done / 1048576:.0f} / {total / 1048576:.0f} MB')

    def _on_download_done(self, ok: bool, message: str) -> None:
        self.progress.setVisible(False)
        self.btn_download.setEnabled(True)
        self.lbl_model.setText(message)
        if ok:
            self.store.set('server.model_type', self._engine)
        self._refresh_model_hint()

    # ---------- 无边框拖动 ----------

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and event.position().y() < 70:
            self._drag_pos = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_pos is not None and event.buttons() & Qt.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_pos)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_pos = None
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 0))
        painter.drawPath(QPainterPath())
