from __future__ import annotations

import os
# 彻底屏蔽 Qt 底层 QPA 剪贴板获取重试所产生的终端诊断噪音警告 (qt.qpa.mime: Retrying to obtain clipboard)
os.environ["QT_LOGGING_RULES"] = "qt.qpa.mime=false"

# onnxruntime 的 CPU 并行核心限制已移入 deepcat.utils.ort_patch 中，按需惰性触发。

import argparse
import sys
import threading
import traceback
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from deepcat.config import Config
from deepcat.utils.crash_reporter import (
    install_crash_reporter,
    redact_argv,
    write_crash_breadcrumb,
    write_crash_exception,
    write_runtime_snapshot,
)
from deepcat.utils.logger import get_logger


logger = get_logger()


def ensure_deps() -> bool:
    try:
        import numpy as _  # noqa: F401
        return True
    except Exception:
        exe = sys.executable or "python"
        msg = (
            "缺少依赖 numpy，当前 Python 环境未安装。\n\n"
            f"请用同一个解释器安装：\n  {exe} -m pip install -r requirements.txt\n\n"
            "如果你用的是 Python 3.13，也可以：\n  py -3.13 -m pip install -r requirements.txt\n"
        )
        print(msg, file=sys.stderr)
        try:
            logger.error(msg)
        except Exception:
            pass
        return False


def install_excepthooks() -> None:
    _in_excepthook = False

    def handle_exception(exc_type, exc, tb):
        nonlocal _in_excepthook
        if _in_excepthook:
            sys.__excepthook__(exc_type, exc, tb)
            return
        _in_excepthook = True
        try:
            write_crash_exception("sys.excepthook", exc_type, exc, tb)
            write_runtime_snapshot("sys.excepthook", include_stacks=True)
            logger.error("未捕获异常: %s", repr(exc))
            logger.error("".join(traceback.format_exception(exc_type, exc, tb)))
        except Exception:
            pass
        finally:
            _in_excepthook = False
            sys.__excepthook__(exc_type, exc, tb)

    sys.excepthook = handle_exception

    if hasattr(threading, "excepthook"):
        _in_thread_excepthook = False

        def handle_thread_exception(args):
            nonlocal _in_thread_excepthook
            if _in_thread_excepthook:
                threading.__excepthook__(args)
                return
            _in_thread_excepthook = True
            try:
                write_crash_exception("threading.excepthook", args.exc_type, args.exc_value, args.exc_traceback)
                write_runtime_snapshot("threading.excepthook", include_stacks=True)
                logger.error("线程未捕获异常: %s", repr(args.exc_value))
                logger.error("".join(traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)))
            except Exception:
                pass
            finally:
                _in_thread_excepthook = False
                threading.__excepthook__(args)

        threading.excepthook = handle_thread_exception


def parse_region(s: str) -> tuple[int, int, int, int]:
    parts = [p.strip() for p in s.split(",") if p.strip()]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("region 需要 4 个整数：left,top,width,height")
    try:
        left, top, width, height = [int(x) for x in parts]
    except Exception as e:
        raise argparse.ArgumentTypeError("region 需要 4 个整数") from e
    if width <= 0 or height <= 0:
        raise argparse.ArgumentTypeError("region 的 width/height 必须 > 0")
    return (left, top, width, height)


def countdown(seconds: int) -> None:
    seconds = max(0, int(seconds))
    for i in range(seconds, 0, -1):
        print(f"{i}...", flush=True)
        time.sleep(1)


