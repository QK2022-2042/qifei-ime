# coding: utf-8
"""
录音交互回归测试

不依赖真实麦克风与按键硬件：直接调用事件处理器与快捷键管理器，
验证 Tab 切换、空格对讲、空格暂停三条主链路。

运行：
    .venv\\Scripts\\python.exe -m pytest tests -q
或
    .venv\\Scripts\\python.exe tests\\test_recording_flow.py
"""

from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 让测试读到代码里的默认配置，而不是用户机器上的 config.json
os.environ['QIFEI_CONFIG'] = str(Path(tempfile.gettempdir()) / 'qifei-test-none.json')
if Path(os.environ['QIFEI_CONFIG']).exists():
    Path(os.environ['QIFEI_CONFIG']).unlink()


class FakeBroadcaster:
    def __init__(self):
        self.events = []

    def send(self, state, text='', **extra):
        self.events.append(state)

    def close(self):
        pass


class FakeApp:
    """最小可用的 App 替身"""

    def __init__(self):
        from core.client.state import ClientState
        self.loop = asyncio.new_event_loop()
        self.state = ClientState(app=self)
        self.status = FakeBroadcaster()
        self._thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self._thread.start()

    def stop(self):
        """停掉循环线程、取消残留任务、关闭循环

        直接对运行中的循环调用 close() 会抛 RuntimeError，
        而且残留的录音协程会刷 "Task was destroyed" 警告，
        所以这里先取消任务、让循环空转一轮，再关闭。
        """
        if self.loop.is_closed():
            return

        def _cancel_all():
            for task in asyncio.all_tasks(self.loop):
                task.cancel()

        try:
            self.loop.call_soon_threadsafe(_cancel_all)
            self.loop.call_soon_threadsafe(self.loop.stop)
        except RuntimeError:
            pass

        self._thread.join(timeout=3)

        try:
            if not self.loop.is_closed():
                self.loop.run_until_complete(asyncio.sleep(0.05))
        except (RuntimeError, asyncio.CancelledError):
            pass

        if not self.loop.is_closed():
            self.loop.close()


class FakeRecorder:
    """把队列读完即结束的假录制器"""

    def __init__(self, app, session_id=None):
        self.app = app
        self.session_id = session_id
        self.messages = []

    async def record_and_send(self):
        while True:
            item = await self.app.state.queue_in.get()
            self.app.state.queue_in.task_done()
            self.messages.append(item['type'])
            if item['type'] == 'finish':
                break


def _make_task(app, key='tab', hold_mode=False, threshold=0.25, **kwargs):
    from core.client.shortcut.shortcut_config import Shortcut
    from core.client.shortcut.task import ShortcutTask

    shortcut = Shortcut(key=key, type='keyboard', suppress=True,
                        hold_mode=hold_mode, threshold=threshold, **kwargs)
    task = ShortcutTask(app, shortcut, recorder_class=FakeRecorder)
    task.threshold = threshold
    return task


class EventHandlerTests(unittest.TestCase):
    """直接测试事件处理器"""

    def setUp(self):
        from core.client.shortcut.event_handler import ShortcutEventHandler

        self.app = FakeApp()
        self.emulated = []

        class Emulator:
            def emulate_key(inner, name):
                self.emulated.append(name)

            def emulate_mouse_click(inner, name):
                self.emulated.append(name)

        class Pool:
            def submit(inner, fn, *args):
                fn(*args)

        self.handler = ShortcutEventHandler({}, Pool(), Emulator())

    def tearDown(self):
        self.app.stop()

    def test_tab_toggles_recording(self):
        task = _make_task(self.app, key='tab', hold_mode=False)

        self.handler.handle_keydown('tab', task)
        self.assertTrue(task.is_recording)
        self.assertTrue(self.app.state.recording)

        self.handler.handle_keyup('tab', task)
        self.assertTrue(task.is_recording, 'Tab 松开不应结束录音')

        self.handler.handle_keydown('tab', task)
        self.assertFalse(task.is_recording)
        self.assertFalse(self.app.state.recording)
        self.assertEqual(self.app.status.events[-1], 'recognizing')

    def test_space_hold_records_and_stops(self):
        task = _make_task(self.app, key='space', hold_mode=True, threshold=0.25,
                          tap_replay=True, pause_when_recording=True)

        self.handler.handle_keydown('space', task)
        self.assertTrue(task.is_recording)

        task.recording_start_time = time.time() - 1.0
        self.handler.handle_keyup('space', task)
        self.assertFalse(task.is_recording)
        self.assertEqual(self.emulated, [], '长按不应补发空格')

    def test_space_tap_replays_space(self):
        task = _make_task(self.app, key='space', hold_mode=True, threshold=0.25,
                          tap_replay=True, pause_when_recording=True)

        self.handler.handle_keydown('space', task)
        self.assertTrue(task.is_recording)

        self.handler.handle_keyup('space', task)   # 立即松开 => 短按
        self.assertFalse(task.is_recording)
        self.assertEqual(self.emulated, ['space'], '短按应补发空格')


