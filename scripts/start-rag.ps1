# 文件说明：默认用 Windows 项目虚拟环境启动 RAG，也可指定任意 WSL 发行版。
# 仅启动 RAG API；Grobid、向量库与模型服务按 docs/setup.md 单独准备。
param([string]$Distro = "", [int]$Port = 0, [string]$PythonPath = "")
$ErrorActionPreference = "Stop"
$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\rag_service")).Path
if (-not $Distro) { $Distro = $env:PAPERLENS_WSL_DISTRO }
if ($Distro) {
    if ($PythonPath) { throw "For WSL, select Python with PAPERLENS_RAG_PYTHON inside WSL." }
    $arguments = @("-d", $Distro, "--cd", $projectRoot, "--exec", "bash", "scripts/dev_stack.sh", "start")
    if ($Port -ne 0) { $arguments += @("--port", [string]$Port) }
    & wsl.exe @arguments
    exit $LASTEXITCODE
}
$selectedPython = $PythonPath
if (-not $selectedPython) { $selectedPython = $env:PAPERLENS_RAG_PYTHON }
if (-not $selectedPython) { $selectedPython = Join-Path $projectRoot ".venv\Scripts\python.exe" }
if (-not (Test-Path -LiteralPath $selectedPython)) {
    throw "Create rag_service/.venv first. See docs/setup.md. Use -PythonPath for a custom interpreter."
}
$arguments = @((Join-Path $PSScriptRoot "manage.py"), "rag")
if ($Port -ne 0) { $arguments += @("--port", [string]$Port) }
& $selectedPython -B @arguments
exit $LASTEXITCODE
