"""
LLM 处理引擎

功能：
1. 执行 LLM 调用（Ollama / 内置 llama.cpp / OpenAI 兼容）
2. 处理流式输出
3. 冷启动自动重试：本地模型第一次加载慢，超时不再直接判死刑
4. 统一错误处理，模型没装时给出能直接照做的提示
5. 精确的生成时间统计（从第一个 token 开始）
"""
import time
import traceback
from typing import Callable, Optional, Dict, Any, List, Tuple

from .llm_role_config import RoleConfig
from .llm_interfaces import IContextManager
from .llm_client_pool import ClientPool
from .llm_constants import APIConfig, estimate_tokens
from .llm_exceptions import (
    APIException,
    ModelNotAvailableError,
    TimeoutErrorWrapper,
)
from llmkit.http_client import (LLMConnectError, LLMHTTPError, LLMStatusError,
                                LLMTimeoutError)
from llmkit import connector as llm_connector
from llmkit import registry as llm_model_registry
from . import logger


class LLMProcessor:
    """LLM 处理引擎 - 负责 API 调用和流式输出"""

    def __init__(self, client_pool: ClientPool):
        self.client_pool = client_pool
        self._settings: Dict[str, Any] = {}
        self._settings_loaded = False

    # ---------- 配置 ----------

    def settings(self) -> Dict[str, Any]:
        """读取 config.json 的 llm 段（界面里配的后端/路径）"""
        if not self._settings_loaded:
            try:
                from config_override import load_overrides
                self._settings = dict(load_overrides().get('llm') or {})
            except Exception:
                self._settings = {}
            if not self._settings:
                # 与界面、角色加载器共用同一套默认值：直连 llama.cpp
                self._settings = llm_connector.default_settings()
            self._settings_loaded = True
        return self._settings

    def refresh_settings(self) -> None:
        """角色重载时调用，让新设置生效"""
        self._settings_loaded = False
        self.client_pool.clear()

    # ---------- 主流程 ----------

    def process(
        self,
        role_config: RoleConfig,
        messages: List[Dict[str, str]],
        callback: Optional[Callable[[str], None]] = None,
        should_stop_check: Optional[Callable[[], bool]] = None,
        context_manager: Optional[IContextManager] = None
    ) -> Tuple[str, int, float]:
        """执行 LLM 处理

        Returns:
            (处理后的文本, 输出token数, 生成时间秒)
        """
        backend = llm_connector.from_role(role_config, self.settings())
        logger.info(f"开始 LLM 处理：{backend.describe()}")

        # 本地后端：先把服务拉起来 / 把模型加载进去
        if backend.is_local:
            ok, message = llm_connector.ensure(backend)
            if not ok:
                raise ModelNotAvailableError(backend.model, message)
            logger.debug(f"本地后端就绪：{message}")

        attempts = 1 + (APIConfig.COLD_START_RETRIES if backend.is_local else 0)
        last_error: Optional[Exception] = None

        for attempt in range(1, attempts + 1):
            try:
                client = self.client_pool.get_client(
                    provider=backend.kind,
                    api_url=backend.base_url,
                    api_key=backend.api_key,
                    timeout=backend.read_timeout,
                    connect_timeout=backend.connect_timeout,
                    keep_alive=backend.keep_alive,
                )
                return self._stream_request(
                    client, callback, should_stop_check,
                    role_config, backend, context_manager, messages,
                )

            except (LLMTimeoutError, LLMConnectError) as exc:
                last_error = exc
                if attempt < attempts:
                    logger.warning(
                        f"本地模型首次响应超时，{APIConfig.COLD_START_RETRY_DELAY:.0f} 秒后重试"
                        f"（第 {attempt}/{attempts} 次）：{exc}"
                    )
                    time.sleep(APIConfig.COLD_START_RETRY_DELAY)
                    # 重试前再确认一次后端在跑
                    llm_connector.ensure(backend)
                    continue
                raise self._wrap(exc, backend)

            except LLMStatusError as exc:
                if exc.model_missing:
                    raise ModelNotAvailableError(
                        backend.model, self._model_hint(backend, exc)) from exc
                raise APIException(f"[{backend.kind}] {exc}", backend.kind) from exc

            except LLMHTTPError as exc:
                raise APIException(f"[{backend.kind}] {exc}", backend.kind) from exc

            except Exception as exc:
                logger.error(f"LLM 处理失败：{exc}\n{traceback.format_exc()}")
                raise APIException(f"LLM 处理失败: {exc}", backend.kind) from exc

        raise self._wrap(last_error or RuntimeError('未知错误'), backend)

    # ---------- 错误包装 ----------

    def _wrap(self, exc: Exception, backend) -> Exception:
        if isinstance(exc, LLMTimeoutError):
            return TimeoutErrorWrapper(exc, backend.kind)
        hint = '本地模型可能还在加载，可在「大模型」页调大生成超时'
        if backend.kind == 'local':
            hint = ('请确认已在「大模型」页选中本地 .gguf 文件；'
                    '直连 llama.cpp 不需要安装 Ollama，也不需要联网下载')
        elif backend.kind == 'ollama':
            hint = '请确认 Ollama 已启动（ollama serve），模型已导入'
        return APIException(f"[{backend.kind}] {exc}。{hint}", backend.kind)

    def _model_hint(self, backend, exc: LLMStatusError) -> str:
        """模型不存在时，把本机可用的模型列出来，省得用户猜"""
        try:
            if backend.kind == 'ollama':
                names = [m.name for m in llm_model_registry.list_installed(backend.base_url)]
                if names:
                    return ('模型没找到。本机已有：' + '、'.join(names[:8])
                            + '。可在「大模型」页选择，或导入本地 gguf。')
        except Exception:
            pass
        return '模型没找到，请在「大模型」页导入本地模型文件或选择已有模型。'

    # ---------- 请求构造 ----------

    def _build_request_params(
        self,
        role_config: RoleConfig,
        messages: List[Dict[str, str]]
    ) -> Dict[str, Any]:
        """统一构建请求参数"""
        params = {
            'model': role_config.model,
            'messages': messages,
            'temperature': role_config.temperature,
            'top_p': role_config.top_p,
        }

        if role_config.max_tokens > 0:
            params['max_tokens'] = role_config.max_tokens

        if role_config.stop:
            stop_list = role_config.stop
            if isinstance(stop_list, str):
                stop_list = [s.strip() for s in stop_list.split(',')]
            params['stop'] = stop_list

        if role_config.extra_options:
            params.update(role_config.extra_options)

        return params

    def _stream_request(
        self,
        client: Any,
        callback: Optional[Callable[[str], None]],
        should_stop_check: Optional[Callable[[], bool]],
        role_config: RoleConfig,
        backend,
        context_manager: Optional[IContextManager],
        messages: List[Dict[str, str]]
    ) -> Tuple[str, int, float]:
        """执行流式请求并协调不同处理引擎"""

        if backend.kind == 'ollama':
            result = self._process_ollama_stream(
                client, role_config, backend, messages, callback, should_stop_check
            )
        else:
            result = self._process_openai_stream(
                client, role_config, backend, messages, callback, should_stop_check
            )

        full_response, total_tokens, generation_time = result

        if total_tokens == 0 and full_response:
            total_tokens = estimate_tokens(full_response)
            result = (full_response, total_tokens, generation_time)

        if role_config.enable_history and context_manager:
            context_manager.add_message('user', messages[-1]['content'])
            context_manager.add_message('assistant', full_response)
            logger.debug("已更新历史记录")

        return result

    def _process_ollama_stream(
        self,
        client: Any,
        role_config: RoleConfig,
        backend,
        messages: List[Dict[str, str]],
        callback: Optional[Callable[[str], None]],
        should_stop_check: Optional[Callable[[], bool]]
    ) -> Tuple[str, int, float]:
        """处理 Ollama 原生流响应"""

        params = self._build_request_params(role_config, messages)
        ollama_options = {
            k: v for k, v in params.items()
            if k in ['temperature', 'top_p', 'stop']
        }
        if role_config.max_tokens > 0:
            ollama_options['num_predict'] = role_config.max_tokens

        stream = client.chat(
            model=backend.model or role_config.model,
            messages=messages,
            stream=True,
            options=ollama_options,
            think=role_config.enable_thinking,
            keep_alive=backend.keep_alive,
        )

        full_response, total_tokens, generation_start_time = "", 0, None

        for chunk in stream:
            if should_stop_check and should_stop_check():
                break

            message = getattr(chunk, 'message', None)
            content = getattr(message, 'content', '') if message else ''
            if content:
                full_response += content
                if generation_start_time is None:
                    generation_start_time = time.time()
                if callback:
                    callback(content)

            if getattr(chunk, 'done', False):
                total_tokens = int(getattr(chunk, 'eval_count', 0) or 0)

        generation_time = time.time() - generation_start_time if generation_start_time else 0.0
        return full_response.strip(), total_tokens, generation_time

    def _process_openai_stream(
        self,
        client: Any,
        role_config: RoleConfig,
        backend,
        messages: List[Dict[str, str]],
        callback: Optional[Callable[[str], None]],
        should_stop_check: Optional[Callable[[], bool]]
    ) -> Tuple[str, int, float]:
        """处理 OpenAI 兼容流响应（含内置 llama.cpp）"""

        params = self._build_request_params(role_config, messages)
        params['stream'] = True
        params['model'] = backend.model or role_config.model

        if not role_config.enable_thinking and backend.kind != 'local':
            params['extra_body'] = {"thinking": {"type": "disabled"}}

        stream = client.chat.completions.create(**params)

        full_response, total_tokens, generation_start_time = "", 0, None

        for chunk in stream:
            if should_stop_check and should_stop_check():
                break

            choices = getattr(chunk, 'choices', None) or []
            if choices:
                delta = getattr(choices[0], 'delta', None)
                content = getattr(delta, 'content', '') if delta else ''
                if content:
                    full_response += content
                    if generation_start_time is None:
                        generation_start_time = time.time()
                    if callback:
                        callback(content)

            usage = getattr(chunk, 'usage', None)
            if usage:
                total_tokens = int(getattr(usage, 'completion_tokens', 0) or 0)

        generation_time = time.time() - generation_start_time if generation_start_time else 0.0
        return full_response.strip(), total_tokens, generation_time
