# coding: utf-8
"""应用入口：单实例、托盘、悬浮条、后台进程守护"""

from __future__ import annotations

import sys
import time

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QIcon, QLinearGradient, QPainter, QPixmap
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QMenu, QMessageBox, QSystemTrayIcon)

from . import brand, paths
from .indicator import Indicator
from .ipc import StatusListener
from .main_window import MainWindow
from .store import store
from .supervisor import Supervisor
from .theme import apply_theme
from .wizard import SetupWizard

SERVER_NAME = f'{brand.APP_ID}-single'


def make_icon() -> QIcon:
    """应用图标：优先用 assets/icon.ico；缺失时现场画同款，视觉不脱节。

    同款设计见 tools/make_icon.py：品牌蓝渐变圆角方形 + 白色声波
    （说出来）+ 底部输入基线与光标（落成文字）。
    """
    icon_file = paths.icon_path()
    if icon_file:
        icon = QIcon(icon_file)
        if not icon.isNull():
            return icon

    n = 128.0
    pixmap = QPixmap(int(n), int(n))
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)

    gradient = QLinearGradient(QPointF(0, 0), QPointF(n, n))
    gradient.setColorAt(0.0, QColor('#7BA2FF'))
    gradient.setColorAt(0.5, QColor('#3D6BFF'))
    gradient.setColorAt(1.0, QColor('#2A45B8'))
    painter.setPen(Qt.NoPen)
    painter.setBrush(gradient)
    painter.drawRoundedRect(QRectF(0, 0, n, n), n * 0.225, n * 0.225)

    painter.setBrush(QColor(255, 255, 255, 255))

    def bar(cx: float, cy: float, width: float, half: float) -> None:
        painter.drawRoundedRect(
            QRectF(cx - width / 2, cy - half, width, half * 2),
            width / 2, width / 2)

    for x, ratio in zip((0.24, 0.37, 0.50, 0.63, 0.76),
                        (0.34, 0.62, 1.00, 0.62, 0.34)):
        bar(x * n, 0.42 * n, 0.075 * n, 0.25 * n * ratio)

    thick = 0.062 * n
    painter.drawRoundedRect(
        QRectF(0.22 * n, 0.79 * n - thick / 2, 0.46 * n, thick),
        thick / 2, thick / 2)
    cw = 0.045 * n
    painter.drawRoundedRect(
        QRectF(0.73 * n - cw / 2, 0.71 * n, cw, 0.16 * n),
        cw / 2, cw / 2)
    painter.end()
    return QIcon(pixmap)


class SingleInstance:
    """用本地套接字保证只运行一个实例"""

    def __init__(self, on_activate) -> None:
        self._on_activate = on_activate
        self._server: QLocalServer | None = None

    def acquire(self) -> bool:
        socket = QLocalSocket()
        socket.connectToServer(SERVER_NAME)
        if socket.waitForConnected(300):
            socket.write(b'show')
            socket.flush()
            socket.waitForBytesWritten(300)
            socket.disconnectFromServer()
            return False

        QLocalServer.removeServer(SERVER_NAME)
        self._server = QLocalServer()
        self._server.newConnection.connect(self._handle)
        self._server.listen(SERVER_NAME)
        return True

    def _handle(self) -> None:
        if not self._server:
            return
        while self._server.hasPendingConnections():
            conn = self._server.nextPendingConnection()
            conn.readyRead.connect(lambda c=conn: self._read(c))
            conn.disconnected.connect(conn.deleteLater)

    def _read(self, conn: QLocalSocket) -> None:
        conn.readAll()
        self._on_activate()


