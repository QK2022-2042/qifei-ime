# coding: utf-8
"""
启飞输入法 · 图形界面入口

双击这个文件（或打包后的 QifeiIME.exe）即可运行。
后台的服务端与听写客户端会由界面自动拉起。
"""

import sys
from pathlib import Path

# 源码运行时保证能导入项目模块
ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    from qifei.app import main as run
    return run()


if __name__ == '__main__':
    sys.exit(main())
