from __future__ import annotations

import os
import sys
import threading
import ctypes
from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication
from deepcat.ui.app_icon import create_app_icon
from deepcat.ui.post_capture_actions import install_smooth_tooltips
from deepcat.utils.crash_reporter import install_crash_reporter, redact_mapping, write_crash_breadcrumb, write_runtime_snapshot

from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND
from deepcat.ui.main_window.window import MainWindow


def run_app(args) -> int:
    install_crash_reporter()
    write_crash_breadcrumb("run_app.start", args=redact_mapping(vars(args)) if hasattr(args, "__dict__") else str(args))
    if os.name == "nt":
        try:
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("DeepCat")
        except Exception:
            pass
    try:
        from PyQt6.QtCore import qInstallMessageHandler
        def _qt_msg_handler(mode, context, message):
            if "QFont::setPointSize: Point size <= 0" in message:
                return
            if "DirectWrite: CreateFontFaceFromHDC() failed" in message and "Fixedsys" in message:
                return
            if "QThreadStorage: entry" in message and "destroyed before end of thread" in message:
                return
            if "must be a top level window" in message:
                return
            write_crash_breadcrumb(
                "qt.message",
                mode=str(mode),
                file=getattr(context, "file", None),
                line=getattr(context, "line", None),
                function=getattr(context, "function", None),
                message=str(message),
                _flush=False,
            )
            import sys
            sys.stderr.write(message + "\n")
        qInstallMessageHandler(_qt_msg_handler)
        write_crash_breadcrumb("run_app.qt_message_handler.installed")
    except Exception:
        write_crash_breadcrumb("run_app.qt_message_handler.failed")
        pass
    from PyQt6.QtNetwork import QLocalServer, QLocalSocket
    app_id = "DeepCat_SingleInstance"
    socket = QLocalSocket()
    socket.connectToServer(app_id)
    if socket.waitForConnected(500):
        write_crash_breadcrumb("run_app.single_instance.activate_existing")
        try:
            socket.write(b"activate")
            socket.flush()
            socket.waitForBytesWritten(500)
        except Exception:
            pass
        socket.disconnectFromServer()
        return 0
    os.environ.setdefault("QT_LOGGING_RULES", "qt.png.warning=false")
    server = QLocalServer()
    if not server.listen(app_id):
        QLocalServer.removeServer(app_id)
        server.listen(app_id)
    write_crash_breadcrumb("run_app.single_instance.server_ready", listening=server.isListening())
    app = QApplication.instance() or QApplication([])
    install_smooth_tooltips(app)
    write_crash_breadcrumb("run_app.qapplication.ready", quit_on_last_window_closed=app.quitOnLastWindowClosed())

    def _background_startup_probes() -> None:
        # mss 探测只为写一条诊断 breadcrumb，不应阻塞启动路径
        try:
            import mss

            with mss.mss() as sct:
                _ = sct.monitors
            write_crash_breadcrumb("run_app.screen_capture_backend.ready")
        except Exception as e:
            write_crash_breadcrumb("run_app.screen_capture_backend.failed", error=repr(e))
        # 自启动命令绑定的是绝对路径，目录移动/解释器更换后会静默失效，启动时自动校正
        try:
            from deepcat.utils.autostart import refresh_autostart_command

            refresh_autostart_command()
        except Exception as e:
            write_crash_breadcrumb("run_app.autostart_refresh.failed", error=repr(e))

    threading.Thread(target=_background_startup_probes, name="startup-probes", daemon=True).start()
    try:
        from PyQt6.QtCore import QTranslator, QLibraryInfo
        translator = QTranslator()
        qt_translations_path = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
        if translator.load("qt_zh_CN", qt_translations_path):
            app.installTranslator(translator)
    except Exception:
        pass
    app.setWindowIcon(create_app_icon())
    app.setQuitOnLastWindowClosed(False)
    try:
        startup_palette = app.palette()
        startup_background = QColor(MAIN_WINDOW_BACKGROUND)
        startup_palette.setColor(QPalette.ColorRole.Window, startup_background)
        startup_palette.setColor(QPalette.ColorRole.Base, startup_background)
        app.setPalette(startup_palette)
    except Exception:
        pass
    win = MainWindow()
    write_crash_breadcrumb("run_app.main_window.created")

    def on_new_connection():
        client = server.nextPendingConnection()
        if client is None:
            return
        def handle_read():
            try:
                data = client.readAll().data()
                write_crash_breadcrumb("run_app.single_instance.message", data=bytes(data).decode("utf-8", "replace"))
                if data == b"activate":
                    win.showNormal()
                    win.raise_()
                    win.activateWindow()
                    QTimer.singleShot(200, win._refresh_hover_cursor)
            except Exception:
                pass
        client.readyRead.connect(handle_read)

    server.newConnection.connect(on_new_connection)
    from deepcat.ocr_worker_client import shutdown_ocr_worker

    app.aboutToQuit.connect(shutdown_ocr_worker)
    app.aboutToQuit.connect(win.cleanup)
    if getattr(args, "format", None):
        win.set_output_format(str(args.format))
    win._prepare_startup_show()
    win.show()
    try:
        QApplication.processEvents()
        win.repaint()
        QApplication.processEvents()
    except Exception:
        pass
    # 首帧已在上方 repaint 中同步光栅化进 backing store，窗口在此之前保持 DWM cloak 隐藏
    # （防止 Windows 先用类背景画刷填出一片无内容的纯色"白屏"）。此处在进入事件循环之前
    # 同步原子揭示窗口，让内容与窗口同一帧完整呈现——不经过定时器排队，冷启动时构造期
    # 注册的 0ms 定时器（本地翻译服务等）与 120ms 延迟启动服务（托盘/热键/自启校正）都
    # 没有机会插在"揭示"之前把窗口冻结在空白状态。
    # 历史方案及其缺陷：
    #   1) "100ms 固定延迟 + 渐进淡入"——固定延迟纯靠猜，且淡入动画会被延迟服务冻结在
    #      半透明状态，表现为"先白屏、后主界面"的割裂感；
    #   2) "singleShot(0) 恢复不透明度"——热启动正常，但冷启动时移除 WS_EX_LAYERED 触发
    #      类画刷重擦客户区，重绘又被拥挤的消息队列推迟，首次打开仍闪白。
    try:
        win._reveal_startup_window()
    except Exception:
        try:
            win.setWindowOpacity(1.0)
        except Exception:
            pass
    # 窗口呈现后刷新一次悬停光标，消除窗口弹出在静止鼠标下方时残留的启动前光标形状（如 I 形）
    QTimer.singleShot(400, win._refresh_hover_cursor)
    write_crash_breadcrumb("run_app.main_window.shown", visible=win.isVisible())
    QTimer.singleShot(120, win._initialize_clipboard_history_background)
    write_runtime_snapshot("run_app.before_exec")
    write_crash_breadcrumb("run_app.exec.enter", _flush=True)
    code = app.exec()
    write_crash_breadcrumb("run_app.exec.returned", code=code, _flush=True)
    # os._exit 跳过 atexit/logging shutdown（避免残留线程导致退出挂死），
    # 这里手动补齐关键清理：刷掉日志缓冲、写最后一条 breadcrumb
    write_crash_breadcrumb("process.exit", code=code, _flush=True)
    try:
        import logging

        logging.shutdown()
    except Exception:
        pass
    try:
        os._exit(code)
    except Exception:
        pass
    return code
