# 第三方组件与许可证

DeepCat 自有代码按 `GPL-3.0-or-later` 授权。Python 包、原生运行库、模型和其他资源适用各自的许可证，不能把项目许可证当作对全部第三方内容的统一授权。

直接依赖与版本范围见 [pyproject.toml](pyproject.toml) 及 requirements 文件。当前环境的分发版本和许可证元数据见 [依赖快照](THIRD_PARTY_LICENSES.md)，对应的可提取许可证原文见 [完整文本](THIRD_PARTY_LICENSES_FULL.md)。

这两份报告是可复核的环境快照，不是锁文件，也不替代对实际发布包的许可证审核。更换 Python、依赖版本、模型或可选工具后，应重新生成和核对。

## 主要组件

| 组件 | 用途与许可说明 |
| --- | --- |
| PyQt6 | GUI Python 绑定，使用 GPL v3 / 商业双授权；不能把 Qt 的 LGPL 许可直接套用于 PyQt6。 |
| Qt | 图形、窗口、SVG 等运行库；按所使用模块与分发包保留相应许可和第三方声明。 |
| NumPy、OpenCV、Pillow、MSS | 数组、图像、文件与屏幕采集；保留各版本的许可证和原生组件声明。 |
| pynput、pyautogui、pywin32、comtypes、pywinauto | 输入、Windows 互操作与辅助功能；遵守各自分发包的许可。 |
| RapidOCR、PaddleOCR、PaddleX、ONNX Runtime | OCR 与推理；Python 包和模型分别核对。 |
| httpx、requests、curl_cffi、websockets 等 | HTTP、网页适配及通信；注意其中可能附带的原生网络库。 |
| pypdf、pypdfium2 等 | PDF 处理及相关依赖，按实际使用与打包情况保留许可。 |
| PyInstaller | 正式构建工具；其引导程序例外允许按相应条件分发生成的应用。 |
| Nuitka 等 | 实验构建所用工具，适用其自身许可。 |

具体版本和包声明以环境快照及上游许可证为准；可选功能可能额外引入不在基础快照中的组件。

## 模型

PP-OCRv6 Small 模型使用固定清单和哈希校验，并随正式构建收集。许可文件位于 [models/pp-ocrv6-small/LICENSE](models/pp-ocrv6-small/LICENSE)，模型来源及文件信息见该目录的 [model_manifest.json](models/pp-ocrv6-small/model_manifest.json)。

RapidOCR 分发包中的模型和其他第三方资源也需要保留其来源与许可。HY-MT 或用户另行下载的模型，应依据所下载仓库、量化文件和具体版本的条款使用或再分发。

## 原生运行库与可选工具

正式构建限制原生 DLL 搜索路径，VC 运行库优先使用 Qt 配套版本，避免误收集开发工具中的同名 DLL。该技术处理不改变原生组件的分发条件。

如果 `tools/` 包含 llama.cpp、CUDA 组件或其他可执行文件，应单独记录版本、来源及许可，并携带相应文件。不能因为 Python 依赖报告已生成，就认定这些目录也已被覆盖。

## 图标与参考交互

`deepcat/ui/assets/` 中由本项目新增或维护的资源，除文件另有说明外，随项目按 GPL-3.0-or-later 提供。运行中下载的网站图标或用户添加的资源不因此转为本项目所有。

猫咪休息提醒参考了以下项目的交互思路：

- [Cat Gatekeeper](https://github.com/zokuzoku/cat-gatekeeper)
- [Panda Gatekeeper](https://github.com/lengyi2030/panda-gatekeeper)

上游代码与视觉素材可能有不同授权范围。DeepCat 不以其代码的 MIT 许可作为复制受限图片、视频、图标或品牌素材的依据；发布时应核对实际打包资源。

## 发布时保留

保留项目的 [LICENSE](LICENSE)、[COPYRIGHT](COPYRIGHT)、本声明，以及与实际环境和资源对应的许可证材料。记录构建环境并检查发布目录，具体步骤见 [构建说明](BUILDING.md) 和 [开源发布清单](OPEN_SOURCE_CHECKLIST.md)。
