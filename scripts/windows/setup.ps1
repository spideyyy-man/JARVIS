# JARVIS setup for Windows. Run from the project folder in PowerShell:
#     powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
# Optional:  -Model qwen2.5:7b   (better answers, needs ~16 GB RAM and is slow without a GPU)
# Optional:  -Python C:\path\to\python.exe   use a specific Python (the one-click installer passes this)
param([string]$Model = "qwen2.5:3b", [switch]$SkipModel, [string]$Python = "")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")

Write-Host "== 1/4  Python environment" -ForegroundColor Cyan
if (-not (Test-Path backend\.venv\Scripts\python.exe)) {
  if ($Python) { & $Python -m venv backend\.venv }
  elseif (Get-Command py -ErrorAction SilentlyContinue) { py -3 -m venv backend\.venv }
  else { python -m venv backend\.venv }
}
& backend\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet
Write-Host "   installing dependencies (a few minutes, no output until it finishes)..."
& backend\.venv\Scripts\python.exe -m pip install -e "backend[desktop]" --quiet
if ($LASTEXITCODE -ne 0) { throw "pip install failed. Python 3.11 or 3.12 (64-bit) is recommended: https://www.python.org/downloads/" }

Write-Host "== 2/4  Configuration (.env)" -ForegroundColor Cyan
if (-not (Test-Path .env)) {
  Copy-Item .env.example .env
  if (-not $SkipModel) {
    (Get-Content .env) -replace '^JARVIS_LLM_PROVIDER=.*', 'JARVIS_LLM_PROVIDER=openai_compat' `
                       -replace '^JARVIS_OPENAI_BASE_URL=.*', 'JARVIS_OPENAI_BASE_URL=http://127.0.0.1:11434/v1' `
                       -replace '^JARVIS_OPENAI_MODEL=.*', "JARVIS_OPENAI_MODEL=$Model" | Set-Content .env
  }
  Write-Host "   wrote .env"
} else { Write-Host "   .env already exists - left untouched" }

Write-Host "== 3/4  Local language model (Ollama)" -ForegroundColor Cyan
if ($SkipModel) { Write-Host "   skipped" }
elseif (Get-Command ollama -ErrorAction SilentlyContinue) {
  $ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
  Write-Host "   $ramGB GB RAM detected. Downloading $Model (this can take a while)..."
  ollama pull $Model
  # Ollama's default context window is too small for an agent's tool definitions; build a variant with 8192 tokens.
  $mf = Join-Path $env:TEMP "jarvis.Modelfile"
  "FROM $Model`nPARAMETER num_ctx 8192" | Set-Content -Encoding ASCII $mf
  ollama create jarvis-model -f $mf
  if ($LASTEXITCODE -eq 0) {
    (Get-Content .env) -replace '^JARVIS_OPENAI_MODEL=.*', 'JARVIS_OPENAI_MODEL=jarvis-model' | Set-Content .env
    Write-Host "   created 'jarvis-model' ($Model with an 8192-token context) and set it in .env"
  } else { Write-Host "   could not create the larger-context variant; using $Model as is" -ForegroundColor Yellow }
} else {
  Write-Host "   Ollama is not installed. Install it from https://ollama.com/download , then run:  ollama pull $Model" -ForegroundColor Yellow
  Write-Host "   Until then JARVIS runs on its built-in rule engine."
}

Write-Host "== 4/4  Done" -ForegroundColor Cyan
Write-Host "Start JARVIS:            scripts\windows\start.bat"
Write-Host "Start with Windows:      backend\.venv\Scripts\python.exe -m jarvis.desktop_app --install-autostart"
