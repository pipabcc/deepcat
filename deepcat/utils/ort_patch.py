# -*- coding: utf-8 -*-
"""
onnxruntime 延迟打补丁工具模块。
防止在程序冷启动时立即导入 onnxruntime 而产生 150MB+ 的内存开销。
在首次初始化 OCR 引擎或者执行自检时，才会按需调用本模块。
"""

import sys

_patched = False

def patch_onnxruntime() -> None:
    """对 onnxruntime 进行限制 CPU 并行线程数的 monkey-patch。
    如果尚未导入 onnxruntime，则动态导入并修改其 SessionOptions 初始化逻辑。
    """
    global _patched
    if _patched:
        return
    _patched = True
    try:
        import onnxruntime as ort

        _orig_session_options_init = ort.SessionOptions.__init__

        def _patched_session_options_init(self, *args, **kwargs):
            _orig_session_options_init(self, *args, **kwargs)
            # 限制单会话内部并行计算线程为 2，防止吃满所有物理核；限制跨算子并行线程为 1
            self.intra_op_num_threads = 2
            self.inter_op_num_threads = 1
            self.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL

        ort.SessionOptions.__init__ = _patched_session_options_init
    except Exception:
        pass
