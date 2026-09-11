import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from deepcat.utils import claude_code_config


class TestClaudeCodeConfig(unittest.TestCase):
    def test_apply_claude_code_config_preserves_settings_and_existing_api_key_field(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".claude"
            config_dir.mkdir()
            settings_path = config_dir / "settings.json"
            settings_path.write_text(
                json.dumps(
                    {
                        "permissions": {"allow_file_access": True},
                        "env": {
                            "ANTHROPIC_API_KEY": "old-key",
                            "ANTHROPIC_DEFAULT_SONNET_MODEL_NAME": "Old Sonnet",
                        },
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )

            with patch.object(claude_code_config, "CLAUDE_CODE_DIR", config_dir):
                written_path = claude_code_config.apply_claude_code_config(
                    base_url="https://api.example.com",
                    model="claude-sonnet-4.6",
                    api_key="new-key",
                )

            self.assertEqual(written_path, settings_path)
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            self.assertEqual(data["permissions"], {"allow_file_access": True})
            env = data["env"]
            self.assertEqual(env["ANTHROPIC_API_KEY"], "new-key")
            self.assertNotIn("ANTHROPIC_AUTH_TOKEN", env)
            self.assertEqual(env["ANTHROPIC_BASE_URL"], "https://api.example.com")
            self.assertEqual(env["ANTHROPIC_MODEL"], "claude-sonnet-4.6")
            self.assertEqual(env["ANTHROPIC_DEFAULT_SONNET_MODEL"], "claude-sonnet-4.6")
            self.assertEqual(env["ANTHROPIC_DEFAULT_SONNET_MODEL_NAME"], "claude-sonnet-4.6")
            self.assertEqual(env["ANTHROPIC_DEFAULT_FABLE_MODEL"], "claude-sonnet-4.6")

    def test_apply_claude_code_config_uses_legacy_file_when_present(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".claude"
            config_dir.mkdir()
            legacy_path = config_dir / "claude.json"
            legacy_path.write_text("{}", encoding="utf-8")

            with patch.object(claude_code_config, "CLAUDE_CODE_DIR", config_dir):
                written_path = claude_code_config.apply_claude_code_config(
                    base_url="https://api.example.com",
                    model="claude-haiku-4.5",
                    api_key="token",
                )

            self.assertEqual(written_path, legacy_path)
            data = json.loads(legacy_path.read_text(encoding="utf-8"))
            self.assertEqual(data["env"]["ANTHROPIC_AUTH_TOKEN"], "token")

    def test_restore_claude_code_config_restores_previous_settings_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".claude"
            config_dir.mkdir()
            settings_path = config_dir / "settings.json"
            original = {"env": {"ANTHROPIC_AUTH_TOKEN": "old-token"}, "theme": "dark"}
            settings_path.write_text(json.dumps(original, ensure_ascii=False), encoding="utf-8")

            with patch.object(claude_code_config, "CLAUDE_CODE_DIR", config_dir):
                claude_code_config.apply_claude_code_config(
                    base_url="https://api.example.com",
                    model="claude-sonnet-4.6",
                    api_key="new-token",
                )
                restored_path = claude_code_config.restore_claude_code_config()

            self.assertEqual(restored_path, settings_path)
            self.assertEqual(json.loads(settings_path.read_text(encoding="utf-8")), original)
            self.assertFalse((config_dir / "deepcat_settings.json.bak").exists())

    def test_restore_claude_code_config_removes_file_created_by_deepcat(self):
        with tempfile.TemporaryDirectory() as tmp:
            config_dir = Path(tmp) / ".claude"

            with patch.object(claude_code_config, "CLAUDE_CODE_DIR", config_dir):
                written_path = claude_code_config.apply_claude_code_config(
                    base_url="https://api.example.com",
                    model="claude-haiku-4.5",
                    api_key="token",
                )
                restored_path = claude_code_config.restore_claude_code_config()

            self.assertEqual(restored_path, written_path)
            self.assertFalse(written_path.exists())
            self.assertFalse((config_dir / "deepcat_settings.json.none").exists())


if __name__ == "__main__":
    unittest.main()
