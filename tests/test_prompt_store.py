import unittest
import tempfile
import shutil
from pathlib import Path
from deepcat.prompt_store import PromptStore, PROMPT_DEFAULTS

class TestPromptStore(unittest.TestCase):
    def setUp(self) -> None:
        self.test_dir = Path(tempfile.mkdtemp())
        self.db_path = self.test_dir / "test_prompt.db"

    def tearDown(self) -> None:
        shutil.rmtree(str(self.test_dir), ignore_errors=True)

    def test_default_values(self) -> None:
        store = PromptStore(self.db_path)
        try:
            prompts = store.load_all_prompts()
            self.assertEqual(prompts["explain_prompt_button_name"], "解释")
            self.assertEqual(prompts["reply_prompt_button_name"], "回复")
            self.assertEqual(prompts["summary_prompt_button_name"], "总结")
            self.assertTrue(store.is_empty())
        finally:
            store.close()

    def test_save_and_load(self) -> None:
        store = PromptStore(self.db_path)
        try:
            store.save_prompt("explain_prompt_button_name", "高级翻译")
            prompts = store.load_all_prompts()
            self.assertEqual(prompts["explain_prompt_button_name"], "高级翻译")
            self.assertFalse(store.is_empty())
        finally:
            store.close()

    def test_save_all_prompts(self) -> None:
        store = PromptStore(self.db_path)
        try:
            data = {
                "explain_prompt_button_name": "翻译",
                "summary_prompt_button_name": "提炼",
            }
            store.save_all_prompts(data)
            prompts = store.load_all_prompts()
            self.assertEqual(prompts["explain_prompt_button_name"], "翻译")
            self.assertEqual(prompts["summary_prompt_button_name"], "提炼")
        finally:
            store.close()

    def test_migrate_from_settings(self) -> None:
        store = PromptStore(self.db_path)
        try:
            self.assertTrue(store.is_empty())
            settings_ui = {
                "translator": {
                    "explain_prompt_button_name": "划词解释",
                    "reply_prompt_button_name": "快捷回复",
                    "some_other_key": "ignored"
                },
                "chat_placeholder_cards": [
                    {
                        "id": "translate",
                        "title": "自定义翻译",
                        "prompt": "自定义提示词"
                    }
                ]
            }
            migrated = store.migrate_from_settings(settings_ui)
            self.assertTrue(migrated)
            self.assertFalse(store.is_empty())

            prompts = store.load_all_prompts()
            self.assertEqual(prompts["explain_prompt_button_name"], "划词解释")
            self.assertEqual(prompts["reply_prompt_button_name"], "快捷回复")
            # 没有迁移的应该落到默认值
            self.assertEqual(prompts["summary_prompt_button_name"], "总结")

            # 校验卡片提示词是否成功迁移并能解析
            import json
            cards = json.loads(prompts["chat_placeholder_cards"])
            self.assertEqual(len(cards), 1)
            self.assertEqual(cards[0]["title"], "自定义翻译")
        finally:
            store.close()
