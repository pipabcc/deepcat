@echo off
setlocal

set "ROOT=%~dp0"
set "SERVER=%ROOT%tools\llama.cpp-b9360\llama-server.exe"
set "MODEL=%ROOT%models\hy-mt2-1.8b\Hy-MT2-1.8B-Q4_K_M.gguf"
set "LOGDIR=%ROOT%logs"
set "LOG=%LOGDIR%\llama_cpp_server.log"

if not exist "%LOGDIR%" mkdir "%LOGDIR%"
title HunyuanMT Translation Server

"%SERVER%" ^
  --model "%MODEL%" ^
  --alias hy-mt2-1.8b-q4_k_m ^
  --host 127.0.0.1 ^
  --port 8080 ^
  --ctx-size 4096 ^
  --threads 4 ^
  --threads-batch 4 ^
  --parallel 1 ^
  --api-key sk-1234 ^
  --temp 0.7 ^
  --top-p 0.6 ^
  --top-k 20 ^
  --repeat-penalty 1.05 ^
  --predict 4096 ^
  --n-gpu-layers 0 ^
  --fit off ^
  --no-repack ^
  --no-ui ^
  --log-verbosity 2 ^
  >> "%LOG%" 2>&1
