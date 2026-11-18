# coding: utf-8
"""
音频流管理模块

提供 AudioStreamManager 类用于管理音频输入流，包括流的创建、
启动、停止和设备检测。
"""

from __future__ import annotations

import sys
import time
import threading
from typing import TYPE_CHECKING, Optional

import numpy as np
import sounddevice as sd

from config_client import ClientConfig as Config
from core.client.state import console
from . import logger

if TYPE_CHECKING:
    from core.client.state import ClientState
    from ..app import CapsWriterClient



class AudioStreamManager:
    """
    音频流管理器

    负责管理音频输入流的生命周期，包括：
    - 检测和选择音频设备
    - 创建和启动音频流
    - 处理音频数据回调
    - 流的重启和关闭

    Attributes:
        state: 客户端状态实例
        sample_rate: 采样率（默认 48000Hz）
        block_duration: 每个数据块的时长（秒，默认 0.05s）
    """

    SAMPLE_RATE = 48000
    BLOCK_DURATION = 0.05  # 50ms
    PROBE_DURATION = 0.6   # 自动探测每个设备时的录音时长（秒）
    SILENCE_PEAK = 1e-4    # 峰值低于此值视为「数字静音」（约 -80 dBFS）

    def __init__(self, app: CapsWriterClient):
        """
        初始化音频流管理器

        Args:
            app: 客户端 App 实例
        """
        self.app = app
        self._channels = 1
        self._running = False  # 标志是否应该运行
        self._device_index: Optional[int] = None
        self._device_name: str = '未选择'

    @property
    def state(self) -> ClientState:
        """快捷访问状态单例"""
        return self.app.state

    def _audio_callback(
        self,
        indata: np.ndarray,
        frames: int,
        time_info,
        status: sd.CallbackFlags
    ) -> None:
        """
        音频数据回调函数

        当音频流接收到新数据时调用，将数据放入异步队列中。
        """
        # 只在录音状态时处理数据；暂停时直接丢弃，不上传也不落盘
        if not self.state.capture_active:
            return

        import asyncio

        # 将数据放入队列
        if self.app.loop and self.state.queue_in:
            asyncio.run_coroutine_threadsafe(
                self.state.queue_in.put({
                    'type': 'data',
                    'time': time.time(),
                    'data': indata.copy(),
                }),
                self.app.loop
            )

    def _on_stream_finished(self) -> None:
        """音频流结束回调"""
        if not threading.main_thread().is_alive():
            return
        if not self._running:
            return

        logger.info("音频流意外结束，正在尝试重启...")
        self.reopen()

    @staticmethod
    def _input_devices() -> list:
        """列出所有可用的输入设备 (序号, 设备信息)"""
        try:
            return [
                (index, info)
                for index, info in enumerate(sd.query_devices())
                if info.get('max_input_channels', 0) > 0
            ]
        except Exception as e:
            logger.warning(f"读取音频设备列表失败: {e}")
            return []

    @classmethod
    def _probe_input_device(
        cls,
        device: Optional[int],
        timeout: float = 3.0,
    ) -> Optional[float]:
        """
        短促录一段，返回峰值幅度；设备打不开 / 超时返回 None。

        用峰值而不是平均能量判断，避免把「无声的数字静音」当成可用设备。
        某些 USB 声卡在系统层面会卡住几十秒（CoreAudio 返回 0x10004003），
        所以放到线程里探测并加超时，不能让它拖住客户端启动。
        """
        result: dict = {}

        def worker() -> None:
            try:
                info = sd.query_devices(device, kind='input')
                rate = int(info.get('default_samplerate') or 16000)
                frames = max(1, int(rate * cls.PROBE_DURATION))
                audio = sd.rec(
                    frames,
                    samplerate=rate,
                    channels=1,
                    device=device,
                    dtype='float32',
                    blocking=True,
                )
                if audio is None or audio.size == 0:
                    result['peak'] = 0.0
                else:
                    result['peak'] = float(np.max(np.abs(audio)))
            except Exception as e:
                result['error'] = e

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout)

        if thread.is_alive():
            logger.warning(f"探测音频设备 {device} 超时（{timeout:.0f}s），跳过")
            return None
        if 'error' in result:
            logger.debug(f"探测音频设备 {device} 失败: {result['error']}")
            return None
        return result.get('peak')

    def _resolve_device(self, spec) -> Optional[int]:
        """把配置里的序号 / 名称片段解析成设备序号"""
        devices = self._input_devices()
        if not devices:
            return None

        if isinstance(spec, int) and not isinstance(spec, bool):
            for index, _info in devices:
                if index == spec:
                    return index
            return None

        if isinstance(spec, str):
            text = spec.strip()
            if text.isdigit():
                return self._resolve_device(int(text))
            for index, info in devices:
                if text and text in info.get('name', ''):
                    return index

        return None

    def _select_device(self) -> Optional[int]:
        """
        选择真正能收到声音的输入设备。

        优先使用配置指定的设备；若它只返回数字静音（例如插着但没接麦克风
        的 USB 声卡、或 TCC 未授权），就逐个探测其它输入设备，挑峰值最大的
        一个。探测都失败时回退到系统默认设备。
        """
        spec = getattr(Config, 'audio_device', None)
        if spec in (-1, '', 'default'):
            spec = None

        configured = self._resolve_device(spec) if spec is not None else None
        if spec is not None and configured is None:
            logger.warning(f"配置的音频设备 {spec!r} 不存在，回退到系统默认麦克风")

        # 系统默认设备：device=None
        probe_target = configured
        peak = self._probe_input_device(probe_target)
        peak_text = '打不开' if peak is None else f'{peak:.2e}'
        logger.info(f"麦克风探测: {self._device_label(probe_target)} 峰值={peak_text}")

        if peak is not None and peak >= self.SILENCE_PEAK:
            return configured

        # 当前设备没有声音，扫描全部输入设备
        best_index: Optional[int] = None
        best_peak = self.SILENCE_PEAK
        for index, info in self._input_devices():
            if index == configured:
                continue
            value = self._probe_input_device(index)
            logger.info(
                f"麦克风探测: [{index}] {info.get('name', '未知设备')} "
                f"峰值={'打不开' if value is None else f'{value:.2e}'}"
            )
            if value is not None and value > best_peak:
                best_peak = value
                best_index = index

        if best_index is not None:
            logger.warning(
                f"当前麦克风没有信号，已自动切换到 "
                f"[{best_index}] {sd.query_devices(best_index).get('name', '未知设备')}"
            )
            return best_index

        if peak is None:
            # 配置设备打不开、也没有其它可用设备 → 交给系统默认
            logger.warning("没有可用的麦克风设备，回退到系统默认输入")
            return None

        logger.warning(
            "所有输入设备都只返回数字静音："
            "请确认系统「隐私与安全性 → 麦克风」已允许启飞输入法，"
            "或在系统声音设置里把输入设备切到真正的麦克风"
        )
        return configured

    @property
    def device_name(self) -> str:
        """当前使用的麦克风名称"""
        return self._device_name

    def _device_label(self, index: Optional[int]) -> str:
        try:
            info = sd.query_devices(index, kind='input')
            return f"[{index if index is not None else '默认'}] {info.get('name', '未知设备')}"
        except Exception:
            return f"[{index}] 未知设备"

    def start(self) -> Optional[sd.InputStream]:
        """
        启动音频流

        Returns:
            创建的音频输入流，如果失败返回 None
        """
        if self._running:
            logger.debug("音频流已在运行，跳过启动")
            return self.state.stream

        # 自动选择真正有声音的输入设备
        device_spec = self._select_device()

        try:
            device = sd.query_devices(device_spec, kind='input')
            self._channels = min(2, device['max_input_channels'])
            device_name = device.get('name', '未知设备')
            self._device_index = device_spec
            self._device_name = device_name
            console.print(
                f'使用音频设备：[italic]{device_name}，声道数：{self._channels}',
                end='\n\n'
            )
            logger.info(
                f"找到音频设备: [{device_spec if device_spec is not None else '默认'}] "
                f"{device_name}, 声道数: {self._channels}"
            )
        except UnicodeDecodeError:
            logger.warning("无法获取音频设备名称（编码问题）")
        except sd.PortAudioError:
            logger.error("未找到麦克风设备")
            if sys.stdin.isatty():
                input('按回车键退出')
            sys.exit(1)

        # 创建音频流
        try:
            stream = sd.InputStream(
                samplerate=self.SAMPLE_RATE,
                blocksize=int(self.BLOCK_DURATION * self.SAMPLE_RATE),
                device=device_spec,
                dtype="float32",
                channels=self._channels,
                callback=self._audio_callback,
                finished_callback=self._on_stream_finished,
            )
            stream.start()

            self.state.stream = stream
            self._running = True
            logger.debug(
                f"音频流已启动: 采样率={self.SAMPLE_RATE}, "
                f"块大小={int(self.BLOCK_DURATION * self.SAMPLE_RATE)}"
            )
            return stream

        except sd.PortAudioError as e:
            logger.error(f"创建音频流失败: {e}", exc_info=True)
            if '-9999' in str(e):
                console.print("""
[bold red]检测到麦克风被占用或权限异常（错误码 -9999）[/bold red]
请尝试以下解决方案：

  1. 设置 > 隐私和安全性 > 麦克风，将「允许桌面应用访问麦克风」打开
  2. 状态栏右下角音量图标 > 右键菜单 > 声音 > 麦克风的属性，关闭「允许应用程序独占控制该设备」
  3. 状态栏右下角音量图标 > 右键菜单 > 声音 > 麦克风的属性，关闭「增强效果」
""")
            return None
        except Exception as e:
            logger.error(f"创建音频流失败: {e}", exc_info=True)
            return None

    def stop(self) -> None:
        """停止音频流"""
        if not self._running:
            return

        self._running = False  # 标记为停止
        if self.state.stream is not None:
            try:
                self.state.stream.close()
                logger.debug("音频流已停止")
            except Exception as e:
                logger.debug(f"停止音频流时发生错误: {e}")
            finally:
                self.state.stream = None

    def reopen(self) -> Optional[sd.InputStream]:
        """
        重新启动音频流

        Returns:
            新创建的音频输入流
        """
        logger.info("正在重启音频流...")

        # 停止旧流
        self.stop()

        # 重载 PortAudio，更新设备列表
        try:
            sd._terminate()
            sd._ffi.dlclose(sd._lib)
            sd._lib = sd._ffi.dlopen(sd._libname)
            sd._initialize()
        except Exception as e:
            logger.warning(f"重载 PortAudio 时发生警告: {e}")

        # 等待设备稳定
        time.sleep(0.1)

        # 启动新流
        return self.start()
