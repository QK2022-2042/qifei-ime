import os
import sys
from collections.abc import Iterable
from pathlib import Path

# 版本信息
__version__ = '2.6'

# 项目根目录
# 打包成 exe 后 __file__ 指向 _internal 里的模块，热词等文件应当放在 exe 旁边，
# 所以这里必须按 sys.executable 定位。
#
# macOS 的 .app 例外：Apple 的签名规范要求 Contents/MacOS 里只能放代码，
# 数据文件必须放 Contents/Resources，再由 PyInstaller 交叉链接到
# Contents/Frameworks（即 sys._MEIPASS）。数据根目录因此要落在 _MEIPASS，
# 否则 .app 的代码签名会失效，辅助功能等 TCC 授权也随之失效。
if getattr(sys, 'frozen', False):
    if sys.platform == 'darwin' and getattr(sys, '_MEIPASS', None):
        BASE_DIR = os.path.abspath(sys._MEIPASS)
    else:
        BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))


# 客户端配置
class ClientConfig:
    addr = '127.0.0.1'          # Server 地址
    port = '6016'               # Server 端口

    # 快捷键配置列表
    #
    # 默认方案（启飞输入法）：
    #   Tab   —— 按一下开始录音，再按一下结束录音（可切换模式）
    #   Space —— 空闲时长按说话、松开结束；录音中按一下暂停、再按一下继续
    #   鼠标侧键 X2 —— 长按说话，保留原有的鼠标习惯
    shortcuts = [
        {
            'key': 'tab',           # Tab 键：切换式录音
            'type': 'keyboard',
            'suppress': True,       # 阻塞按键，Tab 不再跳格
            'hold_mode': False,     # 单击模式：按一下开始，再按一下结束
            'enabled': True
        },
        {
            'key': 'space',
            'type': 'keyboard',
            'suppress': True,       # 阻塞按键，短按会自动补发空格
            'hold_mode': True,      # 长按模式：按住说话，松开结束
            'threshold': 0.25,      # 低于 0.25 秒视为普通空格输入
            'tap_replay': True,     # 短按补发空格，保证正常打字不受影响
            'pause_when_recording': True,   # 其它方式录音时，空格作为暂停/继续键
            'enabled': True
        },
        {
            'key': 'x2',
            'type': 'mouse',
            'suppress': True,
            'hold_mode': True,
            'enabled': True
        },
        {
            'key': 'caps_lock',     # 保留大写锁定键方案，默认关闭，可在设置里开启
            'type': 'keyboard',
            'suppress': True,
            'hold_mode': True,
            'enabled': False
        },
    ]

    threshold    = 0.3          # 快捷键触发阈值（秒）

    # 音频输入设备：None 表示跟随系统默认；
    # 也可填设备序号（如 1）或名称片段（如 'Realtek USB2.0 Audio'）。
    # macOS 上系统默认输入有时是虚拟设备（只出静音），需要手动指定真实麦克风。
    audio_device = None

    # 录音状态广播：把「录音中 / 暂停 / 识别中」推给悬浮指示器
    status_broadcast = True
    status_port = 6019

    paste        = False        # 是否以写入剪切板然后模拟 Ctrl-V 粘贴的方式输出结果
    restore_clip = True         # 模拟粘贴后是否恢复剪贴板
    paste_apps   = [
        # 通讯类：模拟打字不稳定
        'WeiXin.exe', 'Telegram.exe',
        # WPS Office 全系列
        'wps.exe',       # WPS 文字
        'et.exe',        # WPS 表格
        'wpp.exe',       # WPS 演示
        # Microsoft Office 全系列
        'winword.exe',   # Word
        'excel.exe',     # Excel
        'powerpnt.exe',  # PowerPoint
        'outlook.exe',   # Outlook
        'onenote.exe',   # OneNote
    ]  # 匹配时强制粘贴（大小写不敏感）

    enter_apps   = [('happ.exe', 0.5), ('hexin.exe', 0.5)]  # (应用名, 延迟秒数) 输出完成后自动回车，如同花顺，输入股票名后，需要回车才能切换

    save_audio = True           # 是否保存录音文件
    audio_name_len = 20         # 将录音识别结果的前多少个字存储到录音文件名中，建议不要超过200
    
    context = ''                # 提示词上下文，用于辅助 Fun-ASR-Nano 模型识别（例如输入人名、地名、专业术语等）
    language = 'auto'           # 识别语言：'auto', 'chinese', 'english', 'japanese' 等（各引擎支持范围不同）

    trash_punc = '，。,.'       # 识别结果要消除的末尾标点
    trash_punc_thresh = 8       # 识别结果的单词数量低于阈值时，强制去除末尾标点
    trash_punc_apps = ['WeiXin.exe', ]   # 对于指定的应用，强制去除末尾标点

    traditional_convert = False     # 是否将识别结果转换为繁体中文
    traditional_locale = 'zh-hant'  # 繁体地区：'zh-hant'（标准繁体）, 'zh-tw'（台湾繁体）, 'zh-hk'（香港繁体）

    hot = True                 # 是否启用热词替换（统一 RAG 匹配）
    hot_thresh = 0.85           # RAG 替换热词阈值（高阈值，用于实际替换）
    hot_similar = 0.6           # RAG 相似热词阈值（低阈值，用于 LLM 上下文）
    hot_rule = True             # 是否启用自定义规则替换（基于正则表达式）

    llm_enabled = True          # 是否启用 LLM 润色功能，需要配置 LLM/ 目录下的角色文件
    llm_stop_key = 'esc'        # 中断 LLM 输出的快捷键

    # 托盘图标：界面（QifeiIME.exe）已经提供了托盘，客户端默认不再重复显示
    enable_tray = False

    # 日志配置
    log_level = 'DEBUG'          # 日志级别：'DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'

    mic_seg_duration = 60       # 麦克风听写时分段长度：60秒
    mic_seg_overlap = 4         # 麦克风听写时分段重叠：4秒

    file_seg_duration = 60      # 转录文件时分段长度
    file_seg_overlap = 4        # 转录文件时分段重叠

    file_save_srt = True        # 转录文件时是否保存 srt 字幕
    file_save_txt = True        # 转录文件时是否保存 txt 文本（按标点切分后的）
    file_save_json = True       # 转录文件时是否保存 json 结果（含原始时间戳）
    file_save_merge = False      # 转录文件时是否保存 merge.txt（未切分的段落长文本）

    udp_broadcast = False               # 是否启用 UDP 广播输出结果
    udp_broadcast_targets = [           # UDP 广播目标地址列表，格式: (地址, 端口)
        ('127.255.255.255', 6017),      # 本地回环广播
        # ('192.168.1.255', 6017),      # 局域网广播（示例，按需启用）
    ]

    udp_control = False             # 是否启用 UDP 控制录音（外部程序发送 START/STOP 命令）
    udp_control_addr = '127.0.0.1'  # UDP 控制监听地址（'0.0.0.0' 允许外部访问）
    udp_control_port = 6018         # UDP 控制监听端口


