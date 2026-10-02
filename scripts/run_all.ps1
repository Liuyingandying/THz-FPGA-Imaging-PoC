# run_all.ps1 — Windows 一键运行 THz FPGA Imaging PoC
# 用法:  powershell -ExecutionPolicy Bypass -File scripts\run_all.ps1
# 实际流程由 tools\run_all.py 编排(仿真在 WSL 的 Icarus Verilog 中执行)。

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
$python = if (Get-Command python -ErrorAction SilentlyContinue) { "python" } else { "py" }
& $python (Join-Path "tools" "run_all.py") @args
exit $LASTEXITCODE
