# coding: utf-8
"""
界面预览工具（开发用）

在无显示器环境下把界面渲染成 PNG，方便检查排版与配色：

    .venv\\Scripts\\python.exe tools\\ui_preview.py

输出到 .tmp/preview/*.png
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / '.tmp' / 'preview'
OUT.mkdir(parents=True, exist_ok=True)

# 用临时配置，避免污染真实设置
os.environ.setdefault('QIFEI_CONFIG', str(ROOT / '.tmp' / 'preview-config.json'))

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from PySide6.QtCore import QSize, Qt  # noqa: E402
from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from qifei.app import make_icon  # noqa: E402
from qifei.main_window import MainWindow  # noqa: E402
from qifei.store import store  # noqa: E402
from qifei.supervisor import Supervisor  # noqa: E402
from qifei.theme import apply_theme  # noqa: E402
from qifei.wizard import SetupWizard  # noqa: E402


def load_system_font() -> None:
    """offscreen 平台没有字体库，手动加载系统中文字体，预览才不是方块"""
    for name in ('msyh.ttc', 'msyhbd.ttc', 'simhei.ttf', 'simsun.ttc'):
        path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name
        if path.exists():
            font_id = QFontDatabase.addApplicationFont(str(path))
            if font_id >= 0:
                families = QFontDatabase.applicationFontFamilies(font_id)
                if families:
                    print('加载字体', name, '->', families[0])
                    return


def save(widget, name: str) -> None:
    widget.resize(widget.size() if widget.size().isValid() else QSize(940, 660))
    widget.show()
    QApplication.processEvents()
    pixmap = widget.grab()
    path = OUT / f'{name}.png'
    pixmap.save(str(path))
    print('saved', path, pixmap.size())


def main() -> None:
    app = QApplication(sys.argv)
    app.setWindowIcon(make_icon())
    load_system_font()
    apply_theme(app)
    if QFontDatabase.families():
        families = QFontDatabase.families()
        for name in ('Microsoft YaHei UI', 'Microsoft YaHei', 'SimHei'):
            if name in families:
                font = QFont(name)
                font.setPixelSize(13)
                app.setFont(font)
                break

    data = store()
    data.set('app.first_run_done', True, save=True)

    supervisor = Supervisor()
    window = MainWindow(supervisor, None)
    window.resize(940, 660)
    for page in ('overview', 'hotkey', 'model', 'general', 'about'):
        window.show_page(page)
        QApplication.processEvents()
        save(window, f'main-{page}')

    wizard = SetupWizard()
    for index in range(5):
        wizard._goto(index)
        QApplication.processEvents()
        save(wizard, f'wizard-{index}')

    from qifei.indicator import Indicator
    indicator = Indicator()
    for state in ('recording', 'paused', 'recognizing', 'done'):
        indicator.show_state(state, '今天下午三点开产品评审会')
        indicator.setWindowOpacity(1.0)
        QApplication.processEvents()
        save(indicator, f'indicator-{state}')

    print('预览完成')


if __name__ == '__main__':
    main()
