# Provision C:\bench on a native x64 Windows machine (run by ssh.sh provision).
# Expects in C:\bench: wireview.zip and nats-server.zip (from ssh.sh stage).
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$root = 'C:\bench'
$env:UV_INSTALL_DIR = "$root\uv"
$env:UV_PYTHON_INSTALL_DIR = "$root\python"
$env:UV_CACHE_DIR = "$root\cache"
$env:UV_NO_MODIFY_PATH = '1'
$env:PYTHONUTF8 = '1'
$python = if ($env:BENCH_PYTHON) { $env:BENCH_PYTHON } else { 'cpython-3.14-windows-x86_64-none' }
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$uv = "$root\uv\uv.exe"
if (-not (Test-Path $uv)) {
  Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
  if (-not (Test-Path $uv)) { Write-Output 'uv install failed'; exit 10 }
}
& $uv python install $python
if ($LASTEXITCODE -ne 0) { Write-Output 'python install failed'; exit 11 }
if (Test-Path "$root\wireview") { Remove-Item -Recurse -Force "$root\wireview" }
Expand-Archive -Path "$root\wireview.zip" -DestinationPath "$root\wireview" -Force
if (Test-Path "$root\nats") { Remove-Item -Recurse -Force "$root\nats" }
Expand-Archive -Path "$root\nats-server.zip" -DestinationPath "$root\nats" -Force
Set-Location "$root\wireview"
& $uv venv --python $python .venv
& $uv pip install --python .venv\Scripts\python.exe -e . daphne uvicorn granian websockets psutil whitenoise channels-nats
if ($LASTEXITCODE -ne 0) { Write-Output 'venv failed'; exit 12 }
& .venv\Scripts\python.exe -c "import sys, platform, granian, uvicorn, daphne; print(sys.version, platform.platform(), granian.__version__, uvicorn.__version__, daphne.__version__)"
(Get-ChildItem -Recurse "$root\nats" -Filter nats-server.exe | Select-Object -First 1).FullName
