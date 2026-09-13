# DeepCat

[![许可证](https://img.shields.io/badge/license-GPL--3.0--or--later-blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11--3.13-blue)](pyproject.toml)
[![平台](https://img.shields.io/badge/platform-Windows-lightgrey)](USER_GUIDE.md)

DeepCat 是一款常驻 Windows 托盘的截图与效率工具，集成框选截图、滚动长图、标注、离线 OCR、划词翻译与问答，以及复制记录、表格记事、稍后阅读和待办。

截图、标注、本地数据整理，以及模型齐备后的两套本地 OCR 可在离线环境使用。在线翻译、网页模型适配、模型下载、WebDAV 等功能会按用户配置联网。复制记录默认启用并按配置自动清理，具体数据流与开关说明见 [隐私说明](PRIVACY.md)。

## 主要功能

| 功能 | 当前能力 |
| --- | --- |
| 截图与长图 | 框选、滚动截图、多屏坐标换算、标注、贴图、保存与复制；长图拼接支持磁盘暂存和完整图像输出。 |
| 文字识别 | `OCR识别` 使用 RapidOCR，`PP-OCRv6识别` 使用随包提供的 Small ONNX 模型；`AI识别` 使用所选视觉模型。 |
| 翻译与问答 | 独立选择翻译模型和问答模型，支持流式输出、图片附件、提示词管理，以及 OpenAI 兼容、Responses、Anthropic 等适配。 |
| 划词助手 | 通过快捷键或可选浮窗处理选中文字，提供翻译、解释、总结、回复等动作。 |
| 复制记录 | 保存文本、图片和路径，支持分类、关键词搜索、置顶、编辑、批量操作；首屏预读与后台查询减少切页等待。 |
| 表格记事 | 表格与笔记标签、分组、富文本和 Markdown 展示、图片附件，以及表格汇总等操作。 |
| 稍后阅读与搜索 | 链接采集、筛选和整理；侧边栏搜索用于查找本地记录。 |
| 实用工具 | 待办与休息提醒、资源快捷方式、网络探测、磁盘清理分析、录制及 GIF 导出等。 |
| 数据管理 | 本地备份、恢复、自动备份及可选 WebDAV；恢复流程包含校验和失败回滚。 |
| 模型与本地服务 | 内置 Gemini 网页适配、ChatGPT Web 适配、腾讯 HY-MT 本地模型，以及供外部工具调用的本地 API。 |

内置 Gemini 的应用内请求通过进程内通道交给处理器，不监听 `8081`；配置中的 `http://127.0.0.1:8081` 保留为路由标识。Gemini 对外访问、独立启动的服务，以及供外部工具连接的本地 API，仍有各自的网络和授权要求。

## 获取与运行

当前代码面向 Windows 10/11，支持 Python 3.11–3.13。正式构建面向 Windows x64；开发和打包建议使用 Python 3.11 或 3.12。

### 使用打包版

如使用项目发布的 Windows 包，请完整解压后运行 `deepcat.exe`，并保留同级 `_internal` 目录。打包版不要求另装 Python。程序默认在可执行文件所在目录保存配置和数据，因此应放在具有写入权限的目录中。

升级时先退出程序并备份个人数据，再替换程序文件。具体目录及升级方式见 [使用指南](USER_GUIDE.md)。

### 从源码运行

需要 Git、Git LFS 和受支持的 Python。若使用 Fork，请替换下面的仓库地址。

```powershell
git lfs install
git clone https://github.com/pipabcc/deepcat.git
cd deepcat
git lfs pull
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe scripts\verify_ppocrv6_models.py
.\.venv\Scripts\python.exe main.py --gui
```

PP-OCRv6 的 ONNX 权重由 Git LFS 管理。下载源码 ZIP 不一定能得到实际权重文件，应以模型校验结果为准。程序不会在首次 OCR 时自动下载缺失的 PP-OCRv6 模型。

## 开始使用

1. 在“截图设置”中选择输出位置和截图后动作，按 `F1` 框选截图，按 `F2` 进入滚动截图。
2. 截图后通过工具条标注、保存、复制、贴图或识别文字。
3. 在“模型管理”中配置所用服务，分别选择翻译与问答模型，并测试配置。
4. 在侧边栏底部齿轮打开“设置”，按需要调整划词、自动复制和各功能模块的开关。
5. 在“复制记录”“表格记事”“稍后阅读”中整理内容；重要数据可通过“数据管理”备份。

关闭主窗口通常会隐藏到托盘。需要结束程序时，在托盘菜单选择“退出”。

## 默认快捷键

| 快捷键 | 作用 |
| --- | --- |
| `F1` | 框选截图 |
| `F2` | 滚动截图 |
| `F3` | 稍后阅读链接采集 |
| `Alt+Space` | 显示或切换智能问答窗口 |
| `Ctrl+Space` | 划词翻译；在 GUI 滚动截图过程中用于暂停或继续 |
| `Ctrl+B` | 对选中文字打开划词自选面板 |
| `Esc` | 取消当前截图或结束相应浮窗操作 |

截图、阅读、问答和划词的触发键可在“截图设置”中调整。划词快捷键依赖“设置”中的划词开关；捕获过程的控制键及不同阶段的含义见 [使用指南](USER_GUIDE.md)。普通全局热键使用 Windows 系统注册，注册冲突会给出提示。

## 本地 API 与模型

外部工具可以连接 DeepCat 的本地 API，默认地址为 `http://127.0.0.1:11888`，默认测试密钥为 `sk-deepcat-local`。

```powershell
.\.venv\Scripts\python.exe main.py --serve-translate
```

主要接口包括 `GET /v1/models`、`POST /v1/chat/completions`、`POST /v1/responses` 和 `POST /translate`。聊天接口默认使用问答模型，翻译接口使用翻译模型。

- 接入、参数、流式响应和网络自检见 [本地 API 文档](LOCAL_API_SERVICE.md)。
- HY-MT 的模型文件、llama.cpp 和启动方式见 [本地翻译模型指南](HY_MT_LOCAL_TRANSLATION_GUIDE.md)。
- 网页模型适配依赖上游网页、账号状态和网络环境，项目中的模型名称及映射不代表上游官方服务承诺。

## 开发与构建

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -m "not network and not slow" --timeout=120
```

依赖范围和 Python 版本以 [pyproject.toml](pyproject.toml) 为准。正式打包使用 PyInstaller；Nuitka 与混合构建脚本保留为实验方案。

| 文档 | 内容 |
| --- | --- |
| [使用指南](USER_GUIDE.md) | 界面操作、快捷键、数据位置和常见问题 |
| [构建说明](BUILDING.md) | 环境、测试、模型校验、打包与成品验证 |
| [贡献指南](CONTRIBUTING.md) | 提交问题和改动的流程 |
| [架构说明](deepcat/ui/ARCHITECTURE.md) | UI 聚合类、服务、线程及数据边界 |
| [更新日志](CHANGELOG.md) | 当前未发布变更与功能基线 |
| [开源发布清单](OPEN_SOURCE_CHECKLIST.md) | 仓库公开和发布前的核对事项 |

## 隐私、安全与许可证

配置和数据默认保存在本地，在线功能会把所需内容发送给选定服务。API 密钥、本地历史和日志不应直接提交到公开仓库；详见 [隐私说明](PRIVACY.md) 和 [安全政策](SECURITY.md)。

本项目按 **GPL-3.0-or-later** 授权，见 [LICENSE](LICENSE) 和 [COPYRIGHT](COPYRIGHT)。第三方代码、运行库和模型适用各自的许可证，见 [第三方声明](THIRD_PARTY_NOTICES.md)、[依赖快照](THIRD_PARTY_LICENSES.md) 和 [许可证原文](THIRD_PARTY_LICENSES_FULL.md)。

## 友情链接

- [LinuxDo](https://linux.do/)
