# Provision C:\bench inside the Parallels lab guest (run via pmlab_runps as SYSTEM).
# Expects on \\Mac\parlab: wireview.zip, dist\channels_nats-*.whl, nats-server-windows-arm64.zip.
# Makes two venvs: .venv (native ARM64: uvicorn stack; daphne has no ARM64 wheels for
# autobahn/cryptography) and .venv-x64 (x64 under emulation: daphne + uvicorn).
$root = 'C:\bench'
$share = '\\Mac\parlab'
New-Item -ItemType Directory -Force -Path $root, "$root\out" | Out-Null
$env:UV_INSTALL_DIR = "$root\uv"
$env:UV_PYTHON_INSTALL_DIR = "$root\python"
$env:UV_CACHE_DIR = "$root\cache"
$env:UV_NO_MODIFY_PATH = '1'
$env:PYTHONUTF8 = '1'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$uv = "$root\uv\uv.exe"
if (-not (Test-Path $uv)) {
  Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
  if (-not (Test-Path $uv)) { Write-Output 'uv install failed'; exit 10 }
}
& $uv python install cpython-3.12-windows-aarch64-none cpython-3.12-windows-x86_64-none
if ($LASTEXITCODE -ne 0) { Write-Output 'python install failed'; exit 11 }
if (Test-Path "$root\wireview") { Remove-Item -Recurse -Force "$root\wireview" }
Expand-Archive -Path "$share\wireview.zip" -DestinationPath "$root\wireview" -Force
Expand-Archive -Path "$share\nats-server-windows-arm64.zip" -DestinationPath "$root\nats" -Force
Set-Location "$root\wireview"
$wheel = (Get-ChildItem "$share\dist\channels_nats-*.whl" | Select-Object -First 1).FullName
$common = @('-e', '.', 'uvicorn', 'websockets', 'psutil', 'whitenoise', $wheel)
& $uv venv --python cpython-3.12-windows-aarch64-none .venv
& $uv pip install --python .venv\Scripts\python.exe @common
if ($LASTEXITCODE -ne 0) { Write-Output 'arm64 venv failed'; exit 12 }
& $uv venv --python cpython-3.12-windows-x86_64-none .venv-x64
& $uv pip install --python .venv-x64\Scripts\python.exe @common daphne
if ($LASTEXITCODE -ne 0) { Write-Output 'x64 venv failed'; exit 13 }
foreach ($venv in '.venv', '.venv-x64') {
  & "$root\wireview\$venv\Scripts\python.exe" -c "import json, os, platform, sys, psutil, django, uvicorn; d={'python': sys.version, 'platform': platform.platform(), 'cpus': os.cpu_count(), 'ram_gb': round(psutil.virtual_memory().total/2**30, 1), 'django': django.get_version(), 'uvicorn': uvicorn.__version__}; print(json.dumps(d))" | Out-File -Encoding utf8 "$root\out\machine-$venv.json"
  Get-Content "$root\out\machine-$venv.json"
}
& "$root\nats\nats-server-v2.14.6-windows-arm64\nats-server.exe" --version
