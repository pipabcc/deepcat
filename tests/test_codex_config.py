import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from deepcat.utils import codex_config


class CodexConfigTests(unittest.TestCase):
    def test_chatgpt_process_is_recognized_as_codex_desktop_client(self):
        self.assertTrue(codex_config._is_codex_client_process_name("ChatGPT.exe"))
        self.assertTrue(codex_config._is_codex_client_process_name("codex.exe"))
        self.assertFalse(codex_config._is_codex_client_process_name("codex-command-runner.exe"))

    def test_stop_client_terminates_chatgpt_process_tree_instead_of_codex_child(self):
        with patch.object(codex_config.os, "name", "nt"), \
             patch.object(
                 codex_config,
                 "list_codex_client_process_names",
                 side_effect=[["ChatGPT.exe", "codex.exe"], []],
             ), \
             patch.object(
                 codex_config.subprocess,
                 "run",
                 return_value=SimpleNamespace(returncode=0),
             ) as run:
            codex_config.stop_codex_client()

        run.assert_called_once()
        self.assertEqual(
            run.call_args.args[0],
            ["taskkill", "/F", "/T", "/IM", "ChatGPT.exe"],
        )

    def test_apply_config_rolls_back_all_files_when_auth_write_fails(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            paths = {
                "CODEX_DIR": root,
                "CONFIG_PATH": root / "config.toml",
                "AUTH_PATH": root / "auth.json",
                "BAK_CONFIG_PATH": root / "deepcat_config.toml.bak",
                "BAK_AUTH_PATH": root / "deepcat_auth.json.bak",
                "NONE_CONFIG_MARKER": root / "deepcat_config.toml.none",
                "NONE_AUTH_MARKER": root / "deepcat_auth.json.none",
            }
            paths["CONFIG_PATH"].write_text('model = "original"\n', encoding="utf-8")
            paths["AUTH_PATH"].write_text('{"OPENAI_API_KEY":"original"}', encoding="utf-8")
            original_atomic_write = codex_config._atomic_write_text

            def fail_auth_write(path: Path, content: str) -> None:
                if path == paths["AUTH_PATH"]:
                    raise OSError("simulated auth write failure")
                original_atomic_write(path, content)

            with patch.multiple(codex_config, **paths), \
                 patch.object(codex_config, "_atomic_write_text", side_effect=fail_auth_write):
                with self.assertRaisesRegex(OSError, "simulated auth write failure"):
                    codex_config.apply_codex_config(True)

            self.assertEqual(paths["CONFIG_PATH"].read_text(encoding="utf-8"), 'model = "original"\n')
            self.assertEqual(
                paths["AUTH_PATH"].read_text(encoding="utf-8"),
                '{"OPENAI_API_KEY":"original"}',
            )
            for name in (
                "BAK_CONFIG_PATH",
                "BAK_AUTH_PATH",
                "NONE_CONFIG_MARKER",
                "NONE_AUTH_MARKER",
            ):
                self.assertFalse(paths[name].exists())


if __name__ == "__main__":
    unittest.main()
