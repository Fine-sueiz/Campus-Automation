$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

python -m pip install -r requirements.txt
python -m pip install pyinstaller

python -m PyInstaller `
  --noconfirm `
  --clean `
  --onefile `
  --windowed `
  --name WorkStudyMonitor `
  run_gui.py

Write-Host ""
Write-Host "EXE 已生成：$PSScriptRoot\dist\WorkStudyMonitor.exe"
