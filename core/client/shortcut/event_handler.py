# coding: utf-8
"""
事件处理器

处理键盘和鼠标事件的逻辑：

- 切换模式（Tab）  ：按一下开始录音，再按一下结束录音
- 长按模式（X2 等） ：按住说话，松开结束
- 短按补发（空格）  ：时长不足阈值时取消录音并补发该按键，保证正常打字
- 暂停键（空格）    ：其它键正在录音时，按下即暂停 / 继续
"""

import time
from . import logger


class ShortcutEventHandler:
    """
    快捷键事件处理器

    处理按键按下和释放的逻辑，包括录音启动、取消、完成等
    """

    def __init__(self, tasks, pool, emulator):
        """
        初始化事件处理器

        Args:
            tasks: 快捷键任务字典
            pool: 线程池
            emulator: 快捷键模拟器
        """
        self.tasks = tasks
        self.pool = pool
        self.emulator = emulator

    def handle_keydown(self, key_name, task) -> None:
        """处理按键按下事件"""
        task.pressed = True

        # 切换模式：一次按下 = 开始 / 结束
        if not task.shortcut.hold_mode:
            task.toggle()
            return

        # 长按模式
        if not task.is_recording:
            task.launch()

    def handle_keyup(self, key_name, task) -> None:
        """处理按键释放事件"""
        task.pressed = False

        # 切换模式忽略松开事件
        if not task.shortcut.hold_mode:
            return

        if not task.is_recording:
            return

        duration = time.time() - task.recording_start_time
        logger.debug(f"[{key_name}] 松开，持续时间: {duration:.2f}s")

        if duration < task.threshold:
            self._handle_short_press(key_name, task)
        else:
            task.finish()

    def _handle_short_press(self, key_name, task) -> None:
        """处理短按情况：取消录音，必要时把按键补发出去"""
        cancel_start = time.perf_counter()
        task.cancel()
        cancel_time = (time.perf_counter() - cancel_start) * 1000
        logger.debug(f"[{key_name}] task.cancel() 耗时: {cancel_time:.2f}ms")

        # tap_replay 的按键（空格）必须补发，否则正常打字会丢空格
        if task.shortcut.tap_replay or task.shortcut.suppress:
            logger.debug(f"[{key_name}] 安排异步补发按键")
            self.pool.submit(self.emulator.emulate_key, key_name)
