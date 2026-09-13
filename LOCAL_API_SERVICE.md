# DeepCat 本地 API 与应用内模型通道

DeepCat 有多种本地能力，它们的通信边界不同：

| 能力 | 通信方式 | 使用入口 |
| --- | --- | --- |
| 内置 Gemini | 进程内处理请求和流式响应，不监听 `8081` | DeepCat 内部的模型测试、模型列表、翻译和问答。 |
| 本地 API 网关 | 真实 HTTP 服务，默认 `127.0.0.1:11888`，并尝试监听同端口 `::1` | 沉浸式翻译、Pot、ChatBox 等外部客户端。 |
| ChatGPT Web / HY-MT | 独立的适配或模型服务，当前默认端口分别为 `8082` / `8080` | 对应的模型配置。 |

内置 Gemini 配置中的 `http://127.0.0.1:8081` 是兼容现有配置的路由标识。浏览器或其他进程不能据此假定该端口存在服务。需要独立 Gemini HTTP 服务时，可使用源码中的 `deepcat.gemini_web2api` 入口。

进程内通道不需要给内部 TCP 连接设置白名单，也不依赖固定 EXE 路径。访问 Google 等上游服务仍属于外部联网，会受网络、代理、账号及系统策略影响。

ChatGPT Web 的模型 ID、即时流式输出、快照替换扩展及兼容边界见 [ChatGPT Web 说明](CHATGPT_WEB.md)。快照扩展针对其 `8082` 适配接口，不等同于本地 API 网关所有模型的通用字段。

## 启动本地 API 网关

GUI 中进入“模型管理”，开启底部“本地API”。取消勾选会停止服务，开关状态会保存在配置中。

源码命令行入口：

```powershell
.\.venv\Scripts\python.exe main.py --serve-translate
```

默认地址为 `http://127.0.0.1:11888`，默认测试密钥为 `sk-deepcat-local`。请求应带上：

```text
Authorization: Bearer sk-deepcat-local
```

可显式设置地址、端口和密钥：

```powershell
.\.venv\Scripts\python.exe main.py --serve-translate --translate-host 127.0.0.1 --translate-port 11888 --translate-api-key "替换为自己的密钥"
```

空密钥表示不校验。默认测试密钥是公开值，不适合充当共享服务的访问凭据；面向其他设备时，应先确定访问范围和认证方式。

## 外部客户端配置

| 客户端要求的字段 | 示例 |
| --- | --- |
| OpenAI Base URL | `http://127.0.0.1:11888/v1` |
| 完整聊天接口地址 | `http://127.0.0.1:11888/v1/chat/completions` |
| 模型 | `deepcat-translate`，或 `/v1/models` 返回的已配置名称 |
| API Key | 当前服务密钥 |

会自动追加接口路径的客户端应填写 Base URL，不要再填完整 `/chat/completions` 或 `/responses` 地址。兼容路径只用于处理常见客户端差异，不应替代正确配置。

`deepcat-translate` 是路由模型标识。聊天与 Responses 默认读取“问答使用模型”；`/translate` 和显式强制翻译请求读取“翻译使用模型”。请求中指定已配置模型时，按对应配置执行。

## 主要接口

| 方法与路径 | 说明 |
| --- | --- |
| `GET /` | 服务信息。 |
| `GET /health`、`GET /ready` | 服务存活状态，不调用上游模型；不要求 API Key。 |
| `GET /v1/models` | 可用的配置模型列表，需要相应鉴权。 |
| `POST /v1/chat/completions` | 普通聊天或强制翻译，支持 JSON 与 SSE。 |
| `POST /v1/responses` | Responses 风格请求与流式事件。 |
| `POST /translate` | 翻译专用接口，支持 JSON 与 SSE。 |
| `GET /translate` | 查询参数形式的翻译入口；敏感文本优先用 POST。 |

“OpenAI 兼容”描述的是已实现的接口行为，不代表覆盖上游服务的全部端点或功能。模型本身的能力、参数和内容限制仍由实际后端决定。

### 翻译示例