# 快捷键配置说明
r"""
快捷键配置字段说明：
  key        - 按键名称（见下方可用按键列表）
  type       - 输入类型：'keyboard'（键盘）或 'mouse'（鼠标）
  suppress   - 是否阻塞按键（True=阻塞，False=不阻塞）
  hold_mode  - 长按模式（True=按下录音松开停止，False=按一下开始再按一下停止）
  threshold  - 该键单独的触发阈值（秒），不写则用 ClientConfig.threshold
  tap_replay - 短按（时长 < threshold）时补发该按键，用于把空格当普通输入
  pause_when_recording
             - 当别的键正在录音时，本键改为「暂停 / 继续」键（空格默认开启）
  enabled    - 是否启用此快捷键

阻塞模式说明：
  - 阻塞模式  ：按键不会传给其它程序；长按录音，短按（<threshold）自动补发按键
  - 非阻塞模式：对于 CapsLock/NumLock/ScrollLock 这类切换键，松开时会自动补发，以恢复按键状态

默认按键方案：
  Tab        单击开始录音，再按一下结束录音
  Space      空闲时长按说话、松开结束；录音中按一下暂停、再按一下继续
  X2         鼠标侧键，长按说话

可用按键名称：

  字母数字：a - z, 0 - 9（大键盘）

  符号键：, . / \ ` ' - = [ ] ; '


  功能键：f1 - f24

  控制键:
      ctrl_l,   ctrl_r,
      shift,  shift_r,
      alt_l,    alt_gr,
      cmd,    cmd_r

  特殊键：
      space, enter, tab, backspace, delete, insert, home, end
      page_up, page_down, esc, caps_lock, num_lock, scroll_lock
      print_screen, pause, menu

  方向键：up, down, left, right

  鼠标键：x1, x2

示例配置：
  {'key': 'caps_lock', 'type': 'keyboard', 'suppress': False, 'hold_mode': True, 'enabled': True}, 
  {'key': 'f12', 'type': 'keyboard', 'suppress': True, 'hold_mode': True, 'enabled': True}, 
  {'key': 'x2', 'type': 'mouse', 'suppress': True, 'hold_mode': True, 'enabled': True}, 
"""


# ─────────────────────────────────────────────────────────────
# GUI 配置覆盖：启飞输入法把设置存成 JSON，这里在导入时覆盖默认值
# ─────────────────────────────────────────────────────────────
try:
    from config_override import load_overrides as _load_overrides, apply_to as _apply_to
    _apply_to(ClientConfig, _load_overrides().get('client', {}))
except Exception:
    pass
