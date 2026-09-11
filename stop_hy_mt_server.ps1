$ErrorActionPreference = "SilentlyContinue"

Get-Process llama-server | Stop-Process -Force
