from __future__ import annotations

import os
import time
from typing import Any, Optional
from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import QDialog, QFormLayout, QHBoxLayout, QInputDialog, QLabel, QPushButton, QVBoxLayout, QCheckBox, QLineEdit
from deepcat.ima_client import ImaApiError, ImaClient, ImaCredentials, note_html_to_markdown
from deepcat.ui.main_window.compact import StyledDialog, StyledInputDialog


class _ImaSettingsDialog(StyledDialog):
    def __init__(self, config: dict[str, Any], tab_name: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置IMA")
        self.setMinimumSize(520, 520)
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowType.WindowContextHelpButtonHint)
        self._config = dict(config or {})

        layout = QVBoxLayout(self)
        layout.setSpacing(12)
        layout.setContentsMargins(22, 22, 22, 22)

        title = QLabel(f"当前记事本：{tab_name or '未命名记事本'}")
        title.setStyleSheet("font-weight: 700; color: #0f172a;")
        layout.addWidget(title)

        watermark_style = "color: #94a3b8; font-size: 12px;"

        enabled_row = QHBoxLayout()
        enabled_row.setContentsMargins(0, 0, 0, 0)
        self._enabled = QCheckBox("启用 IMA")
        self._enabled.setChecked(bool(self._config.get("enabled", False)))
        enabled_hint = QLabel("请到IMA App→我→Claw配置获取Client ID和API Key")
        enabled_hint.setWordWrap(True)
        enabled_hint.setStyleSheet(watermark_style)
        enabled_row.addWidget(self._enabled)
        enabled_row.addWidget(enabled_hint, 1)
        layout.addLayout(enabled_row)

        auto_sync_row = QHBoxLayout()
        auto_sync_row.setContentsMargins(0, 0, 0, 0)
        self._auto_sync_enabled = QCheckBox("自动追加")
        self._auto_sync_enabled.setChecked(bool(self._config.get("auto_sync_enabled", False)))
        self._auto_sync_enabled.setToolTip("开启后，停止编辑约 5 秒会自动追加写入绑定的 IMA 笔记。")
        auto_sync_hint = QLabel("因IMA Skills自身限制，只可追加全文到笔记尾部")
        auto_sync_hint.setWordWrap(True)
        auto_sync_hint.setStyleSheet(watermark_style)
        auto_sync_row.addWidget(self._auto_sync_enabled)
        auto_sync_row.addWidget(auto_sync_hint, 1)
        layout.addLayout(auto_sync_row)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        env_client_id = os.environ.get("IMA_OPENAPI_CLIENTID", "") or os.environ.get("IMA_CLIENT_ID", "")
        env_api_key = os.environ.get("IMA_OPENAPI_APIKEY", "") or os.environ.get("IMA_API_KEY", "")
        self._client_id = QLineEdit(str(self._config.get("client_id", "") or env_client_id))
        self._api_key = QLineEdit(str(self._config.get("api_key", "") or env_api_key))
        self._api_key.setEchoMode(QLineEdit.EchoMode.Password)
        self._show_key = QCheckBox("显示")
        self._show_key.toggled.connect(
            lambda checked: self._api_key.setEchoMode(QLineEdit.EchoMode.Normal if checked else QLineEdit.EchoMode.Password)
        )

        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.addWidget(self._api_key, 1)
        key_row.addWidget(self._show_key)

        self._note_folder_id = QLineEdit(str(self._config.get("note_folder_id", "") or ""))
        self._note_folder_name = QLineEdit(str(self._config.get("note_folder_name", "") or ""))
        self._remote_note_id = QLineEdit(str(self._config.get("remote_note_id", "") or ""))
        self._remote_note_title = QLineEdit(str(self._config.get("remote_note_title", "") or ""))
        self._kb_enabled = QCheckBox("启用知识库关联")
        self._kb_enabled.setChecked(bool(self._config.get("knowledge_base_enabled", False)))
        self._knowledge_base_id = QLineEdit(str(self._config.get("knowledge_base_id", "") or ""))
        self._knowledge_base_name = QLineEdit(str(self._config.get("knowledge_base_name", "") or ""))
        self._knowledge_folder_id = QLineEdit(str(self._config.get("knowledge_folder_id", "") or ""))
        self._knowledge_folder_name = QLineEdit(str(self._config.get("knowledge_folder_name", "") or ""))

        for edit in (
            self._client_id,
            self._api_key,
            self._note_folder_id,
            self._note_folder_name,
            self._remote_note_id,
            self._remote_note_title,
            self._knowledge_base_id,
            self._knowledge_base_name,
            self._knowledge_folder_id,
            self._knowledge_folder_name,
        ):
            edit.setMinimumHeight(30)

        form.addRow("Client ID:", self._client_id)
        form.addRow("API Key:", key_row)
        form.addRow("IMA 笔记本 ID:", self._note_folder_id)
        form.addRow("IMA 笔记本名称:", self._note_folder_name)
        form.addRow("绑定笔记 ID:", self._remote_note_id)
        form.addRow("绑定笔记标题:", self._remote_note_title)
        form.addRow("", self._kb_enabled)
        form.addRow("知识库 ID:", self._knowledge_base_id)
        form.addRow("知识库名称:", self._knowledge_base_name)
        form.addRow("知识库文件夹 ID:", self._knowledge_folder_id)
        form.addRow("知识库文件夹名称:", self._knowledge_folder_name)
        layout.addLayout(form)

        picker_layout = QHBoxLayout()
        picker_layout.addStretch(1)
        self._pick_notebook_btn = QPushButton("选择IMA笔记本")
        self._pick_kb_btn = QPushButton("选择知识库")
        for btn in (self._pick_notebook_btn, self._pick_kb_btn):
            btn.setObjectName("BtnSmallSecondary")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(30)
        self._pick_notebook_btn.clicked.connect(self._pick_notebook)
        self._pick_kb_btn.clicked.connect(self._pick_knowledge_base)
        picker_layout.addWidget(self._pick_notebook_btn)
        picker_layout.addWidget(self._pick_kb_btn)
        layout.addLayout(picker_layout)

        hint = QLabel("可仅设置Client ID和API Key，测试连通后便可通过标签页右键菜单把当前记事本追加到IMA笔记、下载IMA笔记或添加到知识库。")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #64748b; font-size: 12px;")
        layout.addWidget(hint)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet("color: #475569;")
        layout.addWidget(self._status)

        btn_layout = QHBoxLayout()
        self._test_btn = QPushButton("测试连接")
        self._save_btn = QPushButton("保存")
        self._cancel_btn = QPushButton("取消")
        for btn in (self._test_btn, self._save_btn, self._cancel_btn):
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setMinimumHeight(30)
        self._test_btn.setObjectName("BtnSmallSecondary")
        self._save_btn.setObjectName("BtnSmallPrimary")
        self._cancel_btn.setObjectName("BtnSmallSecondary")
        self._test_btn.clicked.connect(self._test_connection)
        self._save_btn.clicked.connect(self.accept)
        self._cancel_btn.clicked.connect(self.reject)
        btn_layout.addWidget(self._test_btn)
        btn_layout.addStretch(1)
        btn_layout.addWidget(self._save_btn)
        btn_layout.addWidget(self._cancel_btn)
        layout.addLayout(btn_layout)

    def config(self) -> dict[str, Any]:
        merged = dict(self._config)
        merged.update(
            {
                "enabled": bool(self._enabled.isChecked()),
                "auto_sync_enabled": bool(self._auto_sync_enabled.isChecked()),
                "client_id": self._client_id.text().strip(),
                "api_key": self._api_key.text().strip(),
                "note_folder_id": self._note_folder_id.text().strip(),
                "note_folder_name": self._note_folder_name.text().strip(),
                "remote_note_id": self._remote_note_id.text().strip(),
                "remote_note_title": self._remote_note_title.text().strip(),
                "knowledge_base_enabled": bool(self._kb_enabled.isChecked()),
                "knowledge_base_id": self._knowledge_base_id.text().strip(),
                "knowledge_base_name": self._knowledge_base_name.text().strip(),
                "knowledge_folder_id": self._knowledge_folder_id.text().strip(),
                "knowledge_folder_name": self._knowledge_folder_name.text().strip(),
            }
        )
        return merged

    def _test_connection(self) -> None:
        try:
            client = self._client(timeout=12.0)
            notebooks = client.list_notebooks(limit=1).get("data") or {}
            knowledge_bases = client.get_addable_knowledge_bases(limit=1).get("data") or {}
            note_count = len(notebooks.get("note_folder_infos") or [])
            kb_count = len(knowledge_bases.get("addable_knowledge_base_list") or [])
            self._status.setStyleSheet("color: #166534;")
            self._status.setText(f"连接成功：可读取笔记本 {note_count} 项，可添加知识库 {kb_count} 项。")
        except Exception as e:
            self._status.setStyleSheet("color: #b91c1c;")
            self._status.setText(f"连接失败：{e}")

    def _client(self, *, timeout: float = 15.0) -> ImaClient:
        cfg = self.config()
        return ImaClient(ImaCredentials(cfg["client_id"], cfg["api_key"]), timeout=timeout)

    def _notify(self, title: str, message: str, duration_ms: int = 2600) -> None:
        targets = [self.parent(), self.window()]
        for target in targets:
            notifier = getattr(target, "_send_tray_notification", None)
            if callable(notifier):
                notifier(str(title or "IMA"), str(message or ""), int(duration_ms))
                return
        self._status.setStyleSheet("color: #b91c1c;")
        self._status.setText(str(message or ""))

    def _pick_notebook(self) -> None:
        try:
            infos = (self._client().list_notebooks(limit=20).get("data") or {}).get("note_folder_infos") or []
            choices: list[tuple[str, str, str]] = []
            for item in infos:
                if not isinstance(item, dict):
                    continue
                folder_id = str(item.get("folder_id", "") or "").strip()
                name = str(item.get("name", "") or "").strip()
                if folder_id and name:
                    choices.append((f"{name} ({folder_id})", folder_id, name))
            if not choices:
                self._notify("IMA", "未读取到可用的 IMA 笔记本。")
                return
            labels = [label for label, _, _ in choices]
            selected, ok = StyledInputDialog.get_item(self, "选择IMA笔记本", "请选择目标笔记本:", labels, 0, False)
            if not ok:
                return
            for label, folder_id, name in choices:
                if label == selected:
                    self._note_folder_id.setText(folder_id)
                    self._note_folder_name.setText(name)
                    return
        except Exception as e:
            self._notify("IMA 操作失败", f"读取 IMA 笔记本失败：{e}", 3600)

    def _pick_knowledge_base(self) -> None:
        try:
            infos = (self._client().get_addable_knowledge_bases(limit=20).get("data") or {}).get("addable_knowledge_base_list") or []
            choices: list[tuple[str, str, str]] = []
            for item in infos:
                if not isinstance(item, dict):
                    continue
                kb_id = str(item.get("id", "") or "").strip()
                name = str(item.get("name", "") or "").strip()
                if kb_id and name:
                    choices.append((f"{name} ({kb_id})", kb_id, name))
            if not choices:
                self._notify("IMA", "未读取到可添加的知识库。")
                return
            labels = [label for label, _, _ in choices]
            selected, ok = StyledInputDialog.get_item(self, "选择知识库", "请选择目标知识库:", labels, 0, False)
            if not ok:
                return
            for label, kb_id, name in choices:
                if label == selected:
                    self._knowledge_base_id.setText(kb_id)
                    self._knowledge_base_name.setText(name)
                    self._kb_enabled.setChecked(True)
                    return
        except Exception as e:
            self._notify("IMA 操作失败", f"读取 IMA 知识库失败：{e}", 3600)

    def _ensure_within_screen(self) -> None:
        from PyQt6.QtGui import QGuiApplication
        screen = QGuiApplication.screenAt(self.geometry().center()) or QGuiApplication.primaryScreen()
        if not screen:
            return
        screen_geom = screen.availableGeometry()

        geom = self.geometry()
        x = geom.x()
        y = geom.y()
        w = geom.width()
        h = geom.height()

        if x < screen_geom.left():
            x = screen_geom.left()
        elif x + w > screen_geom.right():
            x = screen_geom.right() - w

        if y < screen_geom.top():
            y = screen_geom.top()
        elif y + h > screen_geom.bottom():
            y = screen_geom.bottom() - h

        if x != geom.x() or y != geom.y():
            self.move(x, y)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._ensure_within_screen()


