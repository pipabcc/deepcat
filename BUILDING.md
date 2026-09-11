# 开发、测试与构建

## 环境与依赖

支持 Windows 上的 Python 3.11、3.12、3.13，建议使用 64 位 Python 3.11 或 3.12 进行开发和正式构建。依赖范围以 [pyproject.toml](pyproject.toml) 为准；`requirements.txt`、`requirements-build.txt`、`requirements-dev.txt` 分别服务于运行、构建及开发环境安装。

```powershell
git lfs install
git lfs pull
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
```

使用新建虚拟环境，避免系统 Python、其他工具的包或本地配置混入构建。两种 OpenCV 分发包按项目要求固定为同一版本，共用 `cv2` 命名空间。

## OCR 模型校验

PP-OCRv6 Small 位于 `models/pp-ocrv6-small/`。ONNX 权重由 Git LFS 管理，模型清单记录固定来源、文件大小和 SHA-256。

```powershell
.\.venv\Scripts\python.exe scripts\verify_ppocrv6_models.py
```

校验失败应中止构建并修复源码检出或模型文件，不能把 LFS 指针文本当作模型分发。正式 PyInstaller 和实验构建入口均包含这一检查。

RapidOCR 的资源来自安装的分发包。可选 HY-MT 模型与 llama.cpp 需要单独准备，不应据“附带离线 OCR”推断发布包还附带了所有大语言模型。

## 测试与代码检查

```powershell
.\.venv\Scripts\python.exe -m compileall -q deepcat tests
git diff --check
.\.venv\Scripts\python.exe -m pytest -m "unit and not network" --timeout=120
```

GUI 测试可在离屏平台运行：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
.\.venv\Scripts\python.exe -m pytest -m "gui and not network" --timeout=180
Remove-Item Env:QT_QPA_PLATFORM
```

完整离线测试：

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not network" --timeout=180
```

对本次修改的 Python 文件运行 `python -m ruff check`，按 `pyproject.toml` 中的 Black 配置格式化。`tests/conftest.py` 会补充主要测试分类并记录 Qt 回调异常；需要真实网络的用例应标记 `network`。

离屏测试不覆盖真实托盘、全局按键、显示缩放和系统网络策略，发布前还要验证成品。涉及真实数据的测试应使用独立目录。

## PyInstaller 正式构建

正式构建配置是 [packaging/deepcat_gui.spec](packaging/deepcat_gui.spec)。面向 GitHub 发布时，建议使用独立、干净的输出目录：

```powershell
.\.venv\Scripts\python.exe -m PyInstaller packaging\deepcat_gui.spec --noconfirm --clean --distpath dist-release --workpath build-release
```

程序位于 `dist-release/deepcat/deepcat.exe`。必须分发整个 `deepcat` 目录，不能只分发 EXE。

`scripts/build_exe.ps1` 是开发目录的便利入口，会优先使用项目虚拟环境，输出到 `dist/deepcat`。当前脚本会停止运行中的 `deepcat` 并清理 `dist`、`build`，只暂存、恢复 `settings.json` 和 `data/`。因此它不能替代完整的数据备份，其输出也可能包含个人数据，不应直接压缩后公开发布。

### 运行库与资源

当前 spec 会收集 Qt 界面资源、规则文件、OCR 工作进程、PP-OCRv6 模型、PaddleX 所需元数据，以及可用的 `tools/` 资源。

为避免同名 DLL 冲突，构建的依赖搜索路径被限制到当前 Python、Qt 和 Windows 运行库目录。VC 运行库优先选择随 Qt 分发的版本；运行时 hook 保留 DLL 目录句柄。不要通过向输出目录混拷其他程序的 ICU、UCRT 或 Qt DLL 来修复启动错误。

如 `tools/` 中包含可选推理工具，先确认内容、版本与许可证。该目录默认不随 Git 克隆获得。

### 发布目录整理

独立 PyInstaller 输出中的文档和模型资源可能位于 `_internal`。为方便读者，可以把面向用户的文档放到发布目录顶层：

```powershell
$docs = @(
  "README.md", "LICENSE", "COPYRIGHT", "USER_GUIDE.md",
  "LOCAL_API_SERVICE.md", "HY_MT_LOCAL_TRANSLATION_GUIDE.md",
  "PRIVACY.md", "SECURITY.md", "THIRD_PARTY_NOTICES.md",
  "THIRD_PARTY_LICENSES.md", "THIRD_PARTY_LICENSES_FULL.md"
)
Copy-Item -LiteralPath $docs -Destination .\dist-release\deepcat
Copy-Item -LiteralPath .\model_catalog.json -Destination .\dist-release\deepcat
```

公开包中不应包含个人 `settings.json`、数据库、截图、日志、下载缓存或测试生成的数据。进行成品验证时使用独立测试副本，随后从干净构建目录生成 ZIP 和 SHA-256。

## 成品验证

至少覆盖下列场景，并记录实际结果，不把“构建完成”当成“功能验证通过”：

1. 新目录中的 EXE 能显示主窗口和托盘；“模型管理”“设置”等主要页面可打开。
2. 框选和滚动截图可启动、取消或结束；快捷键冲突能给出提示。
3. 复制记录首次进入有可解释的记录、读取或空状态，切页和后台刷新没有先显示完整长标题再省略的闪烁。
4. `OCR识别`、`PP-OCRv6识别`、`AI识别` 菜单存在；两套本地 OCR 在模型齐备的离线环境可完成识别。
5. 内置 Gemini 的应用内模型测试与请求使用进程内通道，未依赖 `8081` 的 TCP 监听；流式输出和取消请求可正常结束。
6. 如验证供外部工具使用的本地 API，检查 `127.0.0.1`、`::1` 的可达性、鉴权和实际请求。授权提示是否出现与本机策略有关。
7. 超长 PNG、JPEG 超限回退、图片复制或 PDF 输出等本次改动涉及的路径能够完成。
8. 通过托盘“退出”结束程序，并检查工作进程和相关服务资源是否回收。

在临时副本上验证时，可给测试进程设置 `DEEPCAT_SKIP_AUTOSTART_SYNC=1`，避免把正式开机启动项切到测试目录。该变量只跳过自启动路径同步，不关闭其他功能，也不改变系统网络规则。

## CI 与实验构建

[Windows CI](.github/workflows/windows-ci.yml) 包含：

- 修改文件的 Ruff 检查和 Git 差异格式检查；
- Python 3.11、3.12、3.13 的编译与核心离线测试；
- Python 3.11 的完整离线测试；
- Python 3.11 上的模型校验、PyInstaller 构建与 EXE 存在性检查。

CI 中的 EXE 存在性检查不等同于上面的交互验证。`scripts/nuitka_build.ps1` 和 `scripts/build_secure_onedir.ps1` 是实验入口，不作为正式发布基线。

发布前同步 [更新日志](CHANGELOG.md)、[第三方声明](THIRD_PARTY_NOTICES.md) 和 [开源发布清单](OPEN_SOURCE_CHECKLIST.md)。
