# coding: utf-8
"""
悬浮状态条

屏幕上唯一常驻的视觉反馈：录音时在底部中间浮出一个小胶囊，
显示声波与状态文字，不抢焦点、不挡鼠标。
"""

from __future__ import annotations

from PySide6.QtCore import (QEasingCurve, QPoint, QPropertyAnimation, QRectF,
                            Qt, QTimer, Signal)
from PySide6.QtGui import QColor, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from .theme import C
from .widgets import Waveform

STATE_STYLE = {
    'recording': ('正在聆听…', '#5B8CFF', True),
    'paused': ('已暂停', '#F79009', False),
    'recognizing': ('正在识别…', '#7BA2FF', False),
    'done': ('已上屏', '#17B26A', False),
}


class Indicator(QWidget):
    """底部居中的录音状态胶囊"""

    clicked = Signal()

    def __init__(self, parent=None):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
                         | Qt.Tool | Qt.WindowTransparentForInput
                         | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(268, 56)

        self._tone = QColor('#5B8CFF')

        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 0, 18, 0)
        layout.setSpacing(12)

        self.wave = Waveform(self, bars=14, tint='#7BA2FF', height=24)
        self.wave.setFixedWidth(72)
        layout.addWidget(self.wave)

        text_box = QVBoxLayout()
        text_box.setContentsMargins(0, 0, 0, 0)
        text_box.setSpacing(1)
        self.title = QLabel('正在聆听…', self)
        self.title.setStyleSheet('color: #FFFFFF; font-size: 13px; font-weight: 600;')
        self.subtitle = QLabel('', self)
        self.subtitle.setStyleSheet('color: rgba(255,255,255,0.62); font-size: 11px;')
        text_box.addWidget(self.title)
        text_box.addWidget(self.subtitle)
        layout.addLayout(text_box, 1)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.fade_out)

        self._fade = QPropertyAnimation(self, b'windowOpacity', self)
        self._fade.setDuration(160)
        self._fade.setEasingCurve(QEasingCurve.OutCubic)

    # ---------- 展示 ----------

    def show_state(self, state: str, text: str = '') -> None:
        if state == 'idle':
            self.fade_out()
            return

        title, tone, active = STATE_STYLE.get(state, ('', '#5B8CFF', False))
        self._tone = QColor(tone)
        self.title.setText(title)
        self.wave.set_active(active)
        self.wave._tint = self._tone

        if state == 'done':
            snippet = (text or '').strip().replace('\n', ' ')
            if len(snippet) > 12:
                snippet = snippet[:12] + '…'
            self.subtitle.setText(snippet or '已发送到光标处')
        elif state == 'paused':
            self.subtitle.setText('再按空格继续')
        elif state == 'recording':
            self.subtitle.setText('Tab 结束 · 空格暂停')
        else:
            self.subtitle.setText('')

        self._hide_timer.stop()
        self.fade_in()
        self.update()

        if state == 'done':
            self._hide_timer.start(1400)
        elif state == 'recognizing':
            self._hide_timer.start(6000)

    def fade_in(self) -> None:
        self._reposition()
        if not self.isVisible():
            self.setWindowOpacity(0.0)
            self.show()
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(1.0)
        self._fade.start()

    def fade_out(self) -> None:
        if not self.isVisible():
            return
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.finished.connect(self._after_fade)
        self._fade.start()

    def _after_fade(self) -> None:
        try:
            self._fade.finished.disconnect(self._after_fade)
        except (RuntimeError, TypeError):
            pass
        if self.windowOpacity() <= 0.02:
            self.hide()

    # ---------- 位置与绘制 ----------

    def _reposition(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return
        area = screen.availableGeometry()
        x = area.center().x() - self.width() // 2
        y = area.bottom() - self.height() - 96
        self.move(QPoint(x, y))

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = QRectF(0, 0, self.width(), self.height())
        path = QPainterPath()
        path.addRoundedRect(rect, 28, 28)

        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(18, 22, 31, 242))
        painter.drawPath(path)
