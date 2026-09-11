# HY-MT 本地翻译模型指南

DeepCat 可调用 llama.cpp 提供的本地 HY-MT 翻译模型服务。本指南说明当前内置配置需要的文件和接口，不表示模型已随源码或所有发布包安装。

## 当前内置配置

| 界面名称 | 服务模型名 | 模型相对路径 |
| --- | --- | --- |
| 腾讯模型MT1.5 | `hy-mt15-1.8b-q4_k_m` | `models/hy-mt-1.8b/HY-MT1.5-1.8B-Q4_K_M.gguf` |
| 腾讯模型MT2 | `hy-mt2-1.8b-q4_k_m` | `models/hy-mt2-1.8b/Hy-MT2-1.8B-Q4_K_M.gguf` |

自动启停的模型识别以 `deepcat/local_hunyuan_server.py` 中的 `MODEL_SPECS` 为准，预置界面条目见 `model_catalog.json`。

默认服务地址是 `http://127.0.0.1:8080`，内置示例密钥为 `sk-1234`。这个密钥是公开默认值，不应作为共享服务的保密凭据。

## 准备模型与运行库

1. 从相应上游模型仓库获取所需 GGUF 文件，并确认其使用与分发条款。
2. 将模型放入上表路径。
3. 准备与脚本参数兼容的 llama.cpp Windows 运行库。当前代码约定服务端路径为 `tools/llama.cpp-b9360/llama-server.exe`。

可参考：

- [HY-MT1.5 1.8B GGUF](https://huggingface.co/tencent/HY-MT1.5-1.8B-GGUF)
- [Hy-MT2 1.8B GGUF](https://huggingface.co/tencent/Hy-MT2-1.8B-GGUF)
- [llama.cpp 发布页](https://github.com/ggml-org/llama.cpp/releases)

普通 Git 克隆不会得到被忽略的 HY-MT 模型和 `tools/` 目录；PP-OCRv6 的 Git LFS 配置也不替代这些模型的安装。

## 在 DeepCat 中使用

在“模型管理”选择相应条目，核对：

| 字段 | 示例 |
| --- | --- |
| API 地址 | `http://127.0.0.1:8080` |
| 模型名 | 上表中的服务模型名 |
| API 密钥 | 与所启动服务一致，内置示例为 `sk-1234` |
| 代理 | 本机服务通常使用直连 |

将其设为翻译使用模型后，调用会按配置检查并启动服务。内置管理器通常只维持当前本地模型，请求释放且空闲约 180 秒后停止服务；切换模型可能涉及卸载与重新加载。

HY-MT 面向翻译任务。软件虽然允许为其他用途选择模型，但不应据此把翻译模型视作通用问答模型。

## 手动启动

在项目根目录执行对应脚本：

```powershell
.\start_hy_mt2_server.ps1
```

或：

```powershell
.\start_hy_mt15_server.ps1
```

这两个脚本使用当前固定路径和端口，默认以 CPU 模式启动。`start_hy_mt_server.ps1` 是通用名称的旧入口，目前同样指向 MT2 配置，并不接收文档早期示例中的 `-Port` 参数。

相关脚本还包括 `test_hy_mt_server.ps1`、`stop_hy_mt_server.ps1` 及其 BAT 入口。测试脚本使用 MT2 别名；测试其他模型时，应使用下面的请求示例并修改模型名。停止脚本会按进程名处理 `llama-server`，同时运行多个 llama.cpp 服务时应先确认目标。

## 参数与接口

当前启动脚本的主要默认值：

| 参数 | 值 |
| --- | --- |
| `--host` / `--port` | `127.0.0.1` / `8080` |
| `--ctx-size` | `4096` |
| `--threads` / `--threads-batch` | `4` / `4` |
| `--parallel` | `1` |
| `--n-gpu-layers` | `0` |
| `--temp` / `--top-p` / `--top-k` | `0.7` / `0.6` / `20` |
| `--repeat-penalty` / `--predict` | `1.05` / `4096` |

内存、显存和速度由模型、量化、上下文、运行库及硬件共同决定。本项目不提供统一的 tokens/s 保证。使用 GPU 需要兼容的运行库和驱动，并相应调整启动参数；仅安装显卡不会自动改变上述 CPU 配置。

外部客户端通常可使用 `http://127.0.0.1:8080/v1` 作为 Base URL。测试模型列表：

```powershell
$headers = @{ Authorization = "Bearer sk-1234" }
Invoke-RestMethod -Uri "http://127.0.0.1:8080/v1/models" -Headers $headers
```

翻译请求示例：

```powershell
$headers = @{ Authorization = "Bearer sk-1234" }
$body = @{
    model = "hy-mt2-1.8b-q4_k_m"
    messages = @(
        @{
            role = "user"
            content = "Translate the following segment into Chinese, without additional explanation.`n`nHello world"
        }
    )
    temperature = 0.7
    top_p = 0.6
    top_k = 20
    repeat_penalty = 1.05
    max_tokens = 4096
} | ConvertTo-Json -Depth 8
Invoke-RestMethod -Uri "http://127.0.0.1:8080/v1/chat/completions" -Method Post -ContentType "application/json" -Headers $headers -Body $body
```

DeepCat 对识别出的 HY-MT 配置使用相应翻译提示词和参数适配。若更改别名、提示词或服务类型，应确认仍匹配所需的适配逻辑。

## 排查

| 问题 | 检查项 |
| --- | --- |
| 服务端或模型不存在 | 路径、文件名、模型完整性，以及发布包是否包含可选资源。 |
| 端口被占用 | 检查 `8080` 的实际占用进程，不直接结束无关实例。 |
| 服务已监听但连接失败 | 检查本机地址、代理、鉴权和网络策略；HY-MT 是真实 HTTP 服务，与 Gemini 的应用内通道不同。 |
| 返回原文或输出不合预期 | 检查模型别名、翻译提示词、目标语言及输入长度。 |
| 长文报错或资源不足 | 检查上下文长度、内存、请求参数和服务日志，缩小输入后复验。 |

如需让外部工具统一使用 DeepCat 当前翻译模型，可连接 `11888` 本地 API 网关，详见 [本地 API 文档](LOCAL_API_SERVICE.md)。
