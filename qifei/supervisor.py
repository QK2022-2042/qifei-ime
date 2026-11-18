# coding: utf-8
"""
后台进程托管

界面只负责显示，真正的听写能力由两个后台进程提供：
  - 服务端：加载语音模型，负责推理
  - 客户端：监听快捷键、采集麦克风、把文字上屏

本模块负责启动、守护、重启这两个进程，并把它们的输出写进日志文件，
用户全程看不到黑窗口。
"""

from __future__ import annotations

import socket
import sys
import time
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QObject, QProcess, QTimer, Signal

from . import paths

CREATE_NO_WINDOW = 0x08000000


class Supervisor(QObject):
    """两个后台进程的守护者"""

    serverState = Signal(str, str)   # (状态, 说明)
    clientState = Signal(str, str)
    logLine = Signal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self._stopping = False
        self._server: Optional[QProcess] = None
        self._client: Optional[QProcess] = None
        self._server_retry = 0
        self._client_retry = 0

        self._port_timer = QTimer(self)
        self._port_timer.setInterval(400)
        self._port_timer.timeout.connect(self._poll_server_port)
        self._port_deadline = 0.0

        self._log_file = None
        try:
            self._log_file = open(paths.log_dir() / 'qifei.log', 'a', encoding='utf-8')
        except OSError:
            self._log_file = None

    # ---------- 路径 ----------

    def _program(self, kind: str):
        """返回 (可执行文件, 参数)"""
        root = paths.app_root()
        if paths.is_frozen():
            name = 'QifeiServer' if kind == 'server' else 'QifeiClient'
            if sys.platform == 'win32':
                name += '.exe'
            return str(root / name), []
        script = root / ('qifei_server.py' if kind == 'server' else 'qifei_client.py')
        return sys.executable, [str(script)]

    # ---------- 生命周期 ----------

    def start(self) -> None:
        self._stopping = False
        self.start_server()
        # 服务端端口就绪后再拉客户端，避免客户端一上来就报连接失败
        self._port_deadline = time.time() + 180
        self._port_timer.start()

    def start_server(self) -> None:
        if self._server and self._server.state() != QProcess.NotRunning:
            return
        program, args = self._program('server')
        if not Path(program).exists():
            self.serverState.emit('down', f'缺少服务端程序：{program}')
            return

        proc = QProcess(self)
        proc.setProgram(program)
        proc.setArguments(args)
        proc.setWorkingDirectory(str(paths.app_root()))
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: self._pipe(proc, 'server'))
        proc.finished.connect(lambda code, _status: self._on_server_exit(code))
        proc.errorOccurred.connect(lambda err: self._on_error('server', err))
        self._hide_console(proc)
        proc.start()
        self._server = proc
        self.serverState.emit('starting', '正在加载语音模型…')

    def start_client(self) -> None:
        if self._client and self._client.state() != QProcess.NotRunning:
            return
        program, args = self._program('client')
        if not Path(program).exists():
            self.clientState.emit('down', f'缺少客户端程序：{program}')
            return

        proc = QProcess(self)
        proc.setProgram(program)
        proc.setArguments(args)
        proc.setWorkingDirectory(str(paths.app_root()))
        proc.setProcessChannelMode(QProcess.MergedChannels)
        proc.readyReadStandardOutput.connect(lambda: self._pipe(proc, 'client'))
        proc.finished.connect(lambda code, _status: self._on_client_exit(code))
        proc.errorOccurred.connect(lambda err: self._on_error('client', err))
        self._hide_console(proc)
        proc.start()
        self._client = proc
        self.clientState.emit('starting', '正在启动听写…')

    def stop(self) -> None:
        self._stopping = True
        self._port_timer.stop()
        for proc in (self._client, self._server):
            if proc and proc.state() != QProcess.NotRunning:
                proc.terminate()
                if not proc.waitForFinished(2500):
                    proc.kill()
        if self._log_file:
            try:
                self._log_file.close()
            except OSError:
                pass

    def restart_all(self) -> None:
        """设置变更后重启两个进程"""
        self._port_timer.stop()
        for proc in (self._client, self._server):
            if proc and proc.state() != QProcess.NotRunning:
                proc.terminate()
                proc.waitForFinished(2000)
        self._server_retry = 0
        self._client_retry = 0
        QTimer.singleShot(300, self.start)

    def restart_client(self) -> None:
        if self._client and self._client.state() != QProcess.NotRunning:
            self._client.terminate()
            self._client.waitForFinished(1500)
        QTimer.singleShot(200, self.start_client)

    def stop_client(self) -> None:
        """暂停听写（保留服务端，重新开启时不用再等模型加载）"""
        if self._client and self._client.state() != QProcess.NotRunning:
            self._client.terminate()

    # ---------- 内部 ----------

    def _hide_console(self, proc: QProcess) -> None:
        """隐藏子进程控制台窗口（服务端需要控制台才有 stdin）"""
        if sys.platform != 'win32':
            return
        try:
            def modifier(arguments):
                arguments.flags |= CREATE_NO_WINDOW
            proc.setCreateProcessArgumentsModifier(modifier)
        except Exception:
            pass

    def _pipe(self, proc: QProcess, kind: str) -> None:
        data = bytes(proc.readAllStandardOutput()).decode('utf-8', 'replace')
        if not data:
            return
        for line in data.splitlines():
            if line.strip():
                self.logLine.emit(f'[{kind}] {line}')
        if self._log_file:
            try:
                self._log_file.write(data)
                self._log_file.flush()
            except OSError:
                pass

    def _on_server_exit(self, code: int) -> None:
        self.serverState.emit('down', f'服务端已退出（代码 {code}）')
        if self._stopping:
            return
        self._server_retry += 1
        if self._server_retry <= 3:
            QTimer.singleShot(1500 * self._server_retry, self.start_server)

    def _on_client_exit(self, code: int) -> None:
        self.clientState.emit('down', f'听写服务已退出（代码 {code}）')
        if self._stopping:
            return
        self._client_retry += 1
        if self._client_retry <= 5:
            QTimer.singleShot(1200 * self._client_retry, self.start_client)

    def _on_error(self, kind: str, error) -> None:
        message = f'进程启动失败：{error}'
        (self.serverState if kind == 'server' else self.clientState).emit('down', message)

    def _poll_server_port(self) -> None:
        """轮询服务端端口，就绪后拉起客户端"""
        if time.time() > self._port_deadline:
            self._port_timer.stop()
            self.serverState.emit('down', '模型加载超时，请检查模型是否已安装')
            return

        sock = socket.socket()
        sock.settimeout(0.25)
        try:
            if sock.connect_ex(('127.0.0.1', 6016)) == 0:
                self._port_timer.stop()
                self._server_retry = 0
                self.serverState.emit('ready', '模型已就绪')
                self.start_client()
        finally:
            sock.close()

    @property
    def server_running(self) -> bool:
        return bool(self._server and self._server.state() != QProcess.NotRunning)

    @property
    def client_running(self) -> bool:
        return bool(self._client and self._client.state() != QProcess.NotRunning)
