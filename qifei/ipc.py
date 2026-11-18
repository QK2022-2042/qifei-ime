# coding: utf-8
"""
与听写客户端通信

客户端把录音状态通过本机 UDP 广播出来，界面用这个线程接收，
从而驱动悬浮条显示「正在聆听 / 已暂停 / 正在识别」。
"""

from __future__ import annotations

import json
import socket

from PySide6.QtCore import QThread, Signal

from .store import store


class StatusListener(QThread):
    """监听客户端状态广播"""

    statusChanged = Signal(str, str)   # (state, text)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = True
        self._port = int(store().get('client.status_port', 6019) or 6019)

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(('127.0.0.1', self._port))
        except OSError:
            try:
                sock.bind(('0.0.0.0', self._port))
            except OSError:
                return
        sock.settimeout(0.4)

        while self._running:
            try:
                data, _addr = sock.recvfrom(8192)
            except socket.timeout:
                continue
            except OSError:
                break
            try:
                payload = json.loads(data.decode('utf-8'))
            except Exception:
                continue
            state = str(payload.get('state') or '')
            text = str(payload.get('text') or '')
            if state:
                self.statusChanged.emit(state, text)

        sock.close()

    def stop(self) -> None:
        self._running = False
        self.wait(1200)
