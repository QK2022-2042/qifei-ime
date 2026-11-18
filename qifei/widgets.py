# coding: utf-8
"""通用控件：开关、卡片、音量条、声波、键帽、设置行"""

from __future__ import annotations

import math
import random
from typing import Optional

from PySide6.QtCore import (Property, QEasingCurve, QPropertyAnimation, QRectF,
                            QSize, Qt, QTimer, Signal)
from PySide6.QtGui import (QBrush, QColor, QFont, QPainter, QPainterPath, QPen,
                           QRadialGradient)
from PySide6.QtWidgets import (QAbstractButton, QFrame, QHBoxLayout, QLabel,
                               QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from .theme import C, color


# ────────────────────────────────────────────────────────────
# 页面骨架
# ────────────────────────────────────────────────────────────
class Page(QScrollArea):
    """带标题的滚动页面"""

    def __init__(self, title: str, subtitle: str = '', parent=None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        container = QWidget()
        self.body = QVBoxLayout(container)
        self.body.setContentsMargins(32, 26, 32, 32)
        self.body.setSpacing(16)

        header = QVBoxLayout()
        header.setSpacing(4)
        h1 = QLabel(title)
        h1.setObjectName('H1')
        header.addWidget(h1)
        if subtitle:
            sub = QLabel(subtitle)
            sub.setObjectName('Sub')
            sub.setWordWrap(True)
            header.addWidget(sub)
        self.body.addLayout(header)
        self.body.addSpacing(2)

        self.setWidget(container)

    def add(self, widget: QWidget) -> QWidget:
        self.body.addWidget(widget)
        return widget

    def add_stretch(self) -> None:
        self.body.addStretch(1)


class StatusChip(QLabel):
    """带圆点的状态标签"""

    def __init__(self, text: str = '', tone: str = 'muted', parent=None):
        super().__init__(text, parent)
        self.set_tone(tone)

    def set_tone(self, tone: str) -> None:
        mapping = {
            'ok': (C['success_soft'], C['success']),
            'warn': (C['warn_soft'], C['warn']),
            'err': (C['danger_soft'], C['danger']),
            'info': (C['primary_soft'], C['primary']),
            'muted': (C['surface_alt'], C['text_muted']),
        }
        bg, fg = mapping.get(tone, mapping['muted'])
        self.setStyleSheet(
            f'background: {bg}; color: {fg}; border-radius: 9px;'
            f'padding: 4px 10px; font-size: 11px; font-weight: 600;'
        )


# ────────────────────────────────────────────────────────────
# 开关
# ────────────────────────────────────────────────────────────
class ToggleSwitch(QAbstractButton):
    """iOS 风格的开关，带滑动动画"""

    def __init__(self, parent: Optional[QWidget] = None, checked: bool = False):
        super().__init__(parent)
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.PointingHandCursor)
        self.setFixedSize(46, 26)
        self._pos = 1.0 if checked else 0.0
        self._anim = QPropertyAnimation(self, b'knob', self)
        self._anim.setDuration(160)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)
        self.toggled.connect(self._on_toggled)

    def _on_toggled(self, checked: bool) -> None:
        self._anim.stop()
        self._anim.setStartValue(self._pos)
        self._anim.setEndValue(1.0 if checked else 0.0)
        self._anim.start()

    def get_knob(self) -> float:
        return self._pos

    def set_knob(self, value: float) -> None:
        self._pos = value
        self.update()

    knob = Property(float, get_knob, set_knob)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        radius = h / 2

        off = color('#D6DCE8')
        on = color(C['primary'])
        track = QColor(
            int(off.red() + (on.red() - off.red()) * self._pos),
            int(off.green() + (on.green() - off.green()) * self._pos),
            int(off.blue() + (on.blue() - off.blue()) * self._pos),
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(track)
        painter.drawRoundedRect(QRectF(0, 0, w, h), radius, radius)

        knob_size = h - 6
        x = 3 + self._pos * (w - knob_size - 6)
        painter.setBrush(QColor('#FFFFFF'))
        painter.drawEllipse(QRectF(x, 3, knob_size, knob_size))


# ────────────────────────────────────────────────────────────
# 卡片 / 行
# ────────────────────────────────────────────────────────────
class Card(QFrame):
    """圆角卡片容器"""

    def __init__(self, parent: Optional[QWidget] = None, spacing: int = 14,
                 margins: tuple = (20, 18, 20, 18)):
        super().__init__(parent)
        self.setObjectName('Card')
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(*margins)
        self._layout.setSpacing(spacing)

    def body(self) -> QVBoxLayout:
        return self._layout

    def add(self, widget: QWidget) -> QWidget:
        self._layout.addWidget(widget)
        return widget

    def add_title(self, text: str, desc: str = '') -> None:
        title = QLabel(text)
        title.setObjectName('CardTitle')
        self._layout.addWidget(title)
        if desc:
            sub = QLabel(desc)
            sub.setObjectName('RowDesc')
            sub.setWordWrap(True)
            self._layout.addWidget(sub)


class SettingRow(QWidget):
    """左侧标题说明，右侧控件的标准行"""

    def __init__(self, title: str, desc: str = '', control: Optional[QWidget] = None,
                 parent: Optional[QWidget] = None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 4)
        layout.setSpacing(16)

        text_box = QVBoxLayout()
        text_box.setSpacing(3)
        title_label = QLabel(title)
        title_label.setObjectName('RowTitle')
        text_box.addWidget(title_label)
        if desc:
            desc_label = QLabel(desc)
            desc_label.setObjectName('RowDesc')
            desc_label.setWordWrap(True)
            text_box.addWidget(desc_label)

        layout.addLayout(text_box, 1)
        if control is not None:
            control.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
            layout.addWidget(control, 0, Qt.AlignVCenter)


class Divider(QFrame):
    """1px 分隔线"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setFixedHeight(1)
        self.setStyleSheet(f'background: {C["border"]};')


class KeyCap(QLabel):
    """键帽样式的小标签"""

    def __init__(self, text: str, tone: str = 'normal', parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        bg = {'normal': C['surface_alt'], 'primary': C['primary_soft'],
              'success': C['success_soft'], 'warn': C['warn_soft']}.get(tone, C['surface_alt'])
        fg = {'normal': C['text_sub'], 'primary': C['primary'],
              'success': C['success'], 'warn': C['warn']}.get(tone, C['text_sub'])
        self.setStyleSheet(
            f'background: {bg}; color: {fg}; border-radius: 7px;'
            f'padding: 5px 10px; font-size: 12px; font-weight: 600;'
        )
        self.setAlignment(Qt.AlignCenter)


# ────────────────────────────────────────────────────────────
# 声波
# ────────────────────────────────────────────────────────────
class Waveform(QWidget):
    """录音声波：录音时跳动，暂停时静止为一条细线"""

    def __init__(self, parent: Optional[QWidget] = None, bars: int = 18,
                 tint: str = '#5B8CFF', height: int = 26):
        super().__init__(parent)
        self._bars = bars
        self._tint = QColor(tint)
        self._phase = 0.0
        self._active = False
        self._level = 0.5
        self.setFixedHeight(height)
        self.setMinimumWidth(90)
        self._timer = QTimer(self)
        self._timer.setInterval(60)
        self._timer.timeout.connect(self._tick)

    def set_active(self, active: bool) -> None:
        self._active = active
        if active and not self._timer.isActive():
            self._timer.start()
        elif not active:
            self._timer.stop()
            self._phase = 0.0
            self.update()

    def set_level(self, level: float) -> None:
        self._level = max(0.05, min(1.0, level))

    def _tick(self) -> None:
        self._phase += 0.42
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setPen(Qt.NoPen)

        w, h = self.width(), self.height()
        mid = h / 2

        # 未在录音时画一条平静的线，表示「没有在采集」
        if not self._active:
            col = QColor(self._tint)
            col.setAlpha(110)
            painter.setBrush(col)
            line_w = w * 0.62
            painter.drawRoundedRect(QRectF((w - line_w) / 2, mid - 1.5, line_w, 3), 1.5, 1.5)
            return

        gap = 3.0
        bar_w = max(2.5, (w - gap * (self._bars - 1)) / self._bars)

        for i in range(self._bars):
            x = i * (bar_w + gap)
            wave = math.sin(self._phase + i * 0.55) * 0.5 + 0.5
            wave = 0.25 + wave * 0.75
            amplitude = wave * (0.35 + self._level * 0.65)
            bar_h = max(2.5, h * amplitude)
            col = QColor(self._tint)
            col.setAlpha(235)
            painter.setBrush(col)
            painter.drawRoundedRect(
                QRectF(x, mid - bar_h / 2, bar_w, bar_h), bar_w / 2, bar_w / 2
            )


class LevelMeter(QWidget):
    """麦克风电平条"""

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._level = 0.0
        self._peak = 0.0
        self.setFixedHeight(10)
        self.setMinimumWidth(180)

    def set_level(self, level: float) -> None:
        self._level = max(0.0, min(1.0, level))
        self._peak = max(self._peak * 0.94, self._level)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()

        painter.setPen(Qt.NoPen)
        painter.setBrush(color(C['surface_alt']))
        painter.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)

        grad = QRadialGradient(0, 0, w)
        grad.setColorAt(0.0, color(C['success']))
        grad.setColorAt(0.65, color(C['primary']))
        grad.setColorAt(1.0, color(C['warn']))
        painter.setBrush(QBrush(grad))
        if self._level > 0.005:
            painter.drawRoundedRect(QRectF(0, 0, max(h, w * self._level), h), h / 2, h / 2)

        if self._peak > 0.01:
            painter.setBrush(color(C['text_muted']))
            painter.drawRoundedRect(
                QRectF(min(w - 2, w * self._peak), 0, 2, h), 1, 1
            )


# ────────────────────────────────────────────────────────────
# 品牌标识
# ────────────────────────────────────────────────────────────
class BrandMark(QWidget):
    """圆形渐变 Logo + 声波"""

    def __init__(self, size: int = 40, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        s = self._size
        grad = QRadialGradient(s * 0.3, s * 0.25, s)
        grad.setColorAt(0.0, QColor('#7BA2FF'))
        grad.setColorAt(0.55, QColor('#3D6BFF'))
        grad.setColorAt(1.0, QColor('#2A45B8'))
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(grad))
        painter.drawRoundedRect(QRectF(0, 0, s, s), s * 0.32, s * 0.32)

        painter.setPen(QPen(QColor(255, 255, 255, 235), max(1.6, s * 0.055),
                            Qt.SolidLine, Qt.RoundCap))
        heights = [0.18, 0.34, 0.5, 0.34, 0.18]
        for i, factor in enumerate(heights):
            x = s * (0.24 + i * 0.13)
            half = s * factor * 0.5
            painter.drawLine(int(x), int(s / 2 - half), int(x), int(s / 2 + half))
