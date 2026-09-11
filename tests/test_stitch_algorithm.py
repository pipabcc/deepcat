import unittest
import numpy as np
from deepcat.core.stitcher import stitch_images
from deepcat.core.detector import FixedRegions

class TestStitchAlgorithm(unittest.TestCase):
    def test_white_background_and_sparse_text_no_squish(self) -> None:
        """
        验证含有大面积纯白背景和稀疏文字的场景下，拼接算法能完美拼合，
        并且不会因为以前的 seam-checking 缺陷而吞掉纯白背景（导致 80 像素的截断与挤压）。
        """
        # 创建两张模拟截图帧 (1000px 宽, 500px 高, 3通道纯白)
        w, h = 1000, 500

        # 帧 1：纯白，但在 y = 400..420 处有少许“文字”（黑色横线）
        frame1 = np.full((h, w, 3), 255, dtype=np.uint8)
        frame1[400:420, 200:800, :] = 0  # 模拟文字

        # 帧 2：向下滚动了 200 像素，所以原有的“文字”在新帧的 y = 200..220 处出现
        # 并且在 y = 450..470 处有第二行新“文字”
        frame2 = np.full((h, w, 3), 255, dtype=np.uint8)
        frame2[200:220, 200:800, :] = 0  # 同样位置对应的文字
        frame2[450:470, 300:700, :] = 0  # 第二行新文字

        # 预期滚动像素是 200 像素，所以 template 匹配位置是在 frame2 的 y = 200 上面。
        # 调用 stitch_images 拼接
        result = stitch_images([frame1, frame2], strip_height=100)

        # 验证拼接结果的尺寸
        # 原始高度 500，向下滚动了 200，所以拼接后的完美高度应当恰好是 500 + 200 = 700 像素。
        self.assertEqual(result.shape[0], 700)
        self.assertEqual(result.shape[1], 1000)

        # 验证文字是否完美保留且位置完全正确
        # 第一行文字在 400:420 处
        self.assertTrue(np.all(result[400:420, 200:800, :] == 0))
        # 拼接点刚好在 420 处，第二行文字在新帧 450 处，对应拼合后应该在 420 + (450 - 220) = 650 处！
        # 第二行文字在 650:670 处
        self.assertTrue(np.all(result[650:670, 300:700, :] == 0))

        # 确保其他部分（例如 500:600 处的白边）为纯白色，说明完全没有受到截断或挤压
        self.assertTrue(np.all(result[500:600, :, :] == 255))
        print("单元测试 test_white_background_and_sparse_text_no_squish 通过！")

    def test_scrollbar_interference_removal(self) -> None:
        """
        验证当右侧存在滚动条滑块随滚动向相反方向移动时，
        边缘裁剪机制能完美隔离滚动条干扰，保持 100% 像素级的文字精准对齐。
        """
        w, h = 1000, 500
        # 帧 1：有两行文字
        frame1 = np.full((h, w, 3), 255, dtype=np.uint8)
        frame1[300:320, 200:800, :] = 0  # 第一行字
        frame1[400:420, 250:750, :] = 0  # 第二行字
        # 右侧滚动条滑块：在顶部区域 (y = 50..150)，颜色为深灰色
        frame1[50:150, w-20:w-5, :] = 80

        # 帧 2：向下滚动了 180 像素，所以文字向上移动了 180 像素
        # 帧 1 的 [300:320] 应该移动到 [120:140]
        # 帧 1 的 [400:420] 应该移动到 [220:240]
        frame2 = np.full((h, w, 3), 255, dtype=np.uint8)
        frame2[120:140, 200:800, :] = 0
        frame2[220:240, 250:750, :] = 0
        # 此时，右侧滚动条滑块向下移动：比如在 (y = 200..300)
        frame2[200:300, w-20:w-5, :] = 80

        # 进行拼接，如果不做边缘裁剪隔离，强对比度的滚动条移动会将匹配拖向完全错位的地方
        result = stitch_images([frame1, frame2], strip_height=100)

        # 物理对齐高度应该是 500 + 180 = 680
        self.assertEqual(result.shape[0], 680)
        self.assertEqual(result.shape[1], 1000)

        # 验证文字是否精准重合与拼合
        self.assertTrue(np.all(result[300:320, 200:800, :] == 0))
        self.assertTrue(np.all(result[400:420, 250:750, :] == 0))
        print("单元测试 test_scrollbar_interference_removal 通过！")

    def test_bottom_scroll_drop_perfect_stitch(self) -> None:
        """
        验证当网页滚动到达最底部，最后一步滚动高度从 200px 骤降到 40px时，
        软性惩罚和置信度豁免机制能够确保拼接完美合龙，无任何重叠或剪切。
        """
        w, h = 1000, 500

        # 模拟三帧拼接
        # 帧 1：正常帧
        frame1 = np.full((h, w, 3), 255, dtype=np.uint8)
        # 通过在 x 轴上设置不同的宽度，赋予每行文字唯一的特征，排除歧义
        frame1[200:220, 200:500, :] = 0  # 第一行字
        frame1[400:420, 300:800, :] = 0  # 第二行字

        # 帧 2：向下滚动了 200 像素
        frame2 = np.full((h, w, 3), 255, dtype=np.uint8)
        frame2[200:220, 300:800, :] = 0  # 第二行字向上移到 [200:220] 处
        frame2[400:420, 400:900, :] = 0  # 第三行新字在 [400:420] 处

        # 帧 3：触底！只滚动了 40 像素 (由于页面高度有限，无法再向下滚动 200 像素)
        # 第三行字应该只向上移动了 40 像素，所以位于 [360:380] 处
        # 并且底部露出了一点点底部版权信息
        frame3 = np.full((h, w, 3), 255, dtype=np.uint8)
        frame3[360:380, 400:900, :] = 0  # 第三行字在 [360:380] 处
        frame3[470:490, 500:700, :] = 0  # 底部版权文字（在 470..490，在裁切 start_y = 460 之后会完整保留）

        # 拼接三帧。拼接 1->2 滚动 200px，拼接 2->3 滚动 40px。
        # 拼接后的最终总高度应当是：500 + 200 + 40 = 740 像素。
        result = stitch_images([frame1, frame2, frame3], strip_height=100)

        self.assertEqual(result.shape[0], 740)
        self.assertEqual(result.shape[1], 1000)

        # 验证拼合后，各文字行的坐标完全正确
        self.assertTrue(np.all(result[200:220, 200:500, :] == 0))  # 第一行字
        self.assertTrue(np.all(result[400:420, 300:800, :] == 0))  # 第二行字
        self.assertTrue(np.all(result[600:620, 400:900, :] == 0))  # 第三行字 (对应拼接后的 600:620)
        self.assertTrue(np.all(result[710:730, 500:700, :] == 0))  # 底部版权
        print("单元测试 test_bottom_scroll_drop_perfect_stitch 通过！")

if __name__ == "__main__":
    unittest.main()
