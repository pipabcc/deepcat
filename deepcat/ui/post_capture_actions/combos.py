from __future__ import annotations

from PyQt6.QtCore import QPoint, QPointF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QPainter, QPen
from PyQt6.QtWidgets import QPushButton, QWidget, QComboBox, QStyle, QStyleOptionComboBox
from deepcat.settings_store import infer_translator_model_provider, load_settings, normalize_translator_provider, normalize_translator_settings
from deepcat.ui.popup_behavior import POPUP_EXACT_WIDTH_PROPERTY, find_parent_with_attr
from PyQt6.QtWidgets import QPushButton, QStyle
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPainter, QPen, QColor
from PyQt6.QtCore import QPointF, QPoint, Qt
from PyQt6.QtWidgets import QWidget, QPushButton

from deepcat.ui.post_capture_actions._shared import logger
from deepcat.ui.post_capture_actions.model_menus import OcrGenericMenuPopup, _OcrModelMenuPopup


class CollapseArrowButton(QPushButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._collapsed = False
        self._hovered = False
        self.setFixedSize(20, 20)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setStyleSheet("QPushButton { background: transparent; border: none; }")

    def enterEvent(self, event) -> None:
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def set_collapsed(self, collapsed: bool) -> None:
        self._collapsed = collapsed
        self.update()

    def paintEvent(self, event) -> None:
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        # 悬停时稍微加粗，常态线条较细腻
        width = 2.0 if self._hovered else 1.3
        color = QColor("#1e293b") if self._hovered else QColor("#4b5563")
        painter.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))

        cx = 10
        cy = 10
        if self._collapsed:
            # 收起状态，小箭头指向右
            painter.drawLine(QPointF(8.0, 6.0), QPointF(12.0, 10.0))
            painter.drawLine(QPointF(12.0, 10.0), QPointF(8.0, 14.0))
        else:
            # 展开状态，小箭头指向左
            painter.drawLine(QPointF(12.0, 6.0), QPointF(8.0, 10.0))
            painter.drawLine(QPointF(8.0, 10.0), QPointF(12.0, 14.0))

        painter.end()


class UpwardComboBox(QComboBox):
    """下拉列表菜单向上展开的 QComboBox"""

    def paintEvent(self, event) -> None:
        if not bool(self.property("centerText")):
            super().paintEvent(event)
            return
        painter = QPainter(self)
        opt = QStyleOptionComboBox()
        self.initStyleOption(opt)
        style = self.style()
        style.drawComplexControl(QStyle.ComplexControl.CC_ComboBox, opt, painter, self)
        text_rect = style.subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            opt,
            QStyle.SubControl.SC_ComboBoxEditField,
            self,
        )
        painter.setPen(QColor("#111827") if self.isEnabled() else QColor("#94a3b8"))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, opt.currentText)
        painter.end()

    def showPopup(self) -> None:
        items = []
        for i in range(self.count()):
            text = self.itemText(i)

            def select_item(_checked=False, idx=i):
                self.setCurrentIndex(idx)
                self.activated.emit(idx)

            items.append((text, select_item, True))

        exact_width = bool(self.property(POPUP_EXACT_WIDTH_PROPERTY))
        popup = OcrGenericMenuPopup(
            items,
            parent=self,
            active_index=self.currentIndex(),
            match_parent_width=exact_width,
            match_parent_width_exact=exact_width,
            active_indicator=str(self.property("activeIndicator") or "check"),
        )

        global_pos = self.mapToGlobal(QPoint(0, 0))
        x = global_pos.x()
        y = global_pos.y() - popup.height() - 4

        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        bounds = screen.availableGeometry()
        if y < bounds.top() + 6:
            y = global_pos.y() + self.height() + 4

        if x + popup.width() > bounds.right() - 6:
            x = bounds.right() - 6 - popup.width()
        x = max(bounds.left() + 6, x)

        popup.show_at_pos(QPoint(x, y))


