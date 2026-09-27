# This creates a project-only Python environment, then downloads BGE-M3.
# All messages are ASCII so Windows PowerShell 5.1 can read the file without a BOM.
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
try {
    $taskPython = $null
    foreach ($candidate in @('py', 'python')) {
        if (Get-Command $candidate -ErrorAction SilentlyContinue) {
            try {
                $probe = & $candidate -c 'import sys; print(sys.executable) if (3,10) <= sys.version_info[:2] <= (3,12) else None' 2>$null
            } catch {
                continue
            }
            if ($LASTEXITCODE -eq 0 -and $probe -and (Test-Path -LiteralPath "$probe")) {
                $taskPython = "$probe"
                break
            }
        }
    }
    if (-not $taskPython) {
        $bundled = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
        if (Test-Path -LiteralPath $bundled) { $taskPython = $bundled }
    }
    if (-not $taskPython) {
        throw 'Python 3.10-3.12 was not found. Install Python 3.12, then run this file again.'
    }
    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        Write-Host 'Creating a local Python environment...'
        & $taskPython -m venv (Join-Path $PSScriptRoot '.venv')
        if ($LASTEXITCODE -ne 0) { throw 'Could not create the Python environment.' }
    }
    # Keep package cache inside this project too.
    $env:PIP_CACHE_DIR = Join-Path $PSScriptRoot '.cache\pip'
    $env:PYTHONUTF8 = '1'
    Write-Host 'Installing packages (internet required)...'
    & $venvPython -m pip install -r (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { throw 'Package installation failed. Check your internet connection and try again.' }
    Write-Host 'Downloading the model and preparing 101 controls. This can take several minutes...'
    & $venvPython -X utf8 (Join-Path $PSScriptRoot 'retriever.py') --prepare
    if ($LASTEXITCODE -ne 0) { throw 'Model preparation failed. See the message above and run setup again.' }
    Write-Host 'Ready. Double-click 02_search.cmd to search.'
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
