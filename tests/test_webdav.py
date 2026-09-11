import unittest


class TestWebDAVClient(unittest.TestCase):
    def test_remote_urls_quote_path_segments(self) -> None:
        from deepcat.utils.webdav import WebDAVClient

        client = WebDAVClient(
            "https://dav.example.com/dav/",
            "user",
            "pass",
            "我的坚果云/二级 目录",
        )

        self.assertEqual(
            client._backup_dir_url(),
            "https://dav.example.com/dav/%E6%88%91%E7%9A%84%E5%9D%9A%E6%9E%9C%E4%BA%91/%E4%BA%8C%E7%BA%A7%20%E7%9B%AE%E5%BD%95/",
        )
        self.assertEqual(
            client._file_url("deep cat 备份.zip"),
            "https://dav.example.com/dav/%E6%88%91%E7%9A%84%E5%9D%9A%E6%9E%9C%E4%BA%91/%E4%BA%8C%E7%BA%A7%20%E7%9B%AE%E5%BD%95/deep%20cat%20%E5%A4%87%E4%BB%BD.zip",
        )

    def test_remote_filename_is_collapsed_to_basename(self) -> None:
        from deepcat.utils.webdav import WebDAVClient

        client = WebDAVClient("https://dav.example.com/dav/", "user", "pass", "backup")

        self.assertEqual(
            client._file_url("../evil.zip"),
            "https://dav.example.com/dav/backup/evil.zip",
        )


if __name__ == "__main__":
    unittest.main()