class ModernPopupComboBox(QComboBox):
    """具有圆角现代下拉菜单样式的自定义 QComboBox"""
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def showPopup(self) -> None:
        items = []
        is_provider = self.objectName() == "TranslatorProviderCombo"

        remarks = {}
        parent_dialog = None
        if is_provider:
            parent_dialog = (
                find_parent_with_attr(self, "_provider_popup_models_by_provider")
                or find_parent_with_attr(self, "_translator")
            )
        if parent_dialog is None:
            parent_dialog = self.window()

        def resolve_provider_owner(attr_name: str) -> QWidget | None:
            candidates: list[object] = []
            seen: set[int] = set()

            def add_candidate(candidate: object) -> None:
                if candidate is None:
                    return
                ident = id(candidate)
                if ident in seen:
                    return
                seen.add(ident)
                candidates.append(candidate)

            add_candidate(parent_dialog)
            try:
                add_candidate(find_parent_with_attr(self, attr_name))
            except Exception:
                pass
            try:
                add_candidate(find_parent_with_attr(self, "_translator"))
            except Exception:
                pass
            current = self.parentWidget()
            parent_seen: set[int] = set()
            while current is not None and id(current) not in parent_seen:
                parent_seen.add(id(current))
                add_candidate(current)
                try:
                    current = current.parentWidget()
                except Exception:
                    current = None
            try:
                add_candidate(self.window())
            except Exception:
                pass
            for candidate in candidates:
                if hasattr(candidate, attr_name):
                    return candidate  # type: ignore[return-value]
            return None

        if parent_dialog and hasattr(parent_dialog, "_translator"):
            remarks = parent_dialog._translator.get("provider_remarks") or {}

        current_role = "translate"
        provider_models = {}
        current_provider_model = ""
        highlighted_provider_models: set[str] = set()
        highlighted_provider_model_colors: dict[str, str] = {}

        def models_by_provider_from_translator(translator_obj: object, role: str) -> dict[str, list[str]]:
            translator_dict = translator_obj if isinstance(translator_obj, dict) else normalize_translator_settings(translator_obj)
            configs = dict(translator_dict.get("model_configs") or {})
            grouped: dict[str, list[str]] = {}
            excluded_for_qa = {"微软翻译", "Google翻译", "DeepLX"}
            for raw_name, raw_cfg in configs.items():
                model_name = str(raw_name or "").strip()
                if not model_name:
                    continue
                if str(role) == "qa" and model_name in excluded_for_qa:
                    continue
                cfg = dict(raw_cfg or {}) if isinstance(raw_cfg, dict) else {}
                provider = normalize_translator_provider(cfg.get("provider"), infer_translator_model_provider(model_name, cfg))
                grouped.setdefault(provider, []).append(model_name)
            return {provider: sorted(names) for provider, names in grouped.items() if names}

        def normalize_provider_models_cache(raw_cache: object, role: str) -> dict[str, list[str]]:
            if not isinstance(raw_cache, dict):
                return {}
            role_key = "qa" if str(role or "").strip().lower() == "qa" else "translate"
            cache = raw_cache.get(role_key) if isinstance(raw_cache.get(role_key), dict) else raw_cache
            if not isinstance(cache, dict):
                return {}
            grouped: dict[str, list[str]] = {}
            for raw_provider, raw_names in cache.items():
                provider = normalize_translator_provider(raw_provider, "")
                if not provider:
                    continue
                if isinstance(raw_names, (list, tuple, set)):
                    names = [str(name or "").strip() for name in raw_names]
                else:
                    names = [str(raw_names or "").strip()]
                names = [name for name in names if name]
                if names:
                    grouped[provider] = names
            return grouped

        def current_model_from_cache(raw_cache: object, role: str) -> str:
            if not isinstance(raw_cache, dict):
                return ""
            role_key = "qa" if str(role or "").strip().lower() == "qa" else "translate"
            return str(raw_cache.get(role_key) or raw_cache.get("current") or "").strip()

        def highlighted_models_from_cache(raw_cache: object) -> set[str]:
            if isinstance(raw_cache, dict):
                raw_values = raw_cache.get("qa") or raw_cache.get("models") or raw_cache.get("current") or []
            else:
                raw_values = raw_cache
            if isinstance(raw_values, str):
                values = [raw_values]
            else:
                try:
                    values = list(raw_values or [])
                except TypeError:
                    values = []
            return {str(name or "").strip() for name in values if str(name or "").strip()}

        def highlighted_model_colors_from_cache(raw_cache: object) -> dict[str, str]:
            if not isinstance(raw_cache, dict):
                return {}
            raw_values = raw_cache.get("qa") if isinstance(raw_cache.get("qa"), dict) else raw_cache
            colors: dict[str, str] = {}
            for raw_name, raw_color in dict(raw_values or {}).items():
                name = str(raw_name or "").strip()
                color = str(raw_color or "").strip()
                if name and color:
                    colors[name] = color
            return colors

        if is_provider:
            try:
                highlighted_provider_model_colors.update(
                    highlighted_model_colors_from_cache(self.property("providerHighlightedModelColors"))
                )
            except Exception:
                pass
            try:
                highlighted_provider_models.update(
                    highlighted_models_from_cache(self.property("providerHighlightedModels"))
                )
            except Exception:
                pass

        if is_provider and parent_dialog is not None:
            role_getter = getattr(parent_dialog, "_current_translator_role", None)
            if callable(role_getter):
                try:
                    current_role = "qa" if str(role_getter() or "").strip().lower() == "qa" else "translate"
                except Exception:
                    current_role = "translate"
            models_getter = getattr(parent_dialog, "_provider_popup_models_by_provider", None)
            if callable(models_getter):
                try:
                    provider_models = dict(models_getter(current_role) or {})
                except TypeError:
                    provider_models = dict(models_getter() or {})
                except Exception:
                    provider_models = {}
            current_model_getter = getattr(parent_dialog, "_provider_popup_current_model", None)
            if callable(current_model_getter):
                try:
                    current_provider_model = str(current_model_getter(current_role) or "").strip()
                except TypeError:
                    current_provider_model = str(current_model_getter() or "").strip()
                except Exception:
                    current_provider_model = ""
            highlighted_getter = getattr(parent_dialog, "_codex_highlighted_model_names", None)
            if not highlighted_provider_models and not highlighted_provider_model_colors and callable(highlighted_getter):
                try:
                    highlighted_provider_models = {
                        str(name or "").strip()
                        for name in set(highlighted_getter("qa") or set())
                        if str(name or "").strip()
                    }
                except TypeError:
                    try:
                        highlighted_provider_models = {
                            str(name or "").strip()
                            for name in set(highlighted_getter() or set())
                            if str(name or "").strip()
                        }
                    except Exception:
                        highlighted_provider_models = set()
                except Exception:
                    highlighted_provider_models = set()
            color_getter = getattr(parent_dialog, "_provider_highlighted_model_colors", None)
            if not highlighted_provider_model_colors and callable(color_getter):
                try:
                    highlighted_provider_model_colors.update(dict(color_getter("qa") or {}))
                except TypeError:
                    try:
                        highlighted_provider_model_colors.update(dict(color_getter() or {}))
                    except Exception:
                        pass
                except Exception:
                    pass
            if not highlighted_provider_models and hasattr(parent_dialog, "_translator"):
                try:
                    translator_dict = parent_dialog._translator if isinstance(parent_dialog._translator, dict) else {}
                    if bool(translator_dict.get("codex_current_model_config_enabled", False)):
                        bound_model = str(
                            translator_dict.get("codex_current_model_config_model", "")
                            or translator_dict.get("qa_model", "")
                            or translator_dict.get("current_model", "")
                        ).strip()
                        if bound_model:
                            highlighted_provider_models.add(bound_model)
                            highlighted_provider_model_colors.setdefault(bound_model, "#2563eb")
                except Exception:
                    pass
            if hasattr(parent_dialog, "_translator"):
                try:
                    translator_dict = parent_dialog._translator if isinstance(parent_dialog._translator, dict) else {}
                    cc_bound_model = str(translator_dict.get("claude_code_current_model_config_model", "") or "").strip()
                    if cc_bound_model:
                        highlighted_provider_models.add(cc_bound_model)
                        highlighted_provider_model_colors[cc_bound_model] = "#d97757"
                except Exception:
                    pass
            if not provider_models:
                try:
                    provider_models = normalize_provider_models_cache(self.property("providerModelsByProvider"), current_role)
                except Exception:
                    provider_models = {}
            if not provider_models and hasattr(parent_dialog, "_translator"):
                try:
                    provider_models = models_by_provider_from_translator(parent_dialog._translator, current_role)
                except Exception:
                    provider_models = {}

        if is_provider and not provider_models:
            try:
                provider_models = normalize_provider_models_cache(self.property("providerModelsByProvider"), current_role)
            except Exception:
                provider_models = {}

        if is_provider and not provider_models:
            try:
                settings = load_settings()
                ui = dict(getattr(settings, "ui", {}) or {})
                provider_models = models_by_provider_from_translator(ui.get("translator"), current_role)
            except Exception:
                provider_models = {}

        if is_provider and not current_provider_model:
            try:
                current_provider_model = current_model_from_cache(self.property("providerCurrentModels"), current_role)
            except Exception:
                current_provider_model = ""

        if is_provider and not highlighted_provider_models:
            try:
                highlighted_provider_models = highlighted_models_from_cache(self.property("providerHighlightedModels"))
            except Exception:
                highlighted_provider_models = set()
        if is_provider and not highlighted_provider_model_colors:
            try:
                highlighted_provider_model_colors = highlighted_model_colors_from_cache(self.property("providerHighlightedModelColors"))
            except Exception:
                highlighted_provider_model_colors = {}

        for name in highlighted_provider_models:
            highlighted_provider_model_colors.setdefault(name, "#2563eb")
        highlighted_provider_models.update(highlighted_provider_model_colors.keys())

        def make_activate_slot(idx):
            def slot(*args):
                self.setCurrentIndex(idx)
                already_called = [False]
                orig_on_selected = None
                if is_provider and parent_dialog and hasattr(parent_dialog, "_on_provider_selected_for_new_model"):
                    orig_on_selected = parent_dialog._on_provider_selected_for_new_model
                    def wrapper(*w_args, **w_kwargs):
                        already_called[0] = True
                        return orig_on_selected(*w_args, **w_kwargs)
                    parent_dialog._on_provider_selected_for_new_model = wrapper
                try:
                    self.activated.emit(idx)
                except Exception:
                    pass
                finally:
                    if orig_on_selected is not None:
                        parent_dialog._on_provider_selected_for_new_model = orig_on_selected
                if is_provider and not already_called[0] and parent_dialog and hasattr(parent_dialog, "_on_provider_selected_for_new_model"):
                    try:
                        parent_dialog._on_provider_selected_for_new_model()
                    except Exception:
                        pass
            return slot

        def make_provider_model_slot(model_name: str):
            def slot(*args):
                if not parent_dialog:
                    return
                selected_handler = getattr(parent_dialog, "_on_provider_popup_model_selected", None)
                if callable(selected_handler):
                    try:
                        selected_handler(model_name)
                        return
                    except TypeError:
                        selected_handler()
                        return
                changed_handler = getattr(parent_dialog, "_on_translator_model_changed", None)
                if callable(changed_handler):
                    changed_handler(model_name, current_role)
            return slot

        if is_provider:
            provider_order: list[str] = []
            seen_providers: set[str] = set()
            for i in range(self.count()):
                provider = str(self.itemText(i) or "").strip()
                if not provider or provider == "-" or provider in seen_providers:
                    continue
                provider_order.append(provider)
                seen_providers.add(provider)
            for provider in provider_models.keys():
                provider = str(provider or "").strip()
                if provider and provider not in seen_providers:
                    provider_order.append(provider)
                    seen_providers.add(provider)

            grouped_provider_models = [
                (provider, [str(name or "").strip() for name in provider_models.get(provider, []) if str(name or "").strip()])
                for provider in provider_order
            ]

            provider_index_by_name = {
                str(self.itemText(i) or "").strip(): i
                for i in range(self.count())
                if str(self.itemText(i) or "").strip()
            }

            def select_provider_group(provider: str) -> None:
                provider = str(provider or "").strip()
                idx = provider_index_by_name.get(provider, -1)
                if idx >= 0:
                    make_activate_slot(idx)()

            def select_provider_model(model_name: str) -> None:
                make_provider_model_slot(model_name)()

            model_delete_handler = getattr(parent_dialog, "_delete_translator_model", None) if parent_dialog else None
            batch_delete_handler = getattr(parent_dialog, "_delete_translator_models", None) if parent_dialog else None
            codex_action_handler = getattr(self, "_codex_action_handler", None)
            codex_label_handler = getattr(self, "_codex_label_handler", None)
            claude_code_action_handler = getattr(self, "_claude_code_action_handler", None)
            claude_code_label_handler = getattr(self, "_claude_code_label_handler", None)
            codex_action_owner = None if callable(codex_action_handler) else resolve_provider_owner("_apply_current_model_codex_config_for_model")
            codex_label_owner = None if callable(codex_label_handler) else (resolve_provider_owner("_codex_action_label_for_model") or codex_action_owner)
            claude_code_action_owner = (
                None
                if callable(claude_code_action_handler)
                else resolve_provider_owner("_apply_current_model_claude_code_config_for_model")
            )
            claude_code_label_owner = (
                None
                if callable(claude_code_label_handler)
                else (resolve_provider_owner("_claude_code_action_label_for_model") or claude_code_action_owner)
            )
            codex_action_handler = (
                codex_action_handler
                if callable(codex_action_handler)
                else (
                    getattr(codex_action_owner, "_apply_current_model_codex_config_for_model", None)
                    if codex_action_owner is not None
                    else None
                )
            )
            codex_label_handler = (
                codex_label_handler
                if callable(codex_label_handler)
                else (
                    getattr(codex_label_owner, "_codex_action_label_for_model", None)
                    if codex_label_owner is not None
                    else None
                )
            )
            claude_code_action_handler = (
                claude_code_action_handler
                if callable(claude_code_action_handler)
                else (
                    getattr(claude_code_action_owner, "_apply_current_model_claude_code_config_for_model", None)
                    if claude_code_action_owner is not None
                    else None
                )
            )
            claude_code_label_handler = (
                claude_code_label_handler
                if callable(claude_code_label_handler)
                else (
                    getattr(claude_code_label_owner, "_claude_code_action_label_for_model", None)
                    if claude_code_label_owner is not None
                    else None
                )
            )
            provider_by_model: dict[str, str] = {}
            for provider, names in grouped_provider_models:
                for name in names:
                    provider_by_model.setdefault(str(name), str(provider))

            def delete_provider_model(model_name: str) -> None:
                if not callable(model_delete_handler):
                    return
                try:
                    model_delete_handler(model_name, current_role)
                except TypeError:
                    model_delete_handler(model_name)

            def delete_provider_models(model_names: list[str]) -> None:
                if not callable(batch_delete_handler):
                    return
                try:
                    batch_delete_handler(list(model_names), current_role)
                except TypeError:
                    batch_delete_handler(list(model_names))

            def apply_provider_codex_action(model_name: str, provider_name: str = "") -> None:
                provider_name = str(provider_name or provider_by_model.get(str(model_name), "") or "").strip()
                is_claude_code = provider_name == "Claude Code"
                action_handler = claude_code_action_handler if is_claude_code else codex_action_handler
                logger.info(
                    "[模型分组下拉] 准备执行%s配置按钮: model=%s provider=%s cached_handler=%s",
                    "Claude Code" if is_claude_code else "Codex",
                    model_name,
                    provider_name,
                    callable(action_handler),
                )
                if not callable(action_handler):
                    action_owner = resolve_provider_owner(
                        "_apply_current_model_claude_code_config_for_model"
                        if is_claude_code
                        else "_apply_current_model_codex_config_for_model"
                    )
                    action_handler = (
                        getattr(
                            action_owner,
                            "_apply_current_model_claude_code_config_for_model"
                            if is_claude_code
                            else "_apply_current_model_codex_config_for_model",
                            None,
                        )
                        if action_owner is not None
                        else None
                    )
                if not callable(action_handler):
                    logger.warning(
                        "[模型分组下拉] 未找到%s配置处理器: model=%s provider=%s parent=%s window=%s",
                        "Claude Code" if is_claude_code else "Codex",
                        model_name,
                        provider_name,
                        type(parent_dialog).__name__ if parent_dialog is not None else None,
                        type(self.window()).__name__ if self.window() is not None else None,
                    )
                    return
                try:
                    try:
                        action_handler(model_name)
                    except TypeError:
                        action_handler()
                except Exception:
                    logger.exception(
                        "[模型分组下拉] %s配置处理器执行失败: model=%s provider=%s",
                        "Claude Code" if is_claude_code else "Codex",
                        model_name,
                        provider_name,
                    )

            def provider_codex_action_label(model_name: str, provider_name: str = "") -> str:
                provider_name = str(provider_name or provider_by_model.get(str(model_name), "") or "").strip()
                is_claude_code = provider_name == "Claude Code"
                label_handler = claude_code_label_handler if is_claude_code else codex_label_handler
                if callable(label_handler):
                    try:
                        label = str(label_handler(model_name) or "").strip()
                        if label:
                            return label
                    except TypeError:
                        try:
                            label = str(label_handler() or "").strip()
                            if label:
                                return label
                        except Exception:
                            pass
                    except Exception:
                        pass
                if is_claude_code:
                    return "填入CC"
                return "还原Codex" if str(model_name or "").strip() in highlighted_provider_models else "填入Codex"

            def provider_codex_action_color(model_name: str, provider_name: str = "") -> str:
                provider_name = str(provider_name or provider_by_model.get(str(model_name), "") or "").strip()
                if provider_name == "Claude Code":
                    return "#d97757"
                return "#2563eb"

            popup = _OcrModelMenuPopup(
                grouped_provider_models,
                current_provider_model,
                select_provider_model,
                parent=self,
                on_delete=delete_provider_model if callable(model_delete_handler) else None,
                on_batch_delete=delete_provider_models if callable(batch_delete_handler) else None,
                purpose=current_role,
                match_parent_width=True,
                active_check_color=highlighted_provider_model_colors.get(current_provider_model, "#111827"),
                highlighted_models=highlighted_provider_models,
                highlighted_text_color="#2563eb",
                highlighted_model_colors=highlighted_provider_model_colors,
                on_group_selected=select_provider_group,
                on_codex_action=apply_provider_codex_action,
                codex_action_label_for_model=provider_codex_action_label,
                codex_action_color_for_model=provider_codex_action_color,
                show_group_move_action=True,
            )
            popup.show_at(self, self.mapToGlobal(QPoint(0, self.height() + 4)))
            return

        popup_active_index = None
        popup_item_index = 0

        for i in range(self.count()):
            text = self.itemText(i)
            is_sep = text == "-"
            if is_sep:
                items.append(("-", None, False))
            else:
                handle_activated = make_activate_slot(i)
                remark = remarks.get(text, "").strip() if is_provider else ""

                actions = []
                if is_provider:
                    if parent_dialog and hasattr(parent_dialog, "_move_current_model_to_provider"):
                        def move_to_this(prov=text, dlg=parent_dialog):
                            dlg._move_current_model_to_provider(prov)
                        actions.append(("移动当前模型到此分组", move_to_this, "normal"))
                    if text != "默认分组" and parent_dialog and hasattr(parent_dialog, "delete_provider_group"):
                        def delete_this(prov=text, dlg=parent_dialog):
                            dlg.delete_provider_group(prov)
                        actions.append(("×", delete_this, "danger"))

                if i == self.currentIndex():
                    popup_active_index = popup_item_index
                items.append((text, handle_activated, True, actions, remark))
                popup_item_index += 1
                if is_provider:
                    for model_name in provider_models.get(text, []):
                        model_name = str(model_name or "").strip()
                        if not model_name:
                            continue
                        current_prefix = "✓ " if model_name == current_provider_model else ""
                        items.append((f"    {current_prefix}{model_name}", make_provider_model_slot(model_name), True))
                        popup_item_index += 1

        exact_width = bool(self.property(POPUP_EXACT_WIDTH_PROPERTY))
        popup = OcrGenericMenuPopup(
            items,
            parent=self,
            active_index=popup_active_index,
            match_parent_width=True,
            match_parent_width_exact=exact_width,
            active_indicator=str(self.property("activeIndicator") or "check"),
        )

        global_pos = self.mapToGlobal(QPoint(0, 0))
        x = global_pos.x()
        y = global_pos.y() + self.height() + 4

        screen = QGuiApplication.screenAt(global_pos) or QGuiApplication.primaryScreen()
        bounds = screen.availableGeometry()

        if y + popup.height() > bounds.bottom() - 6:
            y = global_pos.y() - popup.height() - 4

        if x + popup.width() > bounds.right() - 6:
            x = bounds.right() - 6 - popup.width()
        x = max(bounds.left() + 6, x)

        popup.show_at_pos(QPoint(x, y))


class ModernMouseClickOnlyComboBox(ModernPopupComboBox):
    """屏蔽键盘和鼠标滚轮事件，只响应鼠标点击下拉的 ModernPopupComboBox"""
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def keyPressEvent(self, event) -> None:
        event.ignore()

    def wheelEvent(self, event) -> None:
        event.ignore()
