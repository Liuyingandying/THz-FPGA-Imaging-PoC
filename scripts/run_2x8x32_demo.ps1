# run_2x8x32_demo.ps1 — 2×(8×32) THz Digital Twin 一键演示
# 用法: powershell -ExecutionPolicy Bypass -File scripts\run_2x8x32_demo.ps1
#            [tju|metal_defect|moving_target]
# 流程: 检查数据 → 缺失则构建(数据+RTL仿真+位级比对) → 打开 Demo Viewer

param(
    [string]$Scene = "moving_target",
    [int]$Fps = 2
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$python = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "py" }

# 1) 必要数据(已 RTL 位级验证的产物)
$need = @(
    "results\demo_2x8x32\ref\tju_gold.npy",
    "results\demo_2x8x32\ref\metal_defect_gold.npy",
    "results\demo_2x8x32\ref\moving_target_gold.npy"
)
$missing = @($need | Where-Object { -not (Test-Path (Join-Path $Root $_)) })

if ($missing.Count -gt 0) {
    Write-Host "[demo] 缺少数据产物,先构建(含 RTL 行扫描仿真与位级比对,约1-3分钟)..."
    & $python "tools\demo_2x8x32.py" build
    if ($LASTEXITCODE -ne 0) { Write-Error "构建失败"; exit 1 }
    & $python "tools\demo_2x8x32.py" gif
}

Write-Host "====================================================="
Write-Host " 2×(8×32) THz Digital Twin Demo"
 Write-Host " MODEL: 2×(8×32) ASSUMED | MODE: DIGITAL TWIN"
Write-Host " SYNTHETIC THz DATA — 非真实样机实测"
Write-Host " 32 并行通道 × 16 行扫描 → 512 样本/帧"
Write-Host " 场景: $Scene   (关闭窗口退出;可传参切换 tju/metal_defect/moving_target)"
Write-Host "====================================================="

& $python "tools\thz_demo_viewer.py" --scene $Scene --fps $Fps
exit $LASTEXITCODE
