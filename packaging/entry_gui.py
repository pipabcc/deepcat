import sys
import os

# 打包为 windowed exe (console=False) 时 sys.stdout / sys.stderr 可能为 None。
# 部分第三方库（pynput、PyQt6 等）在异常路径上向 stderr 写入，会触发 AttributeError，
# 导致全局键盘钩子线程被杀死且无任何告警。这里兜底为 io.StringIO，避免静默崩溃。
if getattr(sys, "frozen", False):
    try:
        if sys.stdout is None:
            import io
            sys.stdout = io.StringIO()
        if sys.stderr is None:
            import io
            sys.stderr = io.StringIO()
    except Exception:
        pass

# OCR 使用按需隔离进程。必须在导入 GUI 和其他大型依赖之前分流，
# 确保 ONNX Runtime 先于 Qt 初始化；识别成功后父进程会立即回收 Worker。
if getattr(sys, "frozen", False) and len(sys.argv) > 1 and sys.argv[1] == "--deepcat-ocr-worker":
    from deepcat.ocr_worker import main as ocr_worker_main

    raise SystemExit(ocr_worker_main(sys.argv[2:]))

# comtypes 默认会在 %TEMP%\gen_py 目录动态生成 COM 接口包装代码。
# 打包后该目录可能无写权限、或被多实例并发抢占，导致 UIA 后端首次调用即抛异常。
# 在导入 pywinauto/comtypes 之前禁用动态生成，强制使用运行时反射。
try:
    import comtypes
    import comtypes.client
    import comtypes.gen  # noqa: F401  确保模块已加载以便设置属性
    # 禁用动态代码生成，强制 comtypes 在运行时通过 IDispatch 反射调用
    comtypes.client.gen_dir = None
except Exception:
    pass

# 打包依赖统一由 deepcat_gui.spec 的 hiddenimports、datas 和 binaries 收集。
# 入口不再为了触发 hook 而提前导入大型模块，避免软件启动时无条件占用内存。

from deepcat.main import main


if __name__ == "__main__":
    raise SystemExit(main(["--gui"]))
