param([string]$Destination = (Join-Path (Split-Path $PSScriptRoot -Parent) 'dist\ai-runtime'))
$ErrorActionPreference = 'Stop'
$taskNode = Get-Command node -ErrorAction Stop
$taskNpm = Get-Command npm.cmd -ErrorAction Stop
$taskMajor = [int]((& $taskNode.Source --version).TrimStart('v').Split('.')[0])
if ($taskMajor -lt 22) { throw 'Install Node.js 22 LTS or newer before installing Pi.' }
New-Item -ItemType Directory -Path $Destination -Force | Out-Null
& $taskNpm.Source install --prefix $Destination --no-audit --no-fund --save-exact '@mariozechner/pi-coding-agent@0.73.1'
if ($LASTEXITCODE -ne 0) { throw "Pi installation failed: $LASTEXITCODE" }
Write-Output "Pi installed in $Destination. Keep ai-runtime beside LANMessenger.exe."
