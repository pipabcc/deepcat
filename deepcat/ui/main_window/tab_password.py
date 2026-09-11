from __future__ import annotations

import hashlib
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QMessageBox, QPushButton, QVBoxLayout, QLineEdit


class _TabPasswordSetDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置标签页密码锁")
        self.setFixedSize(340, 250)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {MAIN_WINDOW_BACKGROUND};
            }}
            QLabel {{
                color: #0f172a;
                font-size: 13px;
            }}
            QLineEdit {{
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                padding: 0 8px;
                color: #0f172a;
                font-size: 13px;
            }}
            QLineEdit:hover, QLineEdit:focus {{
                border-color: #94a3b8;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(24, 24, 24, 24)

        layout.addWidget(QLabel("请输入新密码:"))
        self._pwd1 = QLineEdit()
        self._pwd1.setEchoMode(QLineEdit.EchoMode.Password)
        self._pwd1.setFixedHeight(28)
        layout.addWidget(self._pwd1)

        layout.addWidget(QLabel("请再次确认密码:"))
        self._pwd2 = QLineEdit()
        self._pwd2.setEchoMode(QLineEdit.EchoMode.Password)
        self._pwd2.setFixedHeight(28)
        layout.addWidget(self._pwd2)

        # 增加间距给按钮更多空间
        layout.addSpacing(12)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 5, 0, 0)
        btn_layout.addStretch(1)

        ok_btn = QPushButton("确定")
        ok_btn.setObjectName("PwdOkBtn")
        ok_btn.setFixedWidth(75)
        ok_btn.setFixedHeight(30)
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.setStyleSheet("""
            #PwdOkBtn {
                background-color: #1e293b;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 13px;
                font-weight: bold;
            }
            #PwdOkBtn:hover {
                background-color: #334155;
            }
            #PwdOkBtn:pressed {
                background-color: #0f172a;
            }
        """)
        ok_btn.clicked.connect(self._on_ok)

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("PwdCancelBtn")
        cancel_btn.setFixedWidth(75)
        cancel_btn.setFixedHeight(30)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            #PwdCancelBtn {
                background-color: #ffffff;
                color: #1e293b;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                font-size: 13px;
            }
            #PwdCancelBtn:hover {
                background-color: #f8fafc;
                border-color: #94a3b8;
            }
            #PwdCancelBtn:pressed {
                background-color: #f1f5f9;
                border-color: #64748b;
            }
        """)
        cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(ok_btn)
        btn_layout.addSpacing(15)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addStretch(1)

        layout.addLayout(btn_layout)
        self._password = ""

    def _on_ok(self):
        p1 = self._pwd1.text().strip()
        p2 = self._pwd2.text().strip()
        if not p1:
            QMessageBox.warning(self, "错误", "密码不能为空，且不能全为空格。")
            return
        if p1 != p2:
            QMessageBox.warning(self, "错误", "两次输入的密码不一致，请重新输入。")
            return
        self._password = p1
        self.accept()

    def password(self) -> str:
        return self._password

    def _apply_caption_color(self) -> None:
        try:
            import os
            import ctypes
            if os.name != "nt":
                return
            from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND_COLORREF
            dwmapi = ctypes.windll.dwmapi
            hwnd = int(self.winId())
            DWMWA_CAPTION_COLOR = 35
            color = ctypes.c_uint(MAIN_WINDOW_BACKGROUND_COLORREF)
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(DWMWA_CAPTION_COLOR),
                ctypes.byref(color),
                ctypes.sizeof(color),
            )
        except Exception:
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_caption_color()


class _TabPasswordVerifyDialog(QDialog):
    def __init__(self, hashed_pwd: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("输入标签页密码以解锁")
        self.setFixedSize(340, 180)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)

        from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND
        self.setStyleSheet(f"""
            QDialog {{
                background-color: {MAIN_WINDOW_BACKGROUND};
            }}
            QLabel {{
                color: #0f172a;
                font-size: 13px;
            }}
            QLineEdit {{
                background: #ffffff;
                border: 1px solid #e2e8f0;
                border-radius: 6px;
                padding: 0 8px;
                color: #0f172a;
                font-size: 13px;
            }}
            QLineEdit:hover, QLineEdit:focus {{
                border-color: #94a3b8;
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.setContentsMargins(24, 24, 24, 24)

        layout.addWidget(QLabel("该标签页已启用密码锁保护，请输入密码:"))
        self._pwd = QLineEdit()
        self._pwd.setEchoMode(QLineEdit.EchoMode.Password)
        self._pwd.setFixedHeight(28)
        self._pwd.returnPressed.connect(self._on_ok)
        layout.addWidget(self._pwd)

        # 增加间距给按钮更多空间
        layout.addSpacing(12)

        btn_layout = QHBoxLayout()
        btn_layout.setContentsMargins(0, 5, 0, 0)
        btn_layout.addStretch(1)

        ok_btn = QPushButton("确定")
        ok_btn.setObjectName("PwdOkBtn")
        ok_btn.setFixedWidth(75)
        ok_btn.setFixedHeight(30)
        ok_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        ok_btn.setStyleSheet("""
            #PwdOkBtn {
                background-color: #1e293b;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 13px;
                font-weight: bold;
            }
            #PwdOkBtn:hover {
                background-color: #334155;
            }
            #PwdOkBtn:pressed {
                background-color: #0f172a;
            }
        """)
        ok_btn.clicked.connect(self._on_ok)

        cancel_btn = QPushButton("取消")
        cancel_btn.setObjectName("PwdCancelBtn")
        cancel_btn.setFixedWidth(75)
        cancel_btn.setFixedHeight(30)
        cancel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        cancel_btn.setStyleSheet("""
            #PwdCancelBtn {
                background-color: #ffffff;
                color: #1e293b;
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                font-size: 13px;
            }
            #PwdCancelBtn:hover {
                background-color: #f8fafc;
                border-color: #94a3b8;
            }
            #PwdCancelBtn:pressed {
                background-color: #f1f5f9;
                border-color: #64748b;
            }
        """)
        cancel_btn.clicked.connect(self.reject)

        btn_layout.addWidget(ok_btn)
        btn_layout.addSpacing(15)
        btn_layout.addWidget(cancel_btn)
        btn_layout.addStretch(1)

        layout.addLayout(btn_layout)
        self._hashed_pwd = hashed_pwd

    def _on_ok(self):
        pwd = self._pwd.text().strip()
        import hashlib
        hashed = hashlib.sha256(pwd.encode("utf-8")).hexdigest()
        if hashed != self._hashed_pwd:
            QMessageBox.warning(self, "验证失败", "密码不正确，请重新输入。")
            return
        self.accept()

    def _apply_caption_color(self) -> None:
        try:
            import os
            import ctypes
            if os.name != "nt":
                return
            from deepcat.ui.main_window._shared import MAIN_WINDOW_BACKGROUND_COLORREF
            dwmapi = ctypes.windll.dwmapi
            hwnd = int(self.winId())
            DWMWA_CAPTION_COLOR = 35
            color = ctypes.c_uint(MAIN_WINDOW_BACKGROUND_COLORREF)
            dwmapi.DwmSetWindowAttribute(
                ctypes.c_void_p(hwnd),
                ctypes.c_uint(DWMWA_CAPTION_COLOR),
                ctypes.byref(color),
                ctypes.sizeof(color),
            )
        except Exception:
            pass

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._apply_caption_color()