```powershell
$headers = @{ Authorization = "Bearer sk-deepcat-local" }
$body = @{
    text = "Hello world"
    source_lang = "auto"
    target_lang = "zh-CN"
} | ConvertTo-Json
Invoke-RestMethod -Uri "http://127.0.0.1:11888/translate" -Method Post -Headers $headers -ContentType "application/json" -Body $body
```

响应包含 `text`、`translation`、源语言、目标语言和模型字段。具体译文取决于模型，不应把示例文本当作固定结果。

输入兼容 `text`、`q`、`query`、`input`、`sourceText`、`source_text`、`text_list`、`texts`、`sentence`、`sentences`、`messages` 和 `data.text` 等字段；建议新接入使用 `text`、`source_lang`、`target_lang`。

### 聊天与 Responses

聊天请求示例：

```json
{
  "model": "deepcat-translate",
  "messages": [{"role": "user", "content": "请用一句话介绍你能提供的帮助。"}],
  "stream": false
}
```

将 `stream` 设为 `true` 可获取 SSE。聊天增量为 `chat.completion.chunk`，以 `data: [DONE]` 结束；`/translate` 使用 `translation.chunk`。Responses 路径使用相应的 Responses 事件。

需要通过聊天接口翻译时，可设置 `force_translate: true`，或使用项目支持的 `<translate_input>...</translate_input>` 文本标记。客户端断开后会触发取消处理，但上游是否立即停止仍取决于具体后端。

## IPv4、IPv6 与启动自检

本地 API 网关保留 IPv4 主监听，并尝试创建 `::1` 的同端口监听。IPv6 不可用时不会强制关闭 IPv4。使用 `localhost` 的客户端可能优先选择 IPv6，排查时应分别测试两个地址。

当前自检分别探测 `127.0.0.1` 和 `::1`，单地址默认超时为 1.5 秒，并把结果用于界面提示。端口处于监听状态只说明绑定成功，不代表完整 HTTP 请求一定能完成。

```powershell
Test-NetConnection 127.0.0.1 -Port 11888
Test-NetConnection ::1 -Port 11888
Invoke-RestMethod -Uri "http://127.0.0.1:11888/health"
```

TCP 测试成功不等于模型可用，健康检查成功也不等于上游模型能生成结果。应继续区分鉴权、模型选择、代理与上游错误。

## 网络授权与排查

连接超时可能来自地址选择、服务处理、代理或网络策略，不能仅凭 `SYN_SENT` 或超时就断定是 Windows 防火墙。

1. 确认客户端连接的是本地 API 网关，而不是内置 Gemini 的进程内路由标识。
2. 核对端口、客户端实际请求路径和模型配置。
3. 比较 IPv4、IPv6 与 `/health` 的结果；本机请求应直连，不经过不必要的代理。
4. 若确认受到安全策略限制，按该环境允许的方式授权；不要通过关闭整体防护来排查或使用服务。

打包版在相应自检失败时可能显示“一键允许”。这是**用户主动选择的系统配置操作**：点击后请求 UAC 提权，尝试为当前 EXE 添加 Windows 防火墙入站允许规则，然后重新自检。取消 UAC 不会完成授权；规则也不保证能覆盖其他安全软件或组织策略。

源码运行不提供这个按钮。同名规则可能同时存在，不能假定重复点击一定覆盖旧规则；已禁用的规则也不等同于阻止规则，应按实际生效的策略判断。

上述授权针对需要真实 TCP 的外部 API 服务。内置 Gemini 的应用内调用不依赖这项授权，相关进程内处理见 [架构说明](deepcat/ui/ARCHITECTURE.md)。

## 日志与已知边界

应用日志和崩溃日志默认关闭，按需在设置中开启。诊断信息可能包含请求路径、错误、消息或输出片段；日志中的部分敏感字段会脱敏，但公开提交前仍需人工复核。

接口路径错误通常返回 404；鉴权失败、上游拒绝、限流和超时应依据具体响应处理。网页适配依赖上游页面和会话，DNS 预解析及重试改善连接诊断，不代表可以绕过外部网络策略。

数据流与敏感信息说明见 [PRIVACY.md](PRIVACY.md)，开发验证见 [BUILDING.md](BUILDING.md)。
