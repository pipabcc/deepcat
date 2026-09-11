import unittest
from types import SimpleNamespace


class TestLaterReadProbe(unittest.TestCase):
    def test_link_payload_uses_legacy_default_action_url(self) -> None:
        try:
            import deepcat.ui.region_overlay as overlay
        except Exception:
            self.skipTest("缺少 PyQt6，跳过稍后阅读链接探测测试")

        class FakeWrapper:
            element_info = SimpleNamespace(control_type="Hyperlink", name="示例链接")

            def rectangle(self):
                return SimpleNamespace(left=10, top=10, right=100, bottom=40)

            def window_text(self):
                return "示例链接"

            def texts(self):
                return []

            def legacy_properties(self):
                return {"DefaultAction": "Jump to https://example.com/article"}

            @property
            def iface_value(self):
                raise RuntimeError("no value")

        payload = overlay._build_link_payload(FakeWrapper(), 20, 20)

        self.assertEqual(payload, {"title": "示例链接", "url": "https://example.com/article"})

    def test_link_payload_allows_small_rect_margin(self) -> None:
        try:
            import deepcat.ui.region_overlay as overlay
        except Exception:
            self.skipTest("缺少 PyQt6，跳过稍后阅读链接探测测试")

        class FakeWrapper:
            element_info = SimpleNamespace(control_type="Hyperlink", name="https://example.com")

            def rectangle(self):
                return SimpleNamespace(left=10, top=10, right=100, bottom=40)

            def window_text(self):
                return ""

            def texts(self):
                return []

            def legacy_properties(self):
                return {}

            @property
            def iface_value(self):
                raise RuntimeError("no value")

        self.assertIsNone(overlay._build_link_payload(FakeWrapper(), 8, 20, margin=0))
        self.assertEqual(
            overlay._build_link_payload(FakeWrapper(), 8, 20, margin=4),
            {"title": "https://example.com", "url": "https://example.com"},
        )

    def test_link_payload_text_url_is_fallback_only(self) -> None:
        try:
            import deepcat.ui.region_overlay as overlay
        except Exception:
            self.skipTest("缺少 PyQt6，跳过稍后阅读链接探测测试")

        class FakeWrapper:
            element_info = SimpleNamespace(control_type="Text", name="参考链接 https://example.com/fallback")

            def rectangle(self):
                return SimpleNamespace(left=10, top=10, right=260, bottom=40)

            def window_text(self):
                return "参考链接 https://example.com/fallback"

            def texts(self):
                return []

            def legacy_properties(self):
                return {}

            @property
            def iface_value(self):
                raise RuntimeError("no value")

        self.assertIsNone(overlay._build_link_payload(FakeWrapper(), 20, 20))
        self.assertEqual(
            overlay._build_link_payload(FakeWrapper(), 20, 20, allow_text_url=True),
            {"title": "参考链接 https://example.com/fallback", "url": "https://example.com/fallback"},
        )

    def test_link_probe_only_enables_input_passthrough(self) -> None:
        try:
            import deepcat.ui.region_overlay as overlay
        except Exception:
            self.skipTest("缺少 PyQt6，跳过稍后阅读链接探测测试")

        captured: dict[str, object] = {}
        dummy = SimpleNamespace(
            _link_probe_only=True,
            _set_mouse_input_passthrough=lambda enabled: captured.update(enabled=enabled),
        )

        overlay.RegionOverlay._enable_link_probe_input_passthrough(dummy)

        self.assertTrue(captured["enabled"])


if __name__ == "__main__":
    unittest.main()
