# coding: utf-8
"""
快捷键管理器（重构版）

统一管理多个快捷键，处理键盘和鼠标事件，支持：
1. 多快捷键并发处理
2. 防止不同按键互相干扰
3. restore 功能的防自捕获逻辑
4. hold_mode 和 click_mode 支持
"""
from __future__ import annotations
import platform
import time
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Dict, List, Optional, Set

from pynput import keyboard, mouse

from . import logger
from core.client.shortcut.key_mapper import *
from core.client.shortcut.key_mapper import KeyMapper
from core.client.shortcut.emulator import ShortcutEmulator
from core.client.shortcut.event_handler import ShortcutEventHandler
from core.client.shortcut.task import ShortcutTask

if TYPE_CHECKING:
    from core.client.shortcut.shortcut_config import Shortcut
    from core.client.state import ClientState
    from core.client.app import CapsWriterClient



class ShortcutManager:
    """
    快捷键管理器

    统一管理多个快捷键，使用 pynput 监听键盘和鼠标事件。
    所有事件处理都在 win32_event_filter 中完成，确保高性能和低延迟。
    """

    def __init__(self, app: CapsWriterClient, shortcuts: List[Shortcut]):
        """
        初始化快捷键管理器

        Args:
            app: 客户端 App 实例
            shortcuts: 快捷键配置列表
        """
        self.app = app
        self.shortcuts = shortcuts

        # 监听器
        self.keyboard_listener: Optional[keyboard.Listener] = None
        self.mouse_listener: Optional[mouse.Listener] = None

        # 快捷键任务映射（key -> ShortcutTask）
        self.tasks: Dict[str, ShortcutTask] = {}

        # 线程池
        self._pool = ThreadPoolExecutor(max_workers=4)

        # 按键模拟器
        self._emulator = ShortcutEmulator()

        # 按键恢复状态追踪
        self._restoring_keys = set()

        # macOS：正在作为「暂停键」使用的按键（松开时只清状态，不走 handle_keyup）
        self._darwin_pause_keys: Set[str] = set()

        # 事件处理器
        self._event_handler = ShortcutEventHandler(self.tasks, self._pool, self._emulator)

        # 初始化快捷键任务
        self._init_tasks()

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    def _init_tasks(self) -> None:
        """初始化所有快捷键任务"""
        from config_client import ClientConfig as Config

        for shortcut in self.shortcuts:
            if not shortcut.enabled:
                continue

            task = ShortcutTask(self.app, shortcut)
            task._manager_ref = lambda: self  # 弱引用，用于回调
            task.pool = self._pool
            task.threshold = shortcut.get_threshold(Config.threshold)
            self.tasks[shortcut.key] = task

    # ========== 监听器创建 ==========

    def recording_task(self, exclude_key: str = '') -> Optional[ShortcutTask]:
        """返回当前正在录音的任务（可排除某个键，用于暂停键判断）"""
        for key, task in self.tasks.items():
            if key == exclude_key:
                continue
            if task.is_recording:
                return task
        return None

    def _route_pause_key(self, key_name: str, task: ShortcutTask, msg: int) -> bool:
        """
        处理「暂停键」逻辑

        当别的快捷键正在录音时，本键（空格）不再触发录音，
        而是切换暂停 / 继续。返回 True 表示事件已被消费。
        """
        if not task.shortcut.pause_when_recording:
            return False

        owner = self.recording_task(exclude_key=key_name)
        if owner is None:
            return False

        if msg in KEY_DOWN_MESSAGES:
            # 忽略长按产生的自动重复
            if not task.pressed:
                task.pressed = True
                owner.toggle_pause()
        elif msg in KEY_UP_MESSAGES:
            task.pressed = False

        logger.debug(f"[{key_name}] 暂停键事件 -> {owner.shortcut.key}")
        return True

    def create_keyboard_filter(self):
        """创建键盘事件过滤器"""
        def win32_event_filter(msg, data):
            # 只处理 KEYDOWN 和 KEYUP 消息
            if msg not in KEYBOARD_MESSAGES:
                return True

            key_name = KeyMapper.vk_to_name(data.vkCode)

            # 防自捕获检查
            if self._check_emulating(key_name, msg):
                return True
            if self._check_restoring(key_name, msg):
                return True

            # 查找匹配的快捷键
            if key_name not in self.tasks:
                return True

            task = self.tasks[key_name]

            # 录音中按空格 => 暂停 / 继续
            consumed = self._route_pause_key(key_name, task, msg)

            if not consumed:
                if msg in KEY_DOWN_MESSAGES:
                    # 切换模式下忽略长按自动重复，避免疯狂开停
                    if task.pressed and not task.shortcut.hold_mode:
                        if task.shortcut.suppress and self.keyboard_listener:
                            self.keyboard_listener.suppress_event()
                        return True
                    self._event_handler.handle_keydown(key_name, task)
                elif msg in KEY_UP_MESSAGES:
                    self._event_handler.handle_keyup(key_name, task)

            # 阻塞事件
            if task.shortcut.suppress and self.keyboard_listener:
                self.keyboard_listener.suppress_event()

            return True

        return win32_event_filter

    def create_mouse_filter(self):
        """创建鼠标事件过滤器"""
        def win32_event_filter(msg, data):
            # 只处理 XBUTTON 消息
            if msg not in MOUSE_MESSAGES:
                return True

            # 获取按键标识
            xbutton = (data.mouseData >> 16) & 0xFFFF
            button_name = 'x1' if xbutton == XBUTTON1 else 'x2'

            # 防自捕获检查
            if self._check_emulating(button_name, msg, is_mouse=True):
                return True

            # 查找匹配的快捷键
            if button_name not in self.tasks:
                return True

            task = self.tasks[button_name]

            # 处理鼠标事件
            if msg == WM_XBUTTONDOWN:
                self._event_handler.handle_keydown(button_name, task)
            elif msg == WM_XBUTTONUP:
                self._handle_mouse_keyup(button_name, task)

            # 阻塞事件
            if task.shortcut.suppress and self.mouse_listener:
                self.mouse_listener.suppress_event()

            return True

        return win32_event_filter

    def _handle_mouse_keyup(self, button_name: str, task) -> None:
        """处理鼠标按键释放事件"""
        task.pressed = False

        # 切换模式：松开不做任何事
        if not task.shortcut.hold_mode:
            return

        # 长按模式
        if not task.is_recording:
            return

        duration = time.time() - task.recording_start_time
        logger.debug(f"[{button_name}] 松开按键，持续时间: {duration:.3f}s")

        if duration < task.threshold:
            task.cancel()
            if task.shortcut.suppress:
                logger.debug(f"[{button_name}] 安排异步补发鼠标按键")
                self._pool.submit(self._emulator.emulate_mouse_click, button_name)
        else:
            task.finish()

    # ========== 按键恢复管理 ==========

    def schedule_restore(self, key: str) -> None:
        """
        安排按键恢复（延迟执行，避免在事件处理中阻塞）

        Args:
            key: 要恢复的按键

        注意：标志清除只在按键释放事件中处理（_check_restoring），
        避免在线程中提前清除导致主线程收到重复消息。
        """
        from pynput import keyboard

        self._restoring_keys.add(key)

        def do_restore():
            import time
            time.sleep(0.05)  # 延迟 50ms
            if key == 'caps_lock':
                controller = keyboard.Controller()
                controller.press(keyboard.Key.caps_lock)
                controller.release(keyboard.Key.caps_lock)

        self._pool.submit(do_restore)

    def is_restoring(self, key: str) -> bool:
        """检查是否正在恢复指定按键"""
        return key in self._restoring_keys

    def clear_restoring_flag(self, key: str) -> None:
        """清除恢复标志"""
        self._restoring_keys.discard(key)

    # ========== 防自捕获检查 ==========

    def _check_emulating(self, key_name: str, msg: int, is_mouse: bool = False) -> bool:
        """检查是否正在模拟按键"""
        if not self._emulator.is_emulating(key_name):
            return False

        # 松开时清除标志
        if is_mouse:
            if msg == WM_XBUTTONUP:
                self._emulator.clear_emulating_flag(key_name)
        else:
            if msg in (WM_KEYUP, WM_SYSKEYUP):
                self._emulator.clear_emulating_flag(key_name)

        return True  # 放行

    def _check_restoring(self, key_name: str, msg: int) -> bool:
        """检查是否正在恢复按键"""
        if not self.is_restoring(key_name):
            return False

        if msg in (WM_KEYUP, WM_SYSKEYUP):
            self.clear_restoring_flag(key_name)

        return True  # 放行

    # ========== macOS 监听 ==========

    def _start_darwin_listener(self) -> None:
        """
        macOS 全局快捷键监听

        pynput 在 macOS 上没有 win32_event_filter，改用 darwin_intercept：
        在事件进入系统前判断按键，命中已配置的快捷键时吞掉事件，
        再交给原有的事件处理器，行为与 Windows 版一致。
        """
        if self.keyboard_listener and self.keyboard_listener.is_alive():
            logger.debug("键盘监听器已在运行，跳过启动")
            return

        try:
            import Quartz  # pyobjc，pynput 在 macOS 上的依赖
        except Exception as exc:
            logger.error(f"缺少 pyobjc/Quartz，无法启动全局快捷键监听: {exc}")
            return

        self._request_darwin_accessibility()

        self._darwin_quartz = Quartz
        self.keyboard_listener = keyboard.Listener(
            on_press=lambda _key: None,
            on_release=lambda _key: None,
            darwin_intercept=self._darwin_intercept,
        )
        self.keyboard_listener.start()
        logger.info("键盘监听器已启动（macOS darwin_intercept）")

    def _request_darwin_accessibility(self) -> None:
        """主动请求 macOS「辅助功能」授权。

        pynput 需要的是主动式事件 tap（kCGEventTapOptionDefault），没有
        辅助功能授权时 CGEventTapCreate 直接返回 NULL；而 pynput 只会
        打一行警告，仍然报告「监听器已启动」，表现为按快捷键毫无反应。
        用户手动去设置里添加包内的 QifeiClient 也很难匹配上签名身份。

        这里调用系统官方 API：未授权时会弹出标准授权对话框，并把当前
        进程自动写进「系统设置 → 隐私与安全性 → 设备控制和数据访问」。
        """
        try:
            from ApplicationServices import (
                AXIsProcessTrusted,
                AXIsProcessTrustedWithOptions,
                kAXTrustedCheckOptionPrompt,
            )
        except Exception as exc:
            logger.warning(f"无法加载 ApplicationServices，跳过授权检查: {exc}")
            return

        try:
            if AXIsProcessTrusted():
                logger.info("辅助功能权限: 已授权")
                return
        except Exception as exc:
            logger.warning(f"查询辅助功能权限失败: {exc}")
            return

        try:
            AXIsProcessTrustedWithOptions({kAXTrustedCheckOptionPrompt: True})
        except Exception as exc:
            logger.warning(f"弹出辅助功能授权提示失败: {exc}")
        logger.warning(
            "辅助功能权限: 未授权。已弹出系统授权提示，请在"
            "「系统设置 → 隐私与安全性 → 设备控制和数据访问」中勾选本程序，"
            "然后重启启飞输入法。"
        )

    def _darwin_intercept(self, event_type, event):
        """macOS 事件拦截器：返回 None 表示吞掉该事件"""
        Quartz = getattr(self, '_darwin_quartz', None)
        if Quartz is None:
            return event

        # 注意：pynput 的 darwin_intercept 传进来的是裸 CGEventRef，没有
        # getIntegerValueField_ 这类方法（那是 pyobjc 包装成对象后才有的）。
        # 必须调用 Quartz 的 C 函数形式读取字段，否则每次按键都会抛
        # AttributeError，然后被下面的 except 静默吞掉，表现为「快捷键完全无反应」。
        try:
            code = int(Quartz.CGEventGetIntegerValueField(
                event, Quartz.kCGKeyboardEventKeycode))
        except Exception as exc:
            logger.debug(f"读取按键键码失败: {exc}")
            return event

        name = DARWIN_KEYCODES.get(code)
        if name is None or name not in self.tasks:
            return event

        task = self.tasks[name]
        consume = (lambda: None) if task.shortcut.suppress else (lambda: event)

        # 补发的按键不要再次触发，但必须放行给目标应用
        if self._emulator.is_emulating(name):
            if event_type == Quartz.kCGEventKeyUp:
                self._emulator.clear_emulating_flag(name)
            return event

        is_down = event_type == Quartz.kCGEventKeyDown
        is_up = event_type == Quartz.kCGEventKeyUp
        if not is_down and not is_up:
            # flagsChanged（CapsLock 等）暂不支持
            return event

        if is_down:
            # 忽略长按产生的自动重复
            try:
                if bool(Quartz.CGEventGetIntegerValueField(
                        event, Quartz.kCGKeyboardEventAutorepeat)):
                    return consume()
            except Exception:
                pass

            # 另一个快捷键正在录音时，本键作为暂停 / 继续键
            if task.shortcut.pause_when_recording:
                owner = self.recording_task(exclude_key=name)
                if owner is not None:
                    if not task.pressed:
                        task.pressed = True
                        owner.toggle_pause()
                    self._darwin_pause_keys.add(name)
                    return consume()

            self._darwin_pause_keys.discard(name)
            self._event_handler.handle_keydown(name, task)
        else:
            if name in self._darwin_pause_keys:
                self._darwin_pause_keys.discard(name)
                task.pressed = False
                return consume()
            self._event_handler.handle_keyup(name, task)

        return consume()

    # ========== 公共接口 ==========

    def start(self) -> None:
        """启动所有监听器"""
        if platform.system() == 'Darwin':
            # macOS 不支持 win32_event_filter，走 darwin_intercept
            self._start_darwin_listener()
            for shortcut in self.shortcuts:
                if shortcut.enabled:
                    mode = "长按" if shortcut.hold_mode else "单击"
                    logger.info(f"  [{shortcut.key}] {mode}模式, 阻塞:{shortcut.suppress}")
            return

        has_keyboard = any(s.type == 'keyboard' for s in self.shortcuts if s.enabled)
        has_mouse = any(s.type == 'mouse' for s in self.shortcuts if s.enabled)

        if has_keyboard:
            if self.keyboard_listener and self.keyboard_listener.is_alive():
                logger.debug("键盘监听器已在运行，跳过启动")
            else:
                self.keyboard_listener = keyboard.Listener(
                    win32_event_filter=self.create_keyboard_filter()
                )
                self.keyboard_listener.start()
                logger.info("键盘监听器已启动")

        if has_mouse:
            if self.mouse_listener and self.mouse_listener.is_alive():
                logger.debug("鼠标监听器已在运行，跳过启动")
            else:
                self.mouse_listener = mouse.Listener(
                    win32_event_filter=self.create_mouse_filter()
                )
                self.mouse_listener.start()
                logger.info("鼠标监听器已启动")

        # 打印所有启用的快捷键
        for shortcut in self.shortcuts:
            if shortcut.enabled:
                mode = "长按" if shortcut.hold_mode else "单击"
                toggle = "可恢复" if shortcut.is_toggle_key() else "普通键"
                logger.info(f"  [{shortcut.key}] {mode}模式, 阻塞:{shortcut.suppress}, {toggle}")

    def stop(self) -> None:
        """停止所有监听器和清理资源"""
        if self.keyboard_listener:
            try:
                self.keyboard_listener.stop()
                logger.debug("键盘监听器已停止")
            except Exception:
                pass
            finally:
                self.keyboard_listener = None
                
        if self.mouse_listener:
            try:
                self.mouse_listener.stop()
                logger.debug("鼠标监听器已停止")
            except Exception:
                pass
            finally:
                self.mouse_listener = None

        # 取消所有任务
        for task in self.tasks.values():
            if task.is_recording:
                task.cancel()

        # 关闭线程池
        self._pool.shutdown(wait=False)
        logger.debug("快捷键管理器线程池已关闭")
