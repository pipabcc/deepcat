# Nuitka 一键打包 DeepCat 脚本
$ErrorActionPreference = "Stop"

python scripts\verify_ppocrv6_models.py
New-Item -ItemType Directory -Force "build\paddlex-cache" | Out-Null
$env:PADDLE_PDX_CACHE_HOME = (Resolve-Path "build\paddlex-cache").Path
$env:HF_HUB_OFFLINE = "1"
$env:TRANSFORMERS_OFFLINE = "1"

$ppOcrV6ModelRoot = (Resolve-Path "models\pp-ocrv6-small").Path
$ppOcrV6Manifest = Get-Content -Raw (Join-Path $ppOcrV6ModelRoot "model_manifest.json") | ConvertFrom-Json
$ppOcrV6DataArgs = @()
foreach ($modelFile in $ppOcrV6Manifest.files) {
    $relativePath = [string]$modelFile.path
    $sourcePath = Join-Path $ppOcrV6ModelRoot ($relativePath -replace '/', '\')
    $destinationPath = "models/pp-ocrv6-small/$relativePath"
    $ppOcrV6DataArgs += "--include-data-files=$sourcePath=$destinationPath"
}
foreach ($docName in @("model_manifest.json", "README.md", "LICENSE")) {
    $sourcePath = Join-Path $ppOcrV6ModelRoot $docName
    if (Test-Path -LiteralPath $sourcePath -PathType Leaf) {
        $ppOcrV6DataArgs += "--include-data-files=$sourcePath=models/pp-ocrv6-small/$docName"
    }
}

# 1. 杀死可能正在运行的旧进程，防止打包写入失败
Write-Host "[1/5] 终止已在运行的 DeepCat 进程..." -ForegroundColor Cyan
Stop-Process -Name main -Force -ErrorAction SilentlyContinue
Stop-Process -Name deepcat -Force -ErrorAction SilentlyContinue

# 2. 清理旧的编译产物目录
Write-Host "[2/5] 清理旧构建目录..." -ForegroundColor Cyan
Remove-Item -Recurse -Force dist_nuitka -ErrorAction SilentlyContinue

# 3. Nuitka 环境已在准备阶段成功配置，直接进入编译环节

# 4. 执行 Nuitka 编译打包命令
Write-Host "[4/5] 启动 Nuitka 编译 (此步骤可能耗时较长)..." -ForegroundColor Cyan
$nuitkaArgs = @(
    "--standalone"
    "--plugin-enable=pyqt6"
    "--include-module=deepcat.ocr_ipc"
    "--include-module=deepcat.ocr_worker"
    "--include-module=deepcat.ocr_worker_client"
    "--include-module=deepcat.ocr_engines"
    "--include-module=deepcat.ocr_text_layout"
    "--include-module=deepcat.ppocrv6_runtime"
    "--include-package-data=rapidocr_onnxruntime"
    "--include-package=paddleocr"
    "--include-package=paddlex"
    "--include-package-data=paddleocr"
    "--include-package-data=paddlex"
    "--include-package-data=onnxruntime"
    "--include-distribution-metadata=paddlex"
    "--include-distribution-metadata=imagesize"
    "--include-distribution-metadata=opencv-contrib-python"
    "--include-distribution-metadata=pyclipper"
    "--include-distribution-metadata=pypdfium2"
    "--include-distribution-metadata=python-bidi"
    "--include-distribution-metadata=shapely"
    "--windows-console-mode=disable"
    "--windows-icon-from-ico=deepcat\ui\assets\app.ico"
    "--output-dir=dist_nuitka"
    "--show-progress"
    "--show-memory"
)
$nuitkaArgs += $ppOcrV6DataArgs
$nuitkaArgs += "main.py"
python -m nuitka @nuitkaArgs

# 5. 输出编译成功及指引
Write-Host "[5/5] 编译完成！" -ForegroundColor Green
if (Test-Path .\dist_nuitka\main.dist\main.exe) {
    $outPath = Resolve-Path .\dist_nuitka\main.dist
    Write-Host "成功生成独立打包目录: $outPath" -ForegroundColor Green
    Write-Host "双击运行 $outPath\main.exe 测试主程序。" -ForegroundColor Green
} else {
    Write-Host "未能在预期路径找到编译后的可执行文件，请检查编译输出日志。" -ForegroundColor Red
}
