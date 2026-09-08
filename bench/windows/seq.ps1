# The benchmark sequence, run detached by all.ps1. Progress goes to progress.txt, every
# result and server log to \\Mac\parlab\out, and ALL-DONE.txt marks the end.
$root = 'C:\bench'
$out = '\\Mac\parlab\out'
$env:NATS_SERVER = "$root\nats\nats-server-v2.14.6-windows-arm64\nats-server.exe"
$env:PYTHONUTF8 = '1'
Set-Location "$root\wireview"
New-Item -ItemType Directory -Force -Path $out | Out-Null
function Run($venv, $name, $benchArgs) {
  $py = "$root\wireview\$venv\Scripts\python.exe"
  $started = Get-Date
  cmd /c "$py -m bench.run $benchArgs --out $root\out\$name.json > $root\out\$name.log 2>&1"
  $rc = $LASTEXITCODE
  $secs = [int]((Get-Date) - $started).TotalSeconds
  "$name rc=$rc secs=$secs" | Out-File -Append -Encoding utf8 "$root\out\progress.txt"
  Copy-Item "$root\out\*" $out -Force
  if (Test-Path "$root\wireview\bench\.data\logs") { Copy-Item "$root\wireview\bench\.data\logs\*" $out -Force }
}
Run '.venv'     'arm64-uvicorn-memory-1proc'  '--server uvicorn --connections 2000'
Run '.venv'     'arm64-uvicorn-nats-4proc'    '--server uvicorn --layer nats --processes 4 --connections 2000'
Run '.venv-x64' 'x64-daphne-memory-1proc-400' '--connections 400'
Run '.venv-x64' 'x64-daphne-memory-1proc-600' '--connections 600'
Run '.venv-x64' 'x64-uvicorn-memory-1proc'    '--server uvicorn --connections 2000'
Run '.venv-x64' 'x64-daphne-nats-4proc'       '--layer nats --processes 4 --connections 2000'
'done' | Out-File -Encoding utf8 "$out\ALL-DONE.txt"
