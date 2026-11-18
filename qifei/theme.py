# coding: utf-8
"""
视觉设计系统

浅色、干净、克制：大面积留白 + 卡片分层 + 单一主色（信任蓝），
动效靠 Qt 属性动画，不引入额外依赖。
"""

from __future__ import annotations

from string import Template

from PySide6.QtGui import QColor, QFont, QFontDatabase

# 色板
C = {
    'bg': '#F5F7FB',
    'surface': '#FFFFFF',
    'surface_alt': '#F0F3F9',
    'border': '#E6EAF2',
    'border_strong': '#D6DCE8',
    'text': '#141A24',
    'text_sub': '#66707F',
    'text_muted': '#98A1B0',
    'primary': '#3D6BFF',
    'primary_hover': '#2F5AE0',
    'primary_soft': '#EAF0FF',
    'success': '#17B26A',
    'success_soft': '#E7F8F0',
    'warn': '#F79009',
    'warn_soft': '#FEF4E6',
    'danger': '#F04438',
    'danger_soft': '#FDECEA',
    'dark': '#12161F',
}

FONT_STACK = '"Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI", sans-serif'

_QSS = Template(
    """
* { outline: none; }

QWidget {
    font-family: $font;
    font-size: 13px;
    color: $text;
}

QWidget#Root, QDialog#Root {
    background: $bg;
}

/* ---------- 侧边栏 ---------- */
QFrame#Sidebar {
    background: $surface;
    border-right: 1px solid $border;
}
QLabel#BrandName {
    font-size: 16px;
    font-weight: 600;
    color: $text;
}
QLabel#BrandTag {
    font-size: 11px;
    color: $text_muted;
}
QPushButton#NavButton {
    text-align: left;
    padding: 10px 14px;
    border: none;
    border-radius: 10px;
    background: transparent;
    color: $text_sub;
    font-size: 13px;
}
QPushButton#NavButton:hover {
    background: $surface_alt;
    color: $text;
}
QPushButton#NavButton:checked {
    background: $primary_soft;
    color: $primary;
    font-weight: 600;
}

/* ---------- 卡片 ---------- */
QFrame#Card {
    background: $surface;
    border: 1px solid $border;
    border-radius: 14px;
}
QLabel#H1 { font-size: 20px; font-weight: 600; color: $text; }
QLabel#Sub { font-size: 12px; color: $text_sub; }
QLabel#CardTitle { font-size: 14px; font-weight: 600; color: $text; }
QLabel#RowTitle { font-size: 13px; color: $text; }
QLabel#RowDesc { font-size: 12px; color: $text_muted; }
QLabel#Mono { font-family: Consolas, monospace; font-size: 12px; color: $text_sub; }
QLabel#Ok { color: $success; font-size: 12px; }
QLabel#Warn { color: $warn; font-size: 12px; }
QLabel#Err { color: $danger; font-size: 12px; }

/* ---------- 按钮 ---------- */
QPushButton {
    font-family: $font;
    font-size: 13px;
    padding: 9px 18px;
    border-radius: 9px;
    border: 1px solid $border_strong;
    background: $surface;
    color: $text;
}
QPushButton:hover { background: $surface_alt; }
QPushButton:pressed { background: $border; }
QPushButton:disabled { color: $text_muted; background: $surface_alt; border-color: $border; }

QPushButton#Primary {
    background: $primary;
    color: #FFFFFF;
    border: none;
    font-weight: 600;
}
QPushButton#Primary:hover { background: $primary_hover; }
QPushButton#Primary:disabled { background: #B9C6E8; color: #F0F3F9; }

QPushButton#Ghost {
    background: transparent;
    border: 1px solid $border_strong;
    color: $text_sub;
}
QPushButton#Ghost:hover { background: $surface_alt; color: $text; }

QPushButton#Link {
    background: transparent;
    border: none;
    color: $primary;
    padding: 4px 6px;
}
QPushButton#Link:hover { color: $primary_hover; text-decoration: underline; }

/* ---------- 输入 ---------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: $surface;
    border: 1px solid $border_strong;
    border-radius: 9px;
    padding: 8px 10px;
    selection-background-color: $primary_soft;
    selection-color: $text;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: $primary;
}
QComboBox::drop-down { border: none; width: 24px; }
QComboBox QAbstractItemView {
    background: $surface;
    border: 1px solid $border;
    border-radius: 8px;
    selection-background-color: $primary_soft;
    selection-color: $primary;
    padding: 4px;
}

QProgressBar {
    background: $surface_alt;
    border: none;
    border-radius: 6px;
    height: 12px;
    text-align: center;
    font-size: 11px;
    color: $text_sub;
}
QProgressBar::chunk {
    background: $primary;
    border-radius: 6px;
}

QScrollArea, QScrollArea > QWidget > QWidget { background: transparent; border: none; }
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 2px;
}
QScrollBar::handle:vertical {
    background: $border_strong;
    border-radius: 5px;
    min-height: 32px;
}
QScrollBar::handle:vertical:hover { background: $text_muted; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QToolTip {
    background: $dark;
    color: #FFFFFF;
    border: none;
    border-radius: 6px;
    padding: 6px 9px;
    font-size: 12px;
}

QMenu {
    background: $surface;
    border: 1px solid $border;
    border-radius: 10px;
    padding: 6px;
}
QMenu::item {
    padding: 8px 22px 8px 14px;
    border-radius: 7px;
}
QMenu::item:selected { background: $primary_soft; color: $primary; }
QMenu::separator { height: 1px; background: $border; margin: 6px 8px; }

/* ---------- 步骤条 ---------- */
QLabel#StepDot {
    border-radius: 11px;
    background: $surface_alt;
    color: $text_muted;
    font-size: 11px;
    font-weight: 600;
}
QLabel#StepDotActive { border-radius: 11px; background: $primary; color: #FFFFFF; font-size: 11px; font-weight: 600; }
QLabel#StepDotDone { border-radius: 11px; background: $success_soft; color: $success; font-size: 11px; font-weight: 600; }
"""
)


def stylesheet() -> str:
    """生成 QSS"""
    return _QSS.substitute(font=FONT_STACK, **C)


def apply_theme(app) -> None:
    """给 QApplication 应用主题"""
    font = QFont()
    families = QFontDatabase.families()
    for name in ('Microsoft YaHei UI', 'Microsoft YaHei', 'Segoe UI'):
        if name in families:
            font.setFamily(name)
            break
    font.setPixelSize(13)
    app.setFont(font)
    app.setStyleSheet(stylesheet())


def color(name: str) -> QColor:
    """支持色板键名（'primary'）与十六进制（'#3D6BFF'）两种写法"""
    return QColor(C.get(name, name))
