$ErrorActionPreference = "Stop"

$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Server = Join-Path $Root "tools\llama.cpp-b9360\llama-server.exe"
$Model = Join-Path $Root "models\hy-mt2-1.8b\Hy-MT2-1.8B-Q4_K_M.gguf"
$LogDir = Join-Path $Root "logs"
$OutLog = Join-Path $LogDir "llama_cpp_server_hy-mt2-1.8b-q4_k_m.log"
$ErrLog = Join-Path $LogDir "llama_cpp_server_hy-mt2-1.8b-q4_k_m.err.log"

New-Item -ItemType Directory -Force $LogDir | Out-Null

$Args = @(
  "--model", $Model,
  "--alias", "hy-mt2-1.8b-q4_k_m",
  "--host", "127.0.0.1",
  "--port", "8080",
  "--ctx-size", "4096",
  "--threads", "4",
  "--threads-batch", "4",
  "--parallel", "1",
  "--api-key", "sk-1234",
  "--temp", "0.7",
  "--top-p", "0.6",
  "--top-k", "20",
  "--repeat-penalty", "1.05",
  "--predict", "4096",
  "--n-gpu-layers", "0",
  "--fit", "off",
  "--no-repack",
  "--no-ui",
  "--log-verbosity", "2"
)

Start-Process -WindowStyle Hidden -FilePath $Server -ArgumentList $Args -WorkingDirectory $Root -RedirectStandardOutput $OutLog -RedirectStandardError $ErrLog
