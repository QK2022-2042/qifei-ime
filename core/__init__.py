# coding: utf-8

from .logger import get_logger, setup_logger

# colorama 只影响控制台配色，没装也不该让整个包导入失败
# （设置界面会导入 llmkit，不希望被这种可选依赖卡住）
try:
    import colorama
    colorama.init()
except Exception:
    pass
