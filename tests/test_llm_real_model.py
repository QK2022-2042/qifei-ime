# coding: utf-8
"""真实 .gguf 端到端验证（可选，需要本机确实放了模型）

不依赖 Ollama、不联网。链路：

    进程内 llama.cpp 加载 → 直接生成 → 内嵌 HTTP 服务 → SSE → LLMProcessor

用法：
    python tests/test_llm_real_model.py                 # 自动找 models/llm/*.gguf
    python tests/test_llm_real_model.py <path/to/x.gguf>

没找到模型时直接跳过（退出码 0），不会拖慢常规自检。
常规自检用 python tests/test_llm_kit.py（不需要真实模型）。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

from llmkit import connector, native, server
from llmkit.http_client import OpenAICompatClient


def find_model() -> str:
    if len(sys.argv) > 1:
        return sys.argv[1]
    candidates = (sorted((ROOT / 'models' / 'llm').glob('*.gguf')) +
                  sorted((ROOT / 'models').glob('*.gguf')))
    return str(candidates[0]) if candidates else ''


def main() -> int:
    model = find_model()
    if not model or not Path(model).is_file():
        print('没有找到 .gguf，跳过真实模型验证。')
        print('把模型放到 models/llm/ 下，或运行：python tests/test_llm_real_model.py <模型路径>')
        return 0

    print(f'模型: {model}  ({Path(model).stat().st_size / 1024 ** 3:.2f} GB)', flush=True)
    failures = []

    # 1. 进程内加载
    engine = native.get_engine()
    t0 = time.time()
    ok, message = engine.load(model, n_ctx=2048, n_gpu_layers=0, n_threads=0)
    print(f'[1] 进程内加载: {ok} | {message} | {time.time() - t0:.1f}s', flush=True)
    if not ok:
        return 2
    tmpl = engine._lib.llama_model_chat_template(engine._model, None)
    print(f'    chat template: {tmpl[:180]!r}' if tmpl else
          '    chat template: 无（走 ChatML 兜底）', flush=True)

    # 2. 直接生成
    t0 = time.time()
    pieces, final = [], {}
    for item in engine.generate(
            [{'role': 'user', 'content': '你好，请用一句话介绍你自己。'}],
            max_tokens=32, temperature=0.2):
        if item.get('done'):
            final = item
            break
        pieces.append(str(item.get('text') or ''))
    direct = ''.join(pieces)
    print(f'[2] 直接生成 ({time.time() - t0:.1f}s): {direct!r}', flush=True)
    print(f'    tokens={final.get("tokens")}  error={final.get("error")!r}', flush=True)
    if final.get('error') or not direct:
        failures.append('直接生成失败')

    # 3. 内嵌服务
    server.configure(engine_mode='native')
    srv = server.get_server()
    t0 = time.time()
    ok, message = srv.ensure(model, context=2048)
    print(f'[3] ensure: {ok} | {message} | {time.time() - t0:.1f}s', flush=True)
    print(f'    health={server.is_serving(srv.port)}  '
          f'loaded_model={server.loaded_model(srv.port)!r}  '
          f'engine={srv.engine}', flush=True)
    if not ok:
        return 3

    # 4. SSE 流式
    client = OpenAICompatClient(server.base_url(srv.port), '', 5.0, 600.0)
    t0, text, chunks, err = time.time(), '', 0, ''
    try:
        for chunk in client.chat.completions.create(
                model=engine.model_id,
                messages=[{'role': 'user', 'content': '请回答：测试成功'}],
                stream=True, max_tokens=24, temperature=0.2):
            if chunk.choices:
                text += chunk.choices[0].delta.content or ''
                chunks += 1
    except Exception as exc:
        err = f'{type(exc).__name__}: {exc}'
    print(f'[4] SSE 流式 ({time.time() - t0:.1f}s, {chunks} chunks): {text!r}', flush=True)
    if err:
        print(f'    SSE 异常: {err}', flush=True)
    if err or chunks == 0:
        failures.append('SSE 流式失败')

    # 5. 运行时对话（LLMProcessor，与听写客户端同一条路径）
    try:
        from core.client.llm.llm_client_pool import ClientPool
        from core.client.llm.llm_processor import LLMProcessor
        from core.client.llm.llm_role_config import RoleConfig

        settings = connector.default_settings()
        settings.update({
            'model_path': model, 'model': engine.model_id,
            'llama_port': srv.port, 'context': 2048,
            'engine_mode': 'native', 'unify_roles': True,
        })
        role = RoleConfig(name='自检', module_name='selfcheck', provider='local',
                          api_url=server.base_url(srv.port),
                          model=engine.model_id, enabled=True)
        processor = LLMProcessor(ClientPool())
        processor._settings = settings
        processor._settings_loaded = True
        t0 = time.time()
        answer, tokens, _ = processor.process(
            role, [{'role': 'user', 'content': '说"OK"'}])
        print(f'[5] LLMProcessor 运行时对话 ({time.time() - t0:.1f}s): '
              f'{answer!r}  tokens={tokens}', flush=True)
        if not answer:
            failures.append('运行时对话无输出')
    except Exception as exc:
        print(f'[5] 运行时对话异常: {type(exc).__name__}: {exc}', flush=True)
        failures.append('运行时对话异常')

    combined = direct + text
    if '<asr_text>' in combined or 'language ' in combined:
        print('\n提示：输出里出现 ASR 格式（language .../ <asr_text>），'
              '说明选中的是语音识别专用模型，不适合当对话/润色模型。'
              '换成带 chat template 的通用对话模型（如 *-Instruct GGUF）'
              '才能得到正常回答。', flush=True)

    try:
        server.shutdown()
        print('清理: 内嵌服务与模型已释放', flush=True)
    except Exception as exc:
        print(f'清理异常: {exc}', flush=True)

    if failures:
        print('REAL_MODEL_RESULT=FAILED: ' + '；'.join(failures), flush=True)
        return 4
    print('REAL_MODEL_RESULT=PASS', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
