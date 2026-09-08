# Start seq.ps1 detached so prlctl exec returns at once (it would otherwise block for the
# whole run and hit the exec timeout). The host waits for \\Mac\parlab\out\ALL-DONE.txt.
Copy-Item '\\Mac\parlab\bench-seq.ps1' 'C:\bench\bench-seq.ps1' -Force
Remove-Item '\\Mac\parlab\out\ALL-DONE.txt', 'C:\bench\out\progress.txt' -ErrorAction SilentlyContinue
Start-Process powershell -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File C:\bench\bench-seq.ps1' -WindowStyle Hidden
Write-Output 'bench-seq started detached'
