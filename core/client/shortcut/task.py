# coding: utf-8
"""
快捷键任务模块

管理单个快捷键的录音任务状态。

支持三种交互：
1. 切换模式（hold_mode=False）：按一下开始录音，再按一下结束录音（Tab 键）
2. 长按模式（hold_mode=True） ：按住说话，松开结束（鼠标侧键）
3. 暂停能力：录音过程中可暂停 / 继续，暂停期间音频不上传（空格键）
"""

from __future__ import annotations
import asyncio
import time
import uuid
from threading import Event
from typing import TYPE_CHECKING, Optional

from . import logger
from core.tools.my_status import Status

if TYPE_CHECKING:
    from core.client.shortcut.shortcut_config import Shortcut
    from core.client.state import ClientState
    from core.client.audio.recorder import AudioRecorder
    from core.client.app import CapsWriterClient


class ShortcutTask:
    """
    单个快捷键的录音任务

    跟踪每个快捷键独立的录音状态，防止互相干扰。
    """

    def __init__(self, app: CapsWriterClient, shortcut: Shortcut, recorder_class=None):
        """
        初始化快捷键任务

        Args:
            app: 客户端 App 实例
            shortcut: 快捷键配置
            recorder_class: AudioRecorder 类（可选，用于延迟导入）
        """
        self.app = app
        self.shortcut = shortcut
        self._recorder_class = recorder_class

        # 任务状态
        self.task: Optional[asyncio.Future] = None
        self.recording_start_time: float = 0.0
        self.is_recording: bool = False
        self.session_id: Optional[str] = None

        # hold_mode 状态跟踪
        self.pressed: bool = False
        self.released: bool = True
        self.event: Event = Event()

        # 线程池（用于补发按键）
        self.pool = None

        # 录音状态动画
        self._status = Status('开始录音', spinner='point')

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    @property
    def paused(self) -> bool:
        """当前是否处于暂停状态"""
        return self.state.paused

    def _broadcast(self, state: str, text: str = '') -> None:
        """向界面广播状态（失败静默）"""
        broadcaster = getattr(self.app, 'status', None)
        if broadcaster is None:
            return
        try:
            broadcaster.send(state, text, source=self.shortcut.key)
        except Exception:
            pass

    def _get_recorder(self, session_id: str) -> AudioRecorder:
        """获取 AudioRecorder 实例"""
        if self._recorder_class is None:
            from core.client.audio.recorder import AudioRecorder
            self._recorder_class = AudioRecorder
        try:
            return self._recorder_class(self.app, session_id=session_id)
        except TypeError:
            # 兼容旧签名
            return self._recorder_class(self.app)

    # ========== 录音生命周期 ==========

    def launch(self) -> None:
        """启动录音任务"""
        if self.is_recording:
            logger.debug(f"[{self.shortcut.key}] 已在录音，忽略重复启动")
            return

        logger.info(f"[{self.shortcut.key}] 触发：开始录音")

        # 记录开始时间与会话标识
        self.recording_start_time = time.time()
        self.is_recording = True
        self.session_id = str(uuid.uuid4())
        sid = self.session_id

        # 将开始标志放入队列
        asyncio.run_coroutine_threadsafe(
            self.state.queue_in.put({
                'type': 'begin', 'time': self.recording_start_time,
                'data': None, 'sid': sid,
            }),
            self.app.loop
        )

        # 更新录音状态
        self.state.resume_recording()
        self.state.start_recording(self.recording_start_time)

        # 打印动画：正在录音
        self._status.start()

        # 启动识别任务
        recorder = self._get_recorder(sid)
        self.task = asyncio.run_coroutine_threadsafe(
            recorder.record_and_send(),
            self.app.loop,
        )

        self._broadcast('recording', '正在聆听…')

    def finish(self) -> None:
        """完成录音任务：结束本次会话并送去识别"""
        if not self.is_recording:
            return

        logger.info(f"[{self.shortcut.key}] 释放：完成录音")

        self.is_recording = False
        self.state.stop_recording()
        self._status.stop()

        asyncio.run_coroutine_threadsafe(
            self.state.queue_in.put({
                'type': 'finish',
                'time': time.time(),
                'data': None,
                'sid': self.session_id,
            }),
            self.app.loop
        )
        self.session_id = None

        # 执行 restore（可恢复按键 + 非阻塞模式）
        # 阻塞模式下按键不会发送到系统，状态不会改变，不需要恢复
        if self.shortcut.is_toggle_key() and not self.shortcut.suppress:
            self._restore_key()

        self._broadcast('recognizing', '正在识别…')

    def cancel(self) -> None:
        """取消录音任务（时间过短 / 误触）"""
        logger.debug(f"[{self.shortcut.key}] 取消录音任务")

        self.is_recording = False
        self.state.stop_recording()
        self._status.stop()

        if self.task is not None:
            try:
                self.task.cancel()
            except Exception:
                pass
            self.task = None
        self.session_id = None

        # 清空队列里本次会话残留的音频，避免污染下一次录音
        try:
            asyncio.run_coroutine_threadsafe(self._drain_queue(), self.app.loop)
        except Exception:
            pass

        self._broadcast('idle', '')

    async def _drain_queue(self) -> None:
        """清空录音输入队列"""
        await asyncio.sleep(0)
        queue = self.state.queue_in
        while True:
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            else:
                try:
                    queue.task_done()
                except Exception:
                    pass

    def toggle(self) -> None:
        """切换式录音：正在录则结束，否则开始"""
        if self.is_recording:
            self.finish()
        else:
            self.launch()

    # ========== 暂停 / 继续 ==========

    def pause(self) -> None:
        """暂停录音（音频不再上传，会话保留）"""
        if not self.is_recording or self.state.paused:
            return
        self.state.pause_recording()
        logger.info(f"[{self.shortcut.key}] 录音已暂停")
        try:
            self._status.update('已暂停')
        except Exception:
            pass
        self._broadcast('paused', '已暂停')

    def resume(self) -> None:
        """继续录音"""
        if not self.is_recording or not self.state.paused:
            return
        self.state.resume_recording()
        logger.info(f"[{self.shortcut.key}] 录音已继续")
        try:
            self._status.update('开始录音')
        except Exception:
            pass
        self._broadcast('recording', '正在聆听…')

    def toggle_pause(self) -> None:
        """在暂停与继续之间切换"""
        if self.state.paused:
            self.resume()
        else:
            self.pause()

    # ========== 按键恢复 ==========

    def _restore_key(self) -> None:
        """恢复按键状态（防自捕获逻辑由 ShortcutManager 处理）"""
        manager_ref = getattr(self, '_manager_ref', None)
        manager = manager_ref() if manager_ref else None
        if manager:
            logger.debug(f"[{self.shortcut.key}] 自动恢复按键状态 (suppress={self.shortcut.suppress})")
            manager.schedule_restore(self.shortcut.key)
        else:
            logger.warning(f"[{self.shortcut.key}] manager 引用丢失，无法 restore")
