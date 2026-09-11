# 贡献指南

欢迎提交可复现的问题、文档修正和功能改进。一个改动应围绕明确问题，说明现有行为、预期行为和验证结果。

## 准备环境

环境与版本要求以 [pyproject.toml](pyproject.toml) 和 [构建说明](BUILDING.md) 为准。源码检出后先获取 Git LFS 模型，再安装开发依赖：

```powershell
git lfs install
git lfs pull
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe scripts\verify_ppocrv6_models.py
.\.venv\Scripts\python.exe main.py --gui
```

## 提交问题

请提供 Windows 版本、源码或 EXE 运行方式、版本或提交号、复现步骤，以及实际结果。UI 问题可附经过脱敏的截图，网络问题应说明调用的是应用内 Gemini、外部本地 API，还是上游模型服务。

安全问题通过 [安全政策](SECURITY.md) 中的私密渠道报告。普通 Issue 不应包含 Cookie、令牌、密钥、完整配置、私有截图或聊天记录。

## 修改代码

- 保持改动小而清楚，避免把无关重构放进同一个 PR。
- 遵循已有命名和格式，注释解释必要的原因或约束。
- UI 入口和业务职责见 [架构说明](deepcat/ui/ARCHITECTURE.md)；新增逻辑优先放入对应模块。
- 注意 Qt 主线程边界、工作线程生命周期、请求取消，以及数据恢复或清理的失败路径。
- 涉及本地请求时区分进程内通道与真实 HTTP 服务，不把连接超时直接当成防火墙结论。

## 验证与 PR

运行与改动相关的测试，再按影响范围扩展：

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not network and not slow" --timeout=120
git diff --check
```

对修改的 Python 文件运行 Ruff，并按项目的 Black 配置格式化。GUI、全局热键、OCR、数据恢复或打包改动，应说明对应的实际验证场景；仅凭自动化用例通过不能代替所有桌面操作检查。

PR 描述应包含问题、最终行为、验证结果和已知限制。用户可见行为变化同步更新 [使用指南](USER_GUIDE.md) 和 [更新日志](CHANGELOG.md)。无法执行的检查应明确说明，不填写未经执行的通过结果。

## 仓库内容

不要提交个人 `settings.json`、`data/`、截图、日志、下载缓存、虚拟环境或打包产物。`.gitignore` 不能清除已经进入 Git 历史的内容；公开前还应检查跟踪文件和历史，见 [发布清单](OPEN_SOURCE_CHECKLIST.md)。

性能采样和问题附件应先脱敏，再按本次问题所需的最小范围提供。请勿把本机路径、第三方会话或测试输入放进公开示例。

## 授权

提交贡献表示你有权提供这些内容，并同意按项目的 `GPL-3.0-or-later` 许可发布。引入第三方代码、模型、图标或运行库时，同时保留来源和适用的许可证。
