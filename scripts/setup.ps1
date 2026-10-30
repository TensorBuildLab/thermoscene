# One-command local setup for thermoscene (Windows PowerShell).
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1 [-Evaluations] [-ClaudeCode]
param([switch]$Evaluations, [switch]$ClaudeCode)
$ErrorActionPreference = "Stop"
$env:PYTHONUTF8 = "1"          # UTF-8 text I/O regardless of the Windows code page
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "== Python environment"
python -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt

Write-Host "== EnergyPlus"
if (-not $env:THERMOSCENE_ENERGYPLUS) {
  foreach ($c in @("C:\EnergyPlusV22-2-0\energyplus.exe", "C:\EnergyPlusV25-2-0\energyplus.exe",
                   (Get-Command energyplus -ErrorAction SilentlyContinue).Source)) {
    if ($c -and (Test-Path $c)) { $env:THERMOSCENE_ENERGYPLUS = $c; break }
  }
}
if (-not $env:THERMOSCENE_ENERGYPLUS) {
  Write-Host "EnergyPlus not found. Install EnergyPlus (tested: 22.2), set THERMOSCENE_ENERGYPLUS to energyplus.exe and rerun."; exit 1
}
Write-Host "using $env:THERMOSCENE_ENERGYPLUS"

Write-Host "== Case studies"
foreach ($c in "A_single_room", "B_hvac_system", "C_doe_medium_office") {
  python -m thermoscene build "case_studies/$c/case.yaml" --render
}
Write-Host "== Tests"
python -m pytest -q tests

if ($Evaluations) {
  python case_studies/topology_survey.py; python case_studies/zone_sweep.py
  python case_studies/sensitivity.py; python case_studies/paper_numbers.py; python case_studies/paper_figures.py
}
if (-not (Test-Path .git)) { git init -q; git add .; git commit -qm "thermoscene: initial import" }
if ($ClaudeCode) { irm https://claude.ai/install.ps1 | iex }
Write-Host "`nDone. Next:"
Write-Host "  .\.venv\Scripts\Activate.ps1; `$env:THERMOSCENE_ENERGYPLUS='$env:THERMOSCENE_ENERGYPLUS'"
Write-Host "  git remote add origin <your GitHub repo URL>; git push -u origin main     # optional"
Write-Host "  claude                                                                     # start Claude Code here"
