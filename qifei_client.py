# coding: utf-8
"""
听写客户端入口

监听快捷键、采集麦克风、把识别结果打到光标处。
一般由界面自动拉起，不需要手动双击。

排查用：加 --selftest 参数只检查运行环境，不启动听写，打印结果后退出。
"""

import multiprocessing
import sys
from pathlib import Path

if __name__ == '__main__':
    multiprocessing.freeze_support()

    ROOT = Path(__file__).resolve().parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    if '--selftest' in sys.argv:
        import importlib

        failures = 0
        for name in ('numpy', 'sounddevice', 'pynput', 'websockets',
                     'pyclip', 'pypinyin', 'rapidfuzz'):
            try:
                module = importlib.import_module(name)
                version = getattr(module, '__version__', '?')
                print(f'[ok]   {name} {version}')
            except Exception as exc:
                failures += 1
                print(f'[fail] {name}: {type(exc).__name__}: {exc}')

        try:
            import sounddevice as sd
            devices = sd.query_devices()
            inputs = [d for d in devices if d.get('max_input_channels', 0) > 0]
            print(f'[ok]   麦克风 {len(inputs)} 个')
        except Exception as exc:
            failures += 1
            print(f'[fail] 麦克风: {type(exc).__name__}: {exc}')

        try:
            import config_client
            print(f'[ok]   基目录 {config_client.BASE_DIR}')
            keys = [s.get('key') for s in config_client.ClientConfig.shortcuts]
            print(f'[ok]   快捷键 {keys}')
        except Exception as exc:
            failures += 1
            print(f'[fail] config_client: {type(exc).__name__}: {exc}')

        print('SELFTEST', 'PASS' if failures == 0 else f'FAIL({failures})')
        sys.exit(0 if failures == 0 else 1)

    from core.client import CapsWriterClient
    CapsWriterClient().start()