class ManagerRoutingTests(unittest.TestCase):
    """测试快捷键管理器把空格路由为暂停键"""

    def setUp(self):
        from core.client.shortcut.shortcut_manager import ShortcutManager
        from core.client.shortcut.shortcut_config import Shortcut

        self.app = FakeApp()
        shortcuts = [
            Shortcut(key='tab', type='keyboard', suppress=True, hold_mode=False),
            Shortcut(key='space', type='keyboard', suppress=True, hold_mode=True,
                     threshold=0.25, tap_replay=True, pause_when_recording=True),
        ]
        self.manager = ShortcutManager(self.app, shortcuts)
        for task in self.manager.tasks.values():
            task._recorder_class = FakeRecorder

    def tearDown(self):
        self.manager.stop()
        self.app.stop()

    def test_space_pauses_tab_recording(self):
        from core.client.shortcut.key_mapper import WM_KEYDOWN, WM_KEYUP

        tab_task = self.manager.tasks['tab']
        space_task = self.manager.tasks['space']

        tab_task.launch()
        self.assertTrue(tab_task.is_recording)

        consumed = self.manager._route_pause_key('space', space_task, WM_KEYDOWN)
        self.assertTrue(consumed)
        self.assertTrue(self.app.state.paused)
        self.assertFalse(space_task.is_recording, '暂停时不应另起一段录音')

        self.manager._route_pause_key('space', space_task, WM_KEYUP)
        self.manager._route_pause_key('space', space_task, WM_KEYDOWN)
        self.assertFalse(self.app.state.paused, '第二次按空格应继续录音')

        tab_task.finish()
        self.assertFalse(self.app.state.recording)

    def test_space_is_normal_when_idle(self):
        from core.client.shortcut.key_mapper import WM_KEYDOWN

        space_task = self.manager.tasks['space']
        consumed = self.manager._route_pause_key('space', space_task, WM_KEYDOWN)
        self.assertFalse(consumed, '空闲时空格不应被当作暂停键')


class ConfigOverrideTests(unittest.TestCase):
    """测试配置覆盖层"""

    def test_apply_only_known_fields(self):
        import config_override

        class Dummy:
            value = 1
            flag = True
            items = [1, 2]

        config_override.apply_to(Dummy, {
            'value': 5,
            'flag': 'bad',        # 类型不符，应跳过
            'items': 'bad',       # 类型不符，应跳过
            'unknown': 1,         # 未定义字段，应跳过
        })
        self.assertEqual(Dummy.value, 5)
        self.assertTrue(Dummy.flag)
        self.assertEqual(Dummy.items, [1, 2])

    def test_client_shortcuts_have_new_semantics(self):
        from config_client import ClientConfig

        by_key = {item['key']: item for item in ClientConfig.shortcuts}
        self.assertIn('tab', by_key)
        self.assertFalse(by_key['tab']['hold_mode'], 'Tab 应为切换模式')
        self.assertTrue(by_key['space']['tap_replay'])
        self.assertTrue(by_key['space']['pause_when_recording'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
