# coding: utf-8
"""
设置存储

GUI 的唯一数据源：读写 %APPDATA%\\QifeiIME\\config.json。
客户端与服务端在启动时通过 config_override 读取同一份文件，
所以界面上改完设置后重启两个后台进程即可生效。
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Dict

import config_override

from .paths import default_model_dir

# 界面里可调、且需要写进 config.json 的默认值
DEFAULT: Dict[str, Dict[str, Any]] = {
    'app': {
        'first_run_done': False,
        'autostart': True,
        'show_indicator': True,
        'minimize_to_tray': True,
        'mic_device': -1,          # -1 表示系统默认麦克风
    },
    'client': {
        'shortcuts': [
            {'key': 'tab', 'type': 'keyboard', 'suppress': True,
             'hold_mode': False, 'enabled': True},
            {'key': 'space', 'type': 'keyboard', 'suppress': True,
             'hold_mode': True, 'threshold': 0.25, 'tap_replay': True,
             'pause_when_recording': True, 'enabled': True},
            {'key': 'x2', 'type': 'mouse', 'suppress': True,
             'hold_mode': True, 'enabled': True},
            {'key': 'caps_lock', 'type': 'keyboard', 'suppress': True,
             'hold_mode': True, 'enabled': False},
        ],
        'threshold': 0.3,
        'paste': False,
        'trash_punc': '，。,．.',
        'trash_punc_thresh': 8,
        'save_audio': True,
        'traditional_convert': False,
        'hot': True,
        'llm_enabled': True,
        'language': 'auto',
        'status_broadcast': True,
        'status_port': 6019,
    },
    'server': {
        'model_root': '',          # 空 = 应用目录下的 models
        'model_type': 'sensevoice',
        'format_num': True,
        'format_spell': True,
    },
    # 大模型（LLM 润色）配置：界面「大模型」页写入，客户端启动时读取
    # 默认直连 llama.cpp：程序自带动态库，不装 Ollama、不放 llama-server.exe
    # 就能加载自己下载的 .gguf。Ollama 只是给已经装了的用户留的可选项。
    'llm': {
        'backend': 'local',        # local（默认）| ollama（可选）| openai
        'provider': 'local',       # openai 后端时用来取默认地址
        'api_url': '',
        'api_key': '',
        'model': '',
        'model_path': '',          # 直连 llama.cpp 用的 gguf 路径
        'model_dirs': [],          # 额外扫描目录
        'ollama_exe': '',
        'llama_exe': '',           # 外部 llama-server.exe（一般用不到）
        'llama_port': 11500,
        'context': 4096,
        'gpu_layers': 0,           # 0 = 纯 CPU；有独显可调大或用 99 全卸载
        'n_threads': 0,            # 0 = llama.cpp 自动
        'engine_mode': 'auto',     # auto | native（进程内）| server（外部 exe）
        'connect_timeout': 5.0,
        'read_timeout': 600.0,     # 本地模型冷加载要等，别设太小
        'keep_alive': -1,          # -1 常驻内存（仅 Ollama 后端有效）
        'warmup_on_start': True,   # 启动时就把模型加载进内存
        'unify_roles': True,       # 所有角色统一用这个模型
    },
}


def _deep_merge(base: Dict, extra: Dict) -> Dict:
    """递归合并，extra 覆盖 base"""
    out = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


class Store:
    """轻量设置容器"""

    def __init__(self) -> None:
        self.data: Dict[str, Any] = copy.deepcopy(DEFAULT)
        self.reload()

    # ---------- 读写 ----------

    def reload(self) -> None:
        try:
            disk = config_override.load_overrides()
        except Exception:
            disk = {}
        self.data = _deep_merge(DEFAULT, disk)

        # v2.8 一次性迁移：老配置的默认后端是 ollama，而这一版改成
        # 直连 llama.cpp（不装 Ollama）。只迁移一次，之后用户在界面里
        # 主动选回 Ollama 不会被反复改掉。
        app = self.data.setdefault('app', {})
        llm = self.data.setdefault('llm', {})
        dirty = False
        if not app.get('llm_backend_migrated'):
            # v2.13：无论当前后端是什么，都要把标记补上。老实现只在
            # backend == 'ollama' 的分支里写标记，于是默认 local 的配置
            # 永远没有这个标记；用户后来在界面里主动选回 Ollama 后，
            # 下次启动仍满足「not migrated and backend == 'ollama'」，
            # 会被静默改回 local —— 表现为「选好的后端重启就没了」。
            # 只有真正从老版继承下来的 ollama 默认值才做迁移。
            if llm.get('backend') == 'ollama':
                llm['backend'] = 'local'
                llm['provider'] = 'local'
            app['llm_backend_migrated'] = True
            dirty = True

        # v2.10 开箱即用 + 换机器自愈：
        # 旧版把 model_path 以绝对路径写进 %APPDATA%。换电脑、换盘符、
        # 换安装目录、或把绿色版整个搬到别的机器上之后，这个路径就指向
        # 不存在的文件了。界面虽然能扫到随包内置模型，客户端却仍拿旧
        # 路径去 llama_model_load_from_file()，表现就是「装完不识别大模型」。
        # 这里统一走 resolve_model_path()：路径有效就原样保留（不覆盖用户
        # 手动换过的模型），失效就按文件名重定位 / 退回内置模型，并写回
        # 磁盘 —— 客户端不经过 Store，必须靠这份写回的配置才能拿到新路径。
        if llm.get('backend') == 'local':
            from llmkit import registry as llm_registry
            configured = str(llm.get('model_path') or '').strip()
            resolved, _note = llm_registry.resolve_model_path(
                configured, llm.get('model_dirs') or [])
            # v2.12：用 same_path（解析符号链接 / junction）。安装目录是
            # 链接时，resolved 与 configured 可能只是同一文件的两种写法，
            # 只比字符串会误判成「路径变了」并反复重写用户配置。
            if resolved and (not configured or
                             not llm_registry.same_path(resolved, configured)):
                llm['model_path'] = resolved
                llm['model'] = Path(resolved).stem
                dirty = True
            # 找不到任何模型时不清空配置：留着旧路径，界面会明确提示
            # 「原路径已失效」并让用户点「选择模型文件」，比悄悄清空好查。

        # v2.11：安装包里从来不含 llama-server.exe，engine_mode='server'
        # 对绝大多数用户就是纯陷阱 —— 一旦被写进配置（旧版遗留、误选、
        # 从另一台电脑搬来的配置），server.ensure() 会跳过进程内 native
        # 引擎直接去找不存在的 exe，表现就是「装完不识别语言大模型」。
        # 每次启动都检查，不再依赖一次性迁移标记：用户确实自己放了 exe
        # 时 find_server_exe() 会找到，不会被改掉。客户端不经过 Store，
        # 另有 server.ensure() 的运行时回落兜底。
        if llm.get('engine_mode') == 'server':
            from llmkit import server as llm_server
            if not llm_server.find_server_exe(str(llm.get('llama_exe') or '')):
                llm['engine_mode'] = 'auto'
                app['llm_engine_migrated'] = True
                dirty = True

        if dirty:
            self.save()

    def save(self) -> None:
        # app 段是界面私有的，也一并写入，方便下次读取
        try:
            config_override.save_overrides(self.data)
        except Exception:
            pass

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in path.split('.'):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def set(self, path: str, value: Any, save: bool = True) -> None:
        parts = path.split('.')
        node = self.data
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        if save:
            self.save()

    # ---------- 语义化快捷访问 ----------

    @property
    def model_root(self) -> Path:
        custom = (self.get('server.model_root') or '').strip()
        return Path(custom) if custom else default_model_dir()

    def set_model_root(self, path: str) -> None:
        self.set('server.model_root', str(path or ''))

    def shortcut(self, key: str) -> Dict[str, Any]:
        for item in self.get('client.shortcuts', []) or []:
            if isinstance(item, dict) and item.get('key') == key:
                return item
        return {}

    def set_shortcut_enabled(self, key: str, enabled: bool) -> None:
        for item in self.get('client.shortcuts', []) or []:
            if isinstance(item, dict) and item.get('key') == key:
                item['enabled'] = bool(enabled)
        self.save()


# 全局单例
_store: Store | None = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store