def run_cli(args: argparse.Namespace) -> int:
    if not ensure_deps():
        return 2
    from deepcat.core.capturer import capture_screen
    from deepcat.core.detector import detect_fixed_regions, is_at_bottom, mean_abs_diff
    from deepcat.core.scroller import scroll_down
    from deepcat.core.stitcher import stitch_images
    from deepcat.input.hotkey_listener import HotkeyListener
    from deepcat.output.clipboard import copy_image_to_clipboard
    from deepcat.output.saver import save_image

    cfg = Config()
    cfg.START_DELAY = int(args.start_delay)
    cfg.SCROLL_DELAY = float(args.scroll_delay)
    cfg.SCROLL_AMOUNT = int(args.scroll_amount)
    cfg.MAX_FRAMES = int(args.max_frames)
    cfg.OUTPUT_FORMAT = str(args.format or "png").lower()
    cfg.JPG_QUALITY = int(args.quality)
    cfg.OUTPUT_DIR = str(args.output_dir) if args.output_dir else None
    cfg.CAPTURE_REGION = args.region

    print("按 Enter 开始截图（倒计时后开始滚动截取）")
    input()

    print(f"{cfg.START_DELAY} 秒后开始，请切换到目标窗口...")
    countdown(cfg.START_DELAY)

    listener = HotkeyListener()
    listener.start()
    print("开始截取... 按 ESC 停止，按 Space 暂停/继续")

    frames = []
    prev_frame = None
    bottom_count = 0

    max_frames = int(cfg.MAX_FRAMES)
    i = 0
    try:
        while True:
            if max_frames > 0 and i >= max_frames:
                print("已达到最大帧数，自动停止")
                break
            i += 1
            if listener.should_stop():
                print("用户手动停止")
                break

            listener.wait_if_paused()
            if listener.should_stop():
                print("用户手动停止")
                break

            frame = capture_screen(cfg.CAPTURE_REGION)

            if prev_frame is not None:
                if is_at_bottom(prev_frame, frame, diff_mean_threshold=cfg.BOTTOM_DIFF_MEAN_THRESHOLD):
                    bottom_count += 1
                    if bottom_count >= cfg.BOTTOM_CONFIRM_COUNT:
                        print("已到达底部，自动停止")
                        frames.append(frame)
                        break
                else:
                    bottom_count = 0

            frames.append(frame)
            prev_frame = frame
            print(f"  已截取第 {len(frames)} 帧", flush=True)

            scroll_down(cfg.SCROLL_AMOUNT, cfg.SCROLL_DELAY, method=cfg.SCROLL_METHOD)
            if prev_frame is not None:
                change_threshold = max(1.2, float(cfg.BOTTOM_DIFF_MEAN_THRESHOLD) * 1.5)
                checks = 6 if float(cfg.SCROLL_DELAY) <= 0.10 else 10
                for _ in range(int(checks)):
                    if listener.should_stop():
                        break
                    time.sleep(0.02)
                    probe = capture_screen(cfg.CAPTURE_REGION)
                    if mean_abs_diff(prev_frame, probe) >= change_threshold:
                        break
    finally:
        listener.cleanup()

    if len(frames) == 0:
        print("截取帧数不足，退出")
        return 1

    if len(frames) == 1:
        filepath = save_image(frames[0], cfg.OUTPUT_DIR, cfg.OUTPUT_FORMAT, cfg.JPG_QUALITY)
        print(f"截图已保存: {filepath}")
        return 0

    print(f"共截取 {len(frames)} 帧，正在拼接...")

    fixed = None
    if cfg.DETECT_FIXED_HEADER or cfg.DETECT_FIXED_FOOTER:
        fixed_all = detect_fixed_regions(frames, sample_count=3)
        fixed = fixed_all
        if not cfg.DETECT_FIXED_HEADER:
            fixed = type(fixed_all)(0, fixed_all.footer_height)
        if not cfg.DETECT_FIXED_FOOTER:
            fixed = type(fixed_all)(fixed.header_height, 0)

    result = stitch_images(frames, strip_height=cfg.MATCH_STRIP_HEIGHT, min_confidence=cfg.MATCH_CONFIDENCE, fixed_regions=fixed)
    filepath = save_image(result, cfg.OUTPUT_DIR, cfg.OUTPUT_FORMAT, cfg.JPG_QUALITY)
    print(f"长截图已保存: {filepath}")

    if cfg.AUTO_COPY_CLIPBOARD:
        ok = copy_image_to_clipboard(result)
        if ok:
            print("已复制到剪贴板")
        else:
            print("复制到剪贴板失败（非 Windows 或缺少 pywin32）")

    return 0


def run_gui(args: argparse.Namespace) -> int:
    try:
        write_crash_breadcrumb("run_gui.import_main_window.start")
        from deepcat.ui.main_window import run_app
    except Exception as e:
        write_crash_breadcrumb("run_gui.import_main_window.failed", error=repr(e), _flush=True)
        logger.error("GUI 启动失败：%s", e)
        print("无法启动 GUI：请确认已安装 PyQt6", file=sys.stderr)
        return 2

    write_crash_breadcrumb("run_gui.run_app.start")
    return run_app(args)


def run_translate_service(args: argparse.Namespace) -> int:
    from deepcat.translation_server import DEFAULT_SERVICE_API_KEY, run_translation_server

    raw_api_key = getattr(args, "translate_api_key", DEFAULT_SERVICE_API_KEY)
    api_key = DEFAULT_SERVICE_API_KEY if raw_api_key is None else str(raw_api_key)
    return run_translation_server(
        host=str(getattr(args, "translate_host", "") or "127.0.0.1"),
        port=int(getattr(args, "translate_port", 11888)),
        api_key=api_key,
    )


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="DeepCat 多功能截图工具（框选截屏 + 滚动截屏 + OCR + 翻译）")
    p.add_argument("--gui", action="store_true", help="启动 PyQt6 图形界面")
    p.add_argument("--serve-translate", action="store_true", help="启动 DeepCat 本地 API HTTP 服务")
    p.add_argument("--translate-host", default="127.0.0.1", help="本地 API 服务监听地址")
    p.add_argument("--translate-port", type=int, default=11888, help="本地 API 服务端口")
    p.add_argument("--translate-api-key", default="sk-deepcat-local", help="本地 API 服务 API key；留空则不校验")
    p.add_argument("--region", type=parse_region, default=None, help="截取区域 left,top,width,height（默认全屏）")
    p.add_argument("--format", default=None, choices=["png", "jpg", "pdf"], help="输出格式")
    p.add_argument("--quality", type=int, default=95, help="JPG 质量 1-100")
    p.add_argument("--output-dir", default=None, help="输出目录（默认程序目录）")
    p.add_argument("--start-delay", type=int, default=3, help="开始前倒计时秒数")
    p.add_argument("--scroll-delay", type=float, default=0.06, help="滚动后等待秒数")
    p.add_argument("--scroll-amount", type=int, default=-24, help="每次滚轮步数（负=向下）")
    p.add_argument("--max-frames", type=int, default=0, help="最大截取帧数（0=不限制）")
    return p


def main(argv: Optional[list[str]] = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    # Nuitka 使用根目录 main.py 作为入口，不经过 packaging/entry_gui.py，
    # 因此必须在 argparse 和 GUI 导入前完成 OCR Worker 分流。
    if argv and argv[0] == "--deepcat-ocr-worker":
        from deepcat.ocr_worker import main as ocr_worker_main

        return ocr_worker_main(argv[1:])
    install_crash_reporter()
    write_crash_breadcrumb("main.enter", argv=redact_argv(argv), _flush=True)
    install_excepthooks()
    args = build_parser().parse_args(argv)
    write_crash_breadcrumb("main.args_parsed", gui=bool(args.gui), serve_translate=bool(args.serve_translate), _flush=True)
    if args.serve_translate:
        return run_translate_service(args)
    if args.gui:
        return run_gui(args)
    return run_cli(args)


if __name__ == "__main__":
    raise SystemExit(main())
