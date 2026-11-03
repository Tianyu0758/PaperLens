# 文件说明：使用项目虚拟环境启动 Agent；端口、地址和数据目录由配置或参数决定。
# PythonPath 参数或 PAPERLENS_AGENT_PYTHON 可选择其他已安装依赖的环境。
param([int]$Port = 0, [string]$PythonPath = "")
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location -LiteralPath $projectRoot
$selectedPython = $PythonPath
if (-not $selectedPython) { $selectedPython = $env:PAPERLENS_AGENT_PYTHON }
if (-not $selectedPython) { $selectedPython = Join-Path $projectRoot ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $selectedPython)) {
    throw "Create the Agent venv first. See docs/setup.md. Use -PythonPath for a custom interpreter."
}
$arguments = @((Join-Path $PSScriptRoot "manage.py"), "agent")
if ($Port -ne 0) { $arguments += @("--port", [string]$Port) }
& $selectedPython -B @arguments
exit $LASTEXITCODE
