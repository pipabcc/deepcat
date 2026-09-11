@echo off
taskkill /F /IM llama-server.exe >nul 2>nul
taskkill /F /T /FI "WINDOWTITLE eq HunyuanMT Translation Server" >nul 2>nul
