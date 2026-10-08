# The benchmark sequence on the SSH Windows machine (run by ssh.sh run, in the foreground).
# Every step is python -m bench.servers; its JSON lands in C:\bench\wireview\bench\results.
$root = 'C:\bench'
$env:PYTHONUTF8 = '1'
$env:BENCH_COMMIT = (Get-Content "$root\commit.txt").Trim()
$env:NATS_SERVER = (Get-ChildItem -Recurse "$root\nats" -Filter nats-server.exe | Select-Object -First 1).FullName
$py = "$root\wireview\.venv\Scripts\python.exe"
Set-Location "$root\wireview"

# Hold off sleep while this process runs (ES_CONTINUOUS | ES_SYSTEM_REQUIRED). A closed laptop lid still sleeps.
Add-Type -Namespace Bench -Name Power -MemberDefinition '[DllImport("kernel32.dll")] public static extern uint SetThreadExecutionState(uint esFlags);'
[Bench.Power]::SetThreadExecutionState([uint32]'0x80000001') | Out-Null

$cpu = (Get-CimInstance Win32_Processor).Name
$os = (Get-CimInstance Win32_OperatingSystem).Caption
$machine = "$env:COMPUTERNAME: $os, $cpu, native x64, over SSH"
$steps = @(
  # daphne dies near 500 connections per process here (select() is capped at 512 sockets), so it gets its own size.
  '--servers daphne,uvicorn-nodeflate,granian --connections 400 --rounds 3 --suffix 400',
  '--servers daphne --connections 600 --rounds 1 --suffix daphne-600',
  '--servers uvicorn,uvicorn-nodeflate,granian --connections 2000 --rounds 3',
  '--servers uvicorn-nodeflate,granian --connections 2000 --rounds 3 --layer nats --processes 4 --suffix nats-4proc'
)
if ($env:BENCH_STEPS) { $steps = $env:BENCH_STEPS -split ';' }
foreach ($step in $steps) {
  $started = Get-Date
  Write-Output "== $step"
  $stepArgs = @('-m', 'bench.servers', '--label', 'win10', '--machine', $machine) + ($step -split ' ')
  & $py @stepArgs 2>&1 | ForEach-Object { "$_" }
  Write-Output ("   rc=$LASTEXITCODE secs={0:N0}" -f ((Get-Date) - $started).TotalSeconds)
}
[Bench.Power]::SetThreadExecutionState([uint32]'0x80000000') | Out-Null
