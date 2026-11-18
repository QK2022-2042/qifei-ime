"""
LLM 角色加载器

功能：
1. 自动加载 LLM/ 目录下的所有角色
2. 从模块中提取角色配置
3. 错误处理和验证
"""

import sys
from pathlib import Path
from typing import Dict
from .llm_role_config import RoleConfig


class RoleLoader:
    """角色加载器"""

    def __init__(self):
        self.roles_registry: Dict[str, RoleConfig] = {}
        from config_client import BASE_DIR
        self.llm_dir = Path(BASE_DIR) / 'LLM'
        self.load_all_roles()

    def register_role(self, module_name: str, module) -> str:
        """从模块注册角色

        Returns:
            角色名称
        """
        from dataclasses import fields
        
        # 1. 自动从 RoleConfig 获取所有的命名字段
        config = {'module_name': module_name}
        for f in fields(RoleConfig):
            if f.name == 'module_name':
                continue
            # 如果模块（角色脚本）里定义了这个变量，就读取它
            if hasattr(module, f.name):
                config[f.name] = getattr(module, f.name)
            # 否则不设置，让 RoleConfig 自动使用 dataclass 的默认值

        # 2. 创建 RoleConfig 对象
        role_config = RoleConfig(**config)

        # 3. 用界面里的「大模型」设置覆盖连接信息
        self._apply_runtime_overrides(role_config)

        # 空字符串和「默认」都表示默认角色
        role_name = role_config.display_name or RoleConfig.DEFAULT_ROLE_NAME

        self.roles_registry[role_name] = role_config

        return role_name

    @staticmethod
    def _apply_runtime_overrides(role_config: RoleConfig) -> None:
        """把 config.json 里 llm 段的设置套到角色上

        规则：
        - 后端是本地（ollama / local）且开启了「统一模型」：
          所有角色都改用这个本地模型，省得每个角色文件单独改；
        - 角色本身就用本地后端：只覆盖模型名和地址；
        - 远程后端（deepseek 等）：尊重角色文件自己的配置，不动。
        """
        try:
            from config_override import load_overrides
            data = dict(load_overrides().get('llm') or {})
        except Exception:
            data = {}
        if not data:
            # 老版本升级后可能还没写过 llm 段。此时必须和界面用同一套默认值
            # （直连 llama.cpp），否则角色文件里的 ollama/deepseek 会把链路
            # 悄悄带回「必须装 Ollama」的老路。
            from llmkit import default_settings
            data = default_settings()

        backend = str(data.get('backend') or 'local').lower()
        unify = bool(data.get('unify_roles', True))
        model = str(data.get('model') or '').strip()
        api_url = str(data.get('api_url') or '').strip()
        local_backend = backend in ('ollama', 'local')

        if local_backend and unify:
            role_config.provider = backend
            role_config.backend = backend
            role_config.api_url = api_url
            role_config.api_key = ''
            if model:
                role_config.model = model
        elif role_config.provider in ('ollama', 'local'):
            if model:
                role_config.model = model
            if api_url:
                role_config.api_url = api_url

        read_timeout = data.get('read_timeout')
        try:
            if read_timeout:
                role_config.timeout = float(read_timeout)
        except (TypeError, ValueError):
            pass

        keep_alive = data.get('keep_alive')
        if keep_alive is not None:
            role_config.keep_alive = keep_alive

    def load_all_roles(self):
        """加载所有角色"""
        self.roles_registry.clear()
        errors = []

        for file_path in sorted(self.llm_dir.glob('*.py')):
            if file_path.name == '__init__.py':
                continue

            try:
                module_name = f"LLM.{file_path.stem}"

                if module_name in sys.modules:
                    del sys.modules[module_name]

                module = __import__(module_name, fromlist=[''])

                if not hasattr(module, 'provider') or not hasattr(module, 'model'):
                    errors.append({
                        'file': file_path.name,
                        'error': '缺少必需字段',
                        'details': '缺少 provider 或 model'
                    })
                    continue

                self.register_role(module_name, module)

            except SyntaxError as e:
                errors.append({
                    'file': file_path.name,
                    'error': '语法错误',
                    'details': f"第 {e.lineno} 行: {e.msg}"
                })
            except Exception as e:
                errors.append({
                    'file': file_path.name,
                    'error': type(e).__name__,
                    'details': str(e)
                })

        return errors

    def reload_role(self, file_path: str):
        """重新加载单个角色"""
        try:
            file_path = Path(file_path)

            if file_path.suffix != '.py' or file_path.name == '__init__.py':
                return False, "不是角色文件"

            module_name = f"LLM.{file_path.stem}"

            if module_name in sys.modules:
                del sys.modules[module_name]

            module = __import__(module_name, fromlist=[''])

            if not hasattr(module, 'provider') or not hasattr(module, 'model'):
                return False, "缺少必需字段"

            self.register_role(module_name, module)

            return True, None

        except SyntaxError as e:
            return False, f"语法错误 (第 {e.lineno} 行): {e.msg}"
        except Exception as e:
            return False, f"{type(e).__name__}: {str(e)}"

    def get_roles(self) -> Dict[str, RoleConfig]:
        """获取所有角色"""
        return self.roles_registry.copy()

    def get_default_role(self) -> RoleConfig:
        """获取默认角色"""
        return self.roles_registry.get(RoleConfig.DEFAULT_ROLE_NAME, RoleConfig(name=RoleConfig.DEFAULT_ROLE_NAME, module_name='', enabled=False))

    def get_role_by_name(self, name: str) -> RoleConfig:
        """根据名字获取角色"""
        return self.roles_registry.get(name, RoleConfig(name=RoleConfig.DEFAULT_ROLE_NAME, module_name='', enabled=False))
