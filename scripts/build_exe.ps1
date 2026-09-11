param(
  [string]$Target = "gui"
)

$ErrorActionPreference = "Stop"

# 打包必须使用项目虚拟环境：系统 Python 常缺少 paddleocr/paddlex 的分发包元数据，
# PyInstaller 的 copy_metadata 会直接抛 PackageNotFoundError。
$projectRoot = Split-Path -Parent $PSScriptRoot
$venvPython = Join-Path $projectRoot ".venv\Scripts\python.exe"
$python = if (Test-Path $venvPython) { $venvPython } else { "python" }

& $python scripts\verify_ppocrv6_models.py
if ($LASTEXITCODE -ne 0) {
  throw "PP-OCRv6 模型校验失败（退出码 $LASTEXITCODE），已中止打包。"
}

Stop-Process -Name deepcat -Force -ErrorAction SilentlyContinue

# dist 目录里同时放着用户的 settings.json 与 data（数据库、剪贴板图片等），
# 而 PyInstaller 会整体重建 dist。这里先暂存，打包完成后再原样还原。
$userSettings = "dist\deepcat\settings.json"
$userDataDir = "dist\deepcat\data"
$userDataStash = Join-Path $env:TEMP "deepcat_dist_userdata"
if (Test-Path $userDataStash) {
  Remove-Item -Recurse -Force $userDataStash -ErrorAction SilentlyContinue
}
if (Test-Path $userSettings) {
  New-Item -ItemType Directory -Force $userDataStash | Out-Null
  Copy-Item $userSettings -Destination $userDataStash -Force
}
if (Test-Path $userDataDir) {
  New-Item -ItemType Directory -Force $userDataStash | Out-Null
  Copy-Item $userDataDir -Destination $userDataStash -Recurse -Force
}

Remove-Item -Recurse -Force dist, build -ErrorAction SilentlyContinue

& $python -m PyInstaller packaging\deepcat_gui.spec --noconfirm --clean
$pyInstallerExitCode = $LASTEXITCODE

if (Test-Path $userDataStash) {
  # 打包失败时 dist\deepcat 可能不存在，此时保留暂存副本而不是抛 Copy-Item 错误。
  if (Test-Path "dist\deepcat") {
    $stashedSettings = Join-Path $userDataStash "settings.json"
    if (Test-Path $stashedSettings) {
      Copy-Item $stashedSettings -Destination "dist\deepcat\settings.json" -Force
    }
    $stashedData = Join-Path $userDataStash "data"
    if (Test-Path $stashedData) {
      Copy-Item $stashedData -Destination "dist\deepcat\data" -Recurse -Force
    }
  }
  else {
    Write-Warning "dist\deepcat 不存在，用户数据仍保留在 $userDataStash"
  }
}

if ($pyInstallerExitCode -ne 0) {
  throw "PyInstaller 打包失败（退出码 $pyInstallerExitCode）。"
}

$distDir = "dist\deepcat"
if (Test-Path $distDir) {
  New-Item -ItemType Directory -Force (Join-Path $distDir "models") | Out-Null
  if (Test-Path "model_catalog.json") {
    Copy-Item "model_catalog.json" -Destination $distDir -Force
  }
  foreach ($doc in @(
      "README.md",
      "LICENSE",
      "COPYRIGHT",
      "PRIVACY.md",
      "SECURITY.md",
      "THIRD_PARTY_NOTICES.md",
      "THIRD_PARTY_LICENSES.md",
      "USER_GUIDE.md",
      "LOCAL_API_SERVICE.md"
    )) {
    if (Test-Path $doc) {
      Copy-Item $doc -Destination $distDir -Force
    }
  }
}

Write-Output "输出目录：$(Resolve-Path .\dist\deepcat)"
