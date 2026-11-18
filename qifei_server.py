# coding: utf-8
"""
语音识别服务端入口

加载模型、提供推理服务。一般由界面自动拉起，不需要手动双击。

排查用：加 --selftest 参数只检查运行环境（推理库、模型目录），
不启动服务，打印结果后退出。
"""

import multiprocessing
import sys
from pathlib import Path

if __name__ == '__main__':
    # PyInstaller + multiprocessing 必需，否则识别子进程会反复启动主程序
    multiprocessing.freeze_support()

    ROOT = Path(__file__).resolve().parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    if '--selftest' in sys.argv:
        import importlib

        failures = 0
        # onnxruntime / sentencepiece 是 SenseVoice 引擎的硬依赖：
        # 少了任一，模型检查通过但识别子进程会直接退出（曾在真机上漏检）
        for name in ('numpy', 'soundfile', 'sherpa_onnx', 'onnxruntime',
                     'sentencepiece', 'websockets'):
            try:
                module = importlib.import_module(name)
                version = getattr(module, '__version__', '?')
                print(f'[ok]   {name} {version}')
            except Exception as exc:
                failures += 1
                print(f'[fail] {name}: {type(exc).__name__}: {exc}')

        try:
            import config_server
            from config_server import ModelPaths
            print(f'[ok]   基目录 {config_server.BASE_DIR}')
            print(f'[ok]   模型目录 {ModelPaths.model_dir}')
            print(f'[ok]   模型类型 {config_server.ServerConfig.model_type}')
            # 依赖齐全不等于模型齐全：只查库会漏掉「没下载模型」这种更常见的故障
            required = {
                'sensevoice': (ModelPaths.sensevoice_encoder,
                               ModelPaths.sensevoice_decoder,
                               ModelPaths.sensevoice_tokenizer),
                'paraformer': (ModelPaths.paraformer_model,
                               ModelPaths.paraformer_tokens,
                               ModelPaths.punc_model_dir),
                'fun_asr_nano': (ModelPaths.fun_asr_nano_gguf_encoder_adaptor,
                                 ModelPaths.fun_asr_nano_gguf_ctc,
                                 ModelPaths.fun_asr_nano_gguf_llm_decode,
                                 ModelPaths.fun_asr_nano_gguf_token),
                'qwen_asr': (ModelPaths.qwen3_asr_gguf_encoder_frontend,
                             ModelPaths.qwen3_asr_gguf_encoder_backend,
                             ModelPaths.qwen3_asr_gguf_llm_decode),
            }.get(config_server.ServerConfig.model_type, ())
            missing = [str(p) for p in required if not Path(p).is_file()]
            if missing:
                failures += 1
                print(f'[fail] 模型文件缺失 {len(missing)} 个：' + '、'.join(
                    Path(p).name for p in missing))
            else:
                print(f'[ok]   模型文件齐全（{config_server.ServerConfig.model_type}）')
        except Exception as exc:
            failures += 1
            print(f'[fail] config_server: {type(exc).__name__}: {exc}')

        print('SELFTEST', 'PASS' if failures == 0 else f'FAIL({failures})')
        sys.exit(0 if failures == 0 else 1)

    from core.server.app import CapsWriterServer
    CapsWriterServer().start()
