# ChatGPT Web 模型与流式输出

ChatGPT Web 适配器使用软件中保存的网页登录态访问上游服务。本地默认地址为 `http://127.0.0.1:8082`，其模型目录与 OpenAI 官方 API 的模型目录分别维护。

## 预置模型

| 界面名称 | 请求模型 ID | 说明 |
| --- | --- | --- |
| ChatGPT Web 自动（网页模式） | `auto` | 默认文本模型；未指定模型时也使用网页自动路由。 |
| GPT-5.5 Thinking | `gpt-5-5-thinking` | 保留固定型号配置入口。 |
| GPT-5.6 Luna（固定型号） | `gpt-5-6` | 同时接受 `gpt-5.6` 输入别名。 |
| GPT-6 | `gpt-6` | 保留配置入口，可用性取决于网页账号权限。 |
| ChatGPT Web 生图 | `gpt-image-2` | 原有默认生图入口。 |
| ChatGPT Web 生图 2.5 | `gpt-image-2.5` | 网页 `picture_v2` 生图别名，可独立指定上游对话模型。 |

旧目录中的 GPT-5、GPT-5.1、GPT-5.2、GPT-5.3 及相应旧款 Mini 不再列出。本地 ChatGPT Web 配置中的这些旧 ID 会迁移到默认的 `auto`，模型备注、登录态、代理和翻译／问答选择保持原有配置；远程服务及自定义模型 ID 不参与迁移。生图模型仍保留独立入口。

内置 `/v1/models` 返回的是适配器提供的配置入口，不能据此确认当前账号拥有全部型号。2026-09-12 的实测中，GPT-5.5 Thinking 和 GPT-5.6 均完成了流式问答；该账号的上游模型目录没有列出 `gpt-6`。适配器不会把 GPT-6 请求自动改为其他模型，也不为新模型编造上下文长度或输出上限。

## 网页生图 2.5 与图片预览

