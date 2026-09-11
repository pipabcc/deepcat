import unittest
import tempfile
import zipfile
import shutil
from pathlib import Path
from deepcat.core import data_manager

class TestDataBackupExcludeImages(unittest.TestCase):
    def setUp(self):
        # 创建临时工作目录
        self.test_dir = tempfile.TemporaryDirectory()
        self.app_dir = Path(self.test_dir.name)

        # 准备数据目录
        self.data_dir = self.app_dir / "data"
        self.data_dir.mkdir(parents=True, exist_ok=True)

        # 创建一个模拟的 SQLite 数据库文件
        self.db_path = self.data_dir / "clipboard_history.db"
        self.db_path.write_text("dummy database content")

        # 创建一个模拟的图片目录，并在其中放置几张图片
        self.img_dir = self.data_dir / "clipboard_images"
        self.img_dir.mkdir(parents=True, exist_ok=True)
        (self.img_dir / "image1.png").write_text("image 1 content")
        (self.img_dir / "image2.jpg").write_text("image 2 content")

        # Mock 掉 get_app_dir 以防止其影响用户真实的软件目录
        self.old_get_app_dir = data_manager.get_app_dir
        data_manager.get_app_dir = lambda: self.app_dir

    def tearDown(self):
        # 恢复 get_app_dir
        data_manager.get_app_dir = self.old_get_app_dir
        self.test_dir.cleanup()

    def test_backup_excludes_images_and_restore_preserves_local_images(self):
        zip_path = self.app_dir / "backup.zip"
        options = {
            "clipboard": True,
            "table_notes": False,
            "later_read": False,
            "settings": False,
            "model_catalog": False
        }

        # 1. 运行备份
        ok, msg = data_manager.backup_data(zip_path, options)
        self.assertTrue(ok, f"备份失败: {msg}")
        self.assertTrue(zip_path.exists())

        # 2. 验证 ZIP 内容：应该包含 db，但不应该包含任何图片文件
        with zipfile.ZipFile(zip_path, "r") as zipf:
            namelist = zipf.namelist()
            # 确认数据库文件在里面
            self.assertIn("data/clipboard_history.db", namelist)

            # 确认图片目录及图片不在里面
            for name in namelist:
                self.assertFalse(
                    name.startswith("data/clipboard_images/"),
                    f"备份中不应包含图片文件: {name}"
                )

        # 3. 验证还原行为
        # 在还原前，删除本地数据库，以验证它能被成功恢复
        self.db_path.unlink()

        # 并在本地图片目录中放一个本地新增的图片（模拟不包含在备份里的本地文件）
        local_unique_img = self.img_dir / "local_only.png"
        local_unique_img.write_text("local unique image content")

        # 运行还原
        ok_restore, msg_restore = data_manager.restore_data(zip_path)
        self.assertTrue(ok_restore, f"还原失败: {msg_restore}")

        # 预期：数据库文件已被还原回来
        self.assertTrue(self.db_path.exists())
        self.assertEqual(self.db_path.read_text(), "dummy database content")

        # 预期：本地独有的图片文件 local_only.png 应该完好无损地被保留着，而不是被 shutil.rmtree 删掉
        self.assertTrue(local_unique_img.exists())
        self.assertEqual(local_unique_img.read_text(), "local unique image content")

        # 本地已有的其他图片也应该健在
        self.assertTrue((self.img_dir / "image1.png").exists())

if __name__ == "__main__":
    unittest.main()
