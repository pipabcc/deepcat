import hashlib
import io
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile


class TestUpdater(unittest.TestCase):
    def test_compare_versions(self) -> None:
        import deepcat.updater as updater

        self.assertEqual(updater.compare_versions("1.0.0", "v1.0.0"), 0)
        self.assertGreater(updater.compare_versions("v1.0.1", "1.0.0"), 0)
        self.assertLess(updater.compare_versions("1.0.0-beta", "1.0.0"), 0)
        self.assertFalse(updater.is_newer_version("bad", "1.0.0"))

    def test_release_asset_selection_prefers_deepcat_windows_zip(self) -> None:
        import deepcat.updater as updater

        release = {
            "assets": [
                {"name": "source.zip", "browser_download_url": "https://example.invalid/source.zip", "size": 100},
                {"name": "deepcat-macos.zip", "browser_download_url": "https://example.invalid/mac.zip", "size": 200},
                {"name": "deepcat-windows-x64.zip", "browser_download_url": "https://example.invalid/win.zip", "size": 150},
            ]
        }
        asset = updater.select_release_asset(release)
        self.assertIsNotNone(asset)
        self.assertEqual(asset["name"], "deepcat-windows-x64.zip")

    def test_info_from_release_marks_newer_version(self) -> None:
        import deepcat.updater as updater

        info = updater.info_from_release(
            {
                "tag_name": "v1.0.1",
                "html_url": "https://example.invalid/releases/v1.0.1",
                "published_at": "2026-05-27T00:00:00Z",
                "body": "notes",
                "assets": [
                    {
                        "name": "deepcat-windows.zip",
                        "browser_download_url": "https://example.invalid/deepcat.zip",
                        "size": 12,
                        "digest": "sha256:" + "0" * 64,
                    }
                ],
            },
            "1.0.0",
        )
        self.assertTrue(info.available)
        self.assertEqual(info.latest_version, "1.0.1")
        self.assertEqual(info.asset_name, "deepcat-windows.zip")
        self.assertEqual(info.asset_digest, "sha256:" + "0" * 64)

    def test_download_update_validates_digest_and_exe(self) -> None:
        import deepcat.updater as updater

        buf = io.BytesIO()
        with ZipFile(buf, "w") as zf:
            zf.writestr("deepcat.exe", b"binary")
        data = buf.getvalue()
        digest = hashlib.sha256(data).hexdigest()

        class FakeResponse:
            def __init__(self, payload: bytes) -> None:
                self._stream = io.BytesIO(payload)
                self.headers = {"Content-Length": str(len(payload))}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def read(self, size: int = -1) -> bytes:
                return self._stream.read(size)

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            old_get_app_dir = updater.get_app_dir
            old_urlopen = updater.urlopen
            try:
                updater.get_app_dir = lambda: root  # type: ignore[assignment]
                updater.urlopen = lambda req, timeout=30.0: FakeResponse(data)  # type: ignore[assignment]
                info = updater.UpdateInfo(
                    available=True,
                    current_version="1.0.0",
                    latest_version="1.0.1",
                    tag_name="v1.0.1",
                    asset_name="deepcat-windows.zip",
                    download_url="https://example.invalid/deepcat.zip",
                    release_url="",
                    published_at="",
                    notes="",
                    asset_size=len(data),
                    asset_digest=f"sha256:{digest}",
                )
                path = updater.download_update(info)
                self.assertTrue(path.exists())
                self.assertEqual(path.name, "deepcat-windows.zip")
            finally:
                updater.get_app_dir = old_get_app_dir  # type: ignore[assignment]
                updater.urlopen = old_urlopen  # type: ignore[assignment]

    def test_verify_zip_requires_deepcat_exe(self) -> None:
        import deepcat.updater as updater

        with tempfile.TemporaryDirectory() as d:
            zip_path = Path(d) / "update.zip"
            with ZipFile(zip_path, "w") as zf:
                zf.writestr("readme.txt", "missing exe")
            self.assertFalse(updater.verify_zip_contains_exe(zip_path))


if __name__ == "__main__":
    unittest.main()