参考 [basketikun/chatgpt2api PR #406](https://github.com/basketikun/chatgpt2api/pull/406) 的网页 `picture_v2` 分支，支持在 `/v1/images/generations` 和 `/v1/chat/completions` 请求中使用 `gpt-image-2.5`。设置中的 ChatGPT Web 分组提供“ChatGPT Web 生图 2.5”入口，原有生图选择和默认文本模型 `auto` 保持各自的设置。

```json
{
  "model": "gpt-image-2.5",
  "prompt": "生成一张白底橙色圆形图片，不要文字",
  "response_format": "url"
}
```

此 ID 是网页适配器的配置别名。实际图片生成能力由网页上游及当前账号决定，不能据此推断 OpenAI 官方 API 提供同名型号。调研时该 PR 尚未合并。

可在请求体或适配器配置中指定 `default_upstream_model_name_25`，用于 2.5 入口的上游对话模型；留空时沿用 `default_upstream_model_name`，两项均未配置时使用 `auto`。图片 prepare 和正式请求使用同一个上游 ID。参考图附件继续通过已有上传流程处理，返回结果保留请求的 `gpt-image-2.5` 别名。

回答中的 HTTP/HTTPS 图片、生成后的本地图片和内嵌图片均使用独立缩略图控件，缩略图最大为 340 × 380。网络图片异步加载，失败后可点击重试；来源文章不会作为图片重复显示。旧历史里相邻的来源文章和 OpenAI 缩略图标记在展示时合并，历史原文保持原样。

点击缩略图打开应用内大图对话框，支持 Esc 关闭；悬停或右键可复制和保存原始图片，缩略图缩放不改变保存的数据。通用网络图片请求不携带 ChatGPT 登录凭据。受保护的 ChatGPT 图片仍由后端下载并转为本地文件后显示。

2026-09-13 使用本机已保存的网页登录态，`gpt-image-2.5` 完成一次真实生图请求，约 27.6 秒返回本地 PNG。另用历史中的公开图片地址验证了异步缩略图和内置大图预览，未调用外部程序。这些结果代表本次账号和网络状态。

## 中文输入法与输入刷新

AI 输入框记录输入法的预编辑和上屏状态。选词期间，回车和 Esc 交给输入法处理，窗口不会把选词动作当作发送或关闭；回答区的延迟焦点重置也会检查预编辑状态。

附件整理与输出区布局使用 90 毫秒定时器合并，避免在每次按键或上屏事件中同步执行。选词期间暂停整理，提交后读取最新输入。草稿仍在关闭、切换历史等既有时机保存，不随每个字符写盘。

本机对照测试连续发送 40 次 Qt 上屏事件：修改前执行 40 次附件整理、输出清理和标题栏刷新，修改后在输入停顿后执行 1 次；累计同步事件处理耗时约从 539 毫秒降至 10 毫秒。这是应用层连续输入测试，耗时不代表第三方输入法候选窗口的响应时间。

Qt 输入法事件回归覆盖了连续预编辑、直接上屏、失去焦点、快捷键和连续输入。财神输入法的实际候选窗口还需要在用户日常环境中观察，不能仅凭这些模拟事件排除输入法自身的性能问题。

## 流式行为

默认收到正文后立即向客户端发送，不再等待整段生成结束或最终会话轮询。SSE 支持 `data:` 与 `data: ` 两种写法，以 `data: [DONE]` 结束，并返回 `X-Accel-Buffering: no`。

### 首段等待时间与连接复用

适配器按登录配置、上游地址、代理和 User-Agent 隔离连接池，同一连接每次只借给一个请求。成功完成后归还连接；失败或取消时关闭该连接，服务停止或切换配置时清理闲置连接。

上游验证成功的登录信息最多缓存 120 秒，临近令牌过期时提前失效。校验失败后临时沿用的旧令牌不会记为已验证缓存。后续请求可以省去重复的登录态查询，并复用网络连接；上游要求的每轮请求校验仍正常执行。

本轮提问被上游接收后，适配器会在后台为下一轮提前执行校验。预取结果按登录配置、实际访问令牌、设备和代理隔离，最多保留 60 秒，领取后立即移除；不会把同一个结果用于两次提问。后台失败时，下次请求正常执行同步校验。服务停止或切换配置后，旧后台任务的结果不再进入缓存。

若服务器以 HTTP 403 明确拒绝已预取的校验结果，适配器会重新校验一次再提交；额度错误不会因此重发。调试时可通过 `enable_sentinel_prefetch: false` 关闭预取。

同一 `auto` 模型、同一短问题的交替实测中，后续请求首段从 5.171 秒降至 3.467 秒；命中缓存后，登录态准备和校验领取合计约 0.0023 秒。该结果仅代表本次网络与上游状态，首次请求仍需要完成初始化。

首段耗时还包含上游接收请求、模型处理和网络传输。缓存命中速度不能直接作为首段回答速度。GPT-5.5 Thinking 当前仍沿用扩展思考设置，连接优化不会自动改用其他模型或降低思考档位。

网页响应可能包含新增片段、完整消息快照、正文补丁和 WebSocket 续流。适配器分别处理这些事件：

- 普通新增片段原样追加，保留重复字、空格、空行和 Markdown 符号。
- 完整消息快照及显式正文替换更新已有内容，重复快照不再次追加。
- 未闭合的引用标记暂存到后续分片；引用补全或前文修正可以更新已显示的正文。
- 紧凑补丁沿用上一条补丁的路径，避免多段正文在错误位置续写。
- WebSocket 重发同一编号和同一载荷时只处理一次；没有编号或编号不同的相同文字仍按正常增量处理。
- `analysis`、工具请求和其他非正文事件不混入回答正文。

### 桌面客户端的快照协商

DeepCat 的问答和翻译工作线程在本地 ChatGPT Web 请求中发送：

```json
{
  "model": "auto",
  "messages": [{"role": "user", "content": "介绍流式输出"}],
  "stream": true,
  "stream_snapshot_replacements": true
}
```

通常的响应仍使用 `choices[].delta.content`。当上游修改已发送的前文时，服务发送完整替换帧：

```json
{
  "choices": [{
    "index": 0,
    "message": {"role": "assistant", "content": "修正后的完整回答"},
    "deepcat_replace": true,
    "finish_reason": null
  }]
}
```

支持此扩展的客户端应将当前回答、显示内容和历史缓存整体替换为 `message.content`，包括内容为空的撤回事件。后续 `delta.content` 继续追加。

这是 DeepCat 的协商扩展。未声明支持的 OpenAI 兼容客户端只接收可以追加的内容，服务不会向它追加一整份重写快照；这种客户端无法在同一条标准增量流中修正已显示的前文。

`buffer_stream_until_handoff` 和 `final_fetch_after_stream_completion` 仍可显式设为 `true`，用于兼容需要整段缓冲或最终会话对账的场景。两者默认均为 `false`。

## Aurora 调研结果

参考 [aurora-develop/aurora](https://github.com/aurora-develop/aurora)，调研版本为 `15983ced4d3a7f9faa3b5871086ebbefa8f8881b`。

| 对照项 | DeepCat 当前情况 |
| --- | --- |
| 逐事件写入、刷新与结束标记 | 已恢复即时发送，补齐结束标记和禁止代理缓冲响应头。 |
| 网页补丁协议 | 已支持嵌套补丁、消息元信息、整条消息与正文数组更新、紧凑路径续写，并过滤非正文 channel。 |
| 快照修正与续流重放 | 通过桌面客户端协商更新已有回答，按续流编号识别重复投递。 |
| 账号可见模型同步 | 当前保留静态配置目录；账号目录的持续同步与权限标注仍有差距。 |
| 文本请求的初始化状态 | 保留现有鉴权、warmup 和 conversation 流程；Aurora 的完整 init／prepare 状态协商尚未在普通文本请求中统一实现。 |

调研重点包括 Aurora 的 `internal/httpstream/stream.go`、`internal/sseparser/parser.go`、`internal/chatgpt/handler_response.go`、`internal/chatgpt/models.go` 和 `typings/chatgpt/request.go`。网页协议会变化，后续调整应以实际返回的事件、账号可见模型和回归结果为依据。

## 网页可用但软件提示错误

流内错误会保留上游明确给出的错误码和说明。单独的 `Something went wrong` 或 `Please try again later` 不作为额度耗尽的证据；明确的使用上限说明或 HTTP 429 才按对应限制处理。

排查时应比较同一浏览器账号、实际模型 ID 和思考档位。2026-09-12 核对的账号目录中，`gpt-5-6`、`gpt-5-6-mini`、`gpt-5-6-t-mini` 和 `gpt-5-6-t-mini-mini` 都显示为 `GPT-5.6 Luna`，仅比较显示名无法确认两次请求使用的是同一型号。不同浏览器也可能保留不同的登录状态或模型选择。

若上游返回 `You've hit your limit. Please try again later.`，适配器会展示这段说明。它不会通过自动换账号、换模型或重复发送同一提问来处理限制。只有在核对浏览器实际选择与登录态之后，才能判断网页和软件表现不同的具体原因。

2026-09-12 的 Edge DevTools 对照中，网页成功请求实际使用 `model: auto`；项目使用 `auto` 也成功完成流式回答，而固定的 `gpt-5-6` 请求返回上述使用上限提示。因此默认采用独立标示的网页自动模式；显式选择固定型号时仍按该 ID 请求。

上游可能先返回 HTTP 200 建立 SSE 连接，再在流内返回错误。排查时应同时查看响应流中的错误内容和完成标记。

## 验证入口

`tests/test_chatgpt_streaming.py` 使用线程闸门确认上游尚未结束时 HTTP 客户端已收到首段正文。`tests/test_chatgpt_stream_replacements.py` 覆盖新闻正文后的引用残片、完整快照重写、补丁续写、重复投递、断线续流和普通 OpenAI 客户端兼容。`tests/test_post_capture_actions_worker.py` 验证两条桌面消费路径都能替换已有正文。`tests/test_chatgpt_transport.py` 验证跨线程连接复用、账号隔离、缓存失效和取消释放；`tests/test_chatgpt_upstream_errors.py` 验证错误说明与分类。

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_chatgpt_streaming.py tests/test_chatgpt_stream_replacements.py tests/test_chatgpt_model_catalog.py tests/test_chatgpt_web2api.py tests/test_post_capture_actions_worker.py -q
```

真实账号验证需要软件中有效的登录态；模拟上游测试用于稳定复现重写和重放事件，不能代替真实账号的权限检查。

图片与输入法的专项回归：

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_chatgpt_image25.py tests/test_markdown_images.py tests/test_chat_image_previews.py tests/test_ai_input_ime.py -q
```

这些用例使用本机 HTTP 图片服务和 Qt 输入法事件，不需要真实账号。其中包括网络加载与取消、错误重试、来源链接过滤、内置预览、复制／保存原图以及上屏后合并刷新。
