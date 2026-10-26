# Flow Execution Debugger - Copyright (C) 2026 samcall22 (https://github.com/samcall22) - GPL-3.0-or-later
# Packages the source code (no .exe) into a ZIP ready to share. Python is NOT needed to run this script.
# Usage (from this folder):  powershell -ExecutionPolicy Bypass -File .\build.ps1
$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

try {
    $version = (Select-String -Path "flowdebugger\__init__.py" -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
    Write-Host "Packaging Flow Execution Debugger $version (source)" -ForegroundColor Cyan

    $packageName = "FlowExecutionDebugger_v$version"
    $packageDir = Join-Path "dist" $packageName
    if (Test-Path $packageDir) { Remove-Item $packageDir -Recurse -Force }
    New-Item -ItemType Directory -Path (Join-Path $packageDir "flowdebugger") -Force | Out-Null

    Copy-Item "flowdebugger\*.py" (Join-Path $packageDir "flowdebugger")
    Copy-Item "main.py", "requirements.txt", "iniciar.bat", "INSTRUCCIONES.md", "LICENSE" $packageDir

    $zip = Join-Path "dist" "$packageName.zip"
    if (Test-Path $zip) { Remove-Item $zip -Force }
    Compress-Archive -Path $packageDir -DestinationPath $zip

    Write-Host ""
    Write-Host "Done. Share this file with your colleagues:" -ForegroundColor Green
    Write-Host "  $(Resolve-Path $zip)"
    Invoke-Item "dist"
}
catch {
    Write-Host ""
    Write-Host "ERROR: $($_.Exception.Message)" -ForegroundColor Red
}
finally {
    # "Run with PowerShell" closes the window as soon as the script ends.
    Write-Host ""
    Read-Host "Press Enter to close" | Out-Null
}
