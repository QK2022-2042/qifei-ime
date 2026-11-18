# coding: utf-8
"""
录音状态广播

客户端把「正在录音 / 已暂停 / 识别中」等状态通过本机 UDP 广播出去，
界面进程（启飞输入法悬浮条）监听该端口即可实时显示状态，
两个进程互不依赖，客户端单独运行也不受影响。
"""

from __future__ import annotations

import json
import socket
import time
from typing import Any, Optional

from config_client import ClientConfig as Config

# 状态常量
IDLE = 'idle'
RECORDING = 'recording'
PAUSED = 'paused'
RECOGNIZING = 'recognizing'
DONE = 'done'
ERROR = 'error'


class StatusBroadcaster:
    """极轻量的 UDP 状态广播器（发送失败静默忽略）"""

    def __init__(self, enabled: Optional[bool] = None, port: Optional[int] = None,
                 addr: str = '127.0.0.1'):
        self.enabled = Config.status_broadcast if enabled is None else enabled
        self.port = int(port or getattr(Config, 'status_port', 6019))
        self.addr = addr
        self._sock: Optional[socket.socket] = None
        self._last: tuple = (None, 0.0)

        if self.enabled:
            try:
                self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            except Exception:
                self._sock = None

    def send(self, state: str, text: str = '', **extra: Any) -> None:
        """广播一条状态"""
        if not self._sock:
            return

        # 同一状态 50ms 内不重复发送，避免高频抖动
        now = time.monotonic()
        if state == self._last[0] and now - self._last[1] < 0.05:
            return
        self._last = (state, now)

        payload = {'state': state, 'text': text, 'ts': time.time()}
        if extra:
            payload.update(extra)
        try:
            self._sock.sendto(
                json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                (self.addr, self.port),
            )
        except Exception:
            pass

    def close(self) -> None:
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
