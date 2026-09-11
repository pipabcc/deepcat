import os
import sys
from pathlib import Path

# 彻底屏蔽 Qt 底层 QPA 剪贴板获取重试所产生的终端诊断噪音警告 (qt.qpa.mime: Retrying to obtain clipboard)
os.environ["QT_LOGGING_RULES"] = "qt.qpa.mime=false"

# Add the project root directory to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent))

# onnxruntime 的 CPU 核心线程占用限制已移动到 deepcat.utils.ort_patch 模块中，按需在 OCR 加载时惰性触发。

from deepcat.main import main

if __name__ == "__main__":
    # Default to GUI mode if no arguments provided
    if len(sys.argv) == 1:
        sys.argv.append("--gui")
    sys.exit(main())