class QifeiApp:
    """把窗口、托盘、悬浮条、后台进程串起来"""

    def __init__(self, app: QApplication, start_hidden: bool = False,
                 start_backend: bool = True):
        self.app = app
        self.store = store()
        self.start_hidden = start_hidden
        self.start_backend = start_backend

        self.supervisor = Supervisor()
        self.indicator = Indicator()
        self.listener = StatusListener()
        self.listener.statusChanged.connect(self._on_status)

        self.window = MainWindow(self.supervisor, self.indicator)
        self.window.hide()

        self.tray = self._build_tray()
        self.supervisor.logLine.connect(self._on_log)

        if self.start_backend:
            self.supervisor.start()
        self.listener.start()

        if not self.store.get('app.first_run_done', False):
            QTimer.singleShot(200, self._run_wizard)
        elif not start_hidden:
            QTimer.singleShot(120, self.show_window)

        self.app.aboutToQuit.connect(self._shutdown)

    # ---------- 托盘 ----------

    def _build_tray(self) -> QSystemTrayIcon:
        tray = QSystemTrayIcon(make_icon(), self.app)
        tray.setToolTip(f'{brand.BRAND_CN} · {brand.BRAND_TAGLINE}')

        menu = QMenu()
        action_open = QAction('打开设置', menu)
        action_open.triggered.connect(self.show_window)
        menu.addAction(action_open)

        menu.addSeparator()

        self.action_listen = QAction('暂停听写', menu)
        self.action_listen.triggered.connect(self._toggle_client)
        menu.addAction(self.action_listen)

        action_restart = QAction('重启后台', menu)
        action_restart.triggered.connect(self.supervisor.restart_all)
        menu.addAction(action_restart)

        action_models = QAction('打开模型目录', menu)
        action_models.triggered.connect(self._open_models)
        menu.addAction(action_models)

        menu.addSeparator()

        action_quit = QAction('退出', menu)
        action_quit.triggered.connect(self._quit)
        menu.addAction(action_quit)

        tray.setContextMenu(menu)
        tray.activated.connect(self._on_tray_activated)
        tray.show()
        return tray

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.show_window()

    def _toggle_client(self) -> None:
        if self.supervisor.client_running:
            self.supervisor.stop_client()
            self.action_listen.setText('继续听写')
            self.tray.setToolTip(f'{brand.BRAND_CN} · 已暂停听写')
        else:
            self.supervisor.start_client()
            self.action_listen.setText('暂停听写')
            self.tray.setToolTip(f'{brand.BRAND_CN} · {brand.BRAND_TAGLINE}')

    def _open_models(self) -> None:
        import os
        root = self.store.model_root
        root.mkdir(parents=True, exist_ok=True)
        try:
            from core.tools.open_path import open_path
            open_path(root)
        except Exception:
            pass

    # ---------- 交互 ----------

    def show_window(self) -> None:
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def _run_wizard(self) -> None:
        wizard = SetupWizard()
        if wizard.exec():
            self.supervisor.restart_all()
        self.show_window()

    def _on_status(self, state: str, text: str) -> None:
        if self.store.get('app.show_indicator', True):
            self.indicator.show_state(state, text)

    def _on_log(self, line: str) -> None:
        if '模型已就绪' in line or '加载' in line:
            pass

    def _quit(self) -> None:
        self.app.quit()

    def _shutdown(self) -> None:
        try:
            self.listener.stop()
        except Exception:
            pass
        try:
            self.supervisor.stop()
        except Exception:
            pass
        self.indicator.hide()
        self.tray.hide()


def _write_crash(exc: BaseException) -> str:
    """把崩溃信息落盘，窗口程序没有控制台，只能靠日志排查"""
    import traceback
    detail = ''.join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        path = paths.log_dir() / 'crash.log'
        with open(path, 'a', encoding='utf-8') as handle:
            handle.write(f'\n===== {time.strftime("%Y-%m-%d %H:%M:%S")} =====\n')
            handle.write(detail)
        return str(path)
    except OSError:
        return ''


def main(argv: list[str] | None = None) -> int:
    try:
        return _run(argv)
    except BaseException as exc:  # noqa: BLE001 - 兜底，避免静默退出
        log_path = _write_crash(exc)
        try:
            from PySide6.QtWidgets import QApplication as _QApp, QMessageBox as _Box
            if _QApp.instance() is None:
                _QApp([])
            _Box.critical(
                None,
                f'{brand.BRAND_CN} 启动失败',
                f'{type(exc).__name__}: {exc}\n\n详细日志：{log_path or "（无法写入）"}',
            )
        except Exception:
            pass
        return 1


def _run(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    start_hidden = '--tray' in argv or '--minimized' in argv
    start_backend = '--no-backend' not in argv

    app = QApplication(argv)
    app.setApplicationName(brand.BRAND_CN)
    app.setApplicationDisplayName(brand.BRAND_CN)
    app.setOrganizationName(brand.ORG_NAME)
    app.setWindowIcon(make_icon())
    app.setQuitOnLastWindowClosed(False)
    apply_theme(app)

    if not QSystemTrayIcon.isSystemTrayAvailable():
        print('[warn] 当前系统没有可用的托盘区域，托盘菜单不可用', flush=True)

    holder: dict[str, QifeiApp] = {}

    def activate() -> None:
        instance = holder.get('app')
        if instance:
            instance.show_window()

    single = SingleInstance(activate)
    if not single.acquire():
        return 0

    qifei = QifeiApp(app, start_hidden=start_hidden, start_backend=start_backend)
    holder['app'] = qifei
    return app.exec()