class _ImaSyncWorker(QThread):
    finished = pyqtSignal(bool, str, dict)

    def __init__(self, action: str, config: dict[str, Any], tab_name: str, note_html: str, *, extra: Optional[dict[str, Any]] = None) -> None:
        super().__init__()
        self._action = str(action or "")
        self._config = dict(config or {})
        self._tab_name = str(tab_name or "未命名笔记")
        self._note_html = str(note_html or "")
        self._extra = dict(extra or {})

    def run(self) -> None:
        try:
            client = ImaClient(
                ImaCredentials(
                    str(self._config.get("client_id", "") or ""),
                    str(self._config.get("api_key", "") or ""),
                ),
                timeout=30.0,
            )
            if self._action in {"sync_note", "append_note"}:
                markdown = note_html_to_markdown(self._tab_name, self._note_html)
                remote_note_id = str(self._config.get("remote_note_id", "") or "").strip()
                if remote_note_id:
                    import datetime
                    now = datetime.datetime.now()
                    time_str = f"{now.hour:02d}点{now.minute:02d}分{now.second:02d}秒"
                    time_line = f"------------------------------{time_str}------------------------------"
                    note_id = client.append_note(remote_note_id, f"\n\n{time_line}\n---\n\n" + markdown)
                    message = "已追加到 IMA 笔记。"
                else:
                    note_id = client.import_note(
                        markdown,
                        folder_id=str(self._config.get("note_folder_id", "") or ""),
                        folder_name=str(self._config.get("note_folder_name", "") or ""),
                    )
                    message = "已新建 IMA 笔记并完成追加。"
                result = dict(self._config)
                result["remote_note_id"] = note_id
                result["remote_note_title"] = self._tab_name
                result["last_sync_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self.finished.emit(True, message, result)
                return

            if self._action == "download_note":
                remote_note_id = str(self._config.get("remote_note_id", "") or "").strip()
                if not remote_note_id:
                    raise ImaApiError("请先在“设置IMA”中填写绑定笔记 ID。")
                content = client.get_note_content(remote_note_id, target_content_format=1)
                result = dict(self._config)
                result["last_sync_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                result["_downloaded_note_content"] = content
                self.finished.emit(True, "已下载 IMA 笔记并覆盖当前笔记本。", result)
                return

            if self._action == "add_note_to_kb":
                kb_id = str(self._config.get("knowledge_base_id", "") or "").strip()
                remote_note_id = str(self._config.get("remote_note_id", "") or "").strip()
                if not remote_note_id:
                    markdown = note_html_to_markdown(self._tab_name, self._note_html)
                    remote_note_id = client.import_note(
                        markdown,
                        folder_id=str(self._config.get("note_folder_id", "") or ""),
                        folder_name=str(self._config.get("note_folder_name", "") or ""),
                    )
                media_id = client.add_note_to_knowledge_base(
                    knowledge_base_id=kb_id,
                    note_id=remote_note_id,
                    title=self._tab_name,
                    folder_id=str(self._config.get("knowledge_folder_id", "") or ""),
                )
                result = dict(self._config)
                result["remote_note_id"] = remote_note_id
                result["remote_note_title"] = self._tab_name
                result["knowledge_media_id"] = media_id
                result["last_sync_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self.finished.emit(True, "已将 IMA 笔记添加到知识库。", result)
                return

            if self._action == "import_url_to_kb":
                url = str(self._extra.get("url", "") or "").strip()
                kb_id = str(self._config.get("knowledge_base_id", "") or "").strip()
                client.import_urls(
                    kb_id,
                    [url],
                    folder_id=str(self._config.get("knowledge_folder_id", "") or ""),
                )
                result = dict(self._config)
                result["last_sync_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self.finished.emit(True, "已导入网址到 IMA 知识库。", result)
                return

            if self._action == "search_kb":
                query = str(self._extra.get("query", "") or "").strip()
                kb_id = str(self._config.get("knowledge_base_id", "") or "").strip()
                data = client.search_knowledge(kb_id, query).get("data") or {}
                items = data.get("info_list") or []
                titles: list[str] = []
                for item in items[:5]:
                    if isinstance(item, dict):
                        title = str(item.get("title", "") or "").strip()
                        if title:
                            titles.append(title)
                summary = "未找到匹配内容。" if not titles else "搜索结果：\n" + "\n".join(f"{i + 1}. {title}" for i, title in enumerate(titles))
                self.finished.emit(True, summary, dict(self._config))
                return

            raise ImaApiError("未知 IMA 操作")
        except Exception as e:
            self.finished.emit(False, str(e), dict(self._config))
