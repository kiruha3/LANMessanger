# Run the full test suite first and bump app/__init__.py before each build.
# Keep unrelated application/runtime DLL directories out of PyInstaller's search path.
$ErrorActionPreference = 'Stop'
$taskRepo = Split-Path $PSScriptRoot -Parent
$taskPython = (& py -3.11 -c 'import sys; print(sys.executable)').Trim()
if ($LASTEXITCODE -ne 0) { throw 'Python 3.11 is required' }
$taskOriginalPath = $env:PATH
Push-Location $taskRepo
try {
    $env:PATH = @((Split-Path $taskPython -Parent), "$env:SystemRoot\System32", $env:SystemRoot) -join ';'
    & $taskPython -m PyInstaller --clean --noconfirm --onefile --windowed --name LANMessenger --add-data 'app/core/ai_auth.mjs;app/core' --add-data 'licenses/bb-MIT.txt;licenses' --add-data 'THIRD_PARTY_NOTICES.md;.' main.py
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed: $LASTEXITCODE" }
    New-Item -ItemType Directory -Path (Join-Path $taskRepo 'dist/licenses') -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $taskRepo 'licenses/bb-MIT.txt') -Destination (Join-Path $taskRepo 'dist/licenses/bb-MIT.txt')
    Copy-Item -LiteralPath (Join-Path $taskRepo 'THIRD_PARTY_NOTICES.md') -Destination (Join-Path $taskRepo 'dist/THIRD_PARTY_NOTICES.md')
} finally {
    $env:PATH = $taskOriginalPath
    Pop-Location
}
