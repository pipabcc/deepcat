# PP-OCRv6 Small ONNX 模型

本目录随 DeepCat 安装包离线分发 PaddlePaddle 官方 PP-OCRv6 Small 检测与识别模型，运行时不会下载模型。

## 固定来源

- 检测模型：`PaddlePaddle/PP-OCRv6_small_det_onnx`
  - revision：`28fe5895c24fd108c19eb3e8479f4ab385fbfc62`
- 识别模型：`PaddlePaddle/PP-OCRv6_small_rec_onnx`
  - revision：`b8f84f0b80c529de40b4fbb3544b84fa7233a513`
- 模型集合：https://huggingface.co/collections/PaddlePaddle/pp-ocrv6
- 许可证：Apache License 2.0

`model_manifest.json` 固定记录六个推理文件的大小与 SHA-256。正式构建前执行：

```powershell
python scripts\verify_ppocrv6_models.py
```

任一文件缺失、大小不匹配或哈希变化都会中止构建。
