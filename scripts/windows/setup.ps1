# JARVIS setup for Windows. Run from the project folder in PowerShell:
#     powershell -ExecutionPolicy Bypass -File scripts\windows\setup.ps1
# Optional:  -Model qwen2.5:3b   (smaller and lighter, ~2.3 GB in memory, but it misunderstands Tamil)
# Optional:  -SkipTranslator     (do not download the ~630 MB Tamil/Indian-language translation model)
# Optional:  -Python C:\path\to\python.exe   use a specific Python (the one-click installer passes this)
# Default model: Qwen3 4B Instruct (~3.7 GB in memory with an 8192-token context). On the owner's laptop it understood
# 10/10 Tamil requests where qwen2.5:3b understood 4/10; JARVIS writes Tamil replies with the translation model.
param([string]$Model = "qwen3:4b-instruct-2507-q4_K_M", [switch]$SkipModel, [switch]$SkipTranslator, [string]$Python = "")
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
  # 8 threads: on the owner's busy laptop the model wrote ~40% faster than with the default 4 (2026-11-20)
  "FROM $Model`nPARAMETER num_ctx 8192`nPARAMETER num_thread 8" | Set-Content -Encoding ASCII $mf
  ollama create jarvis-model -f $mf
  if ($LASTEXITCODE -eq 0) {
    (Get-Content .env) -replace '^JARVIS_OPENAI_MODEL=.*', 'JARVIS_OPENAI_MODEL=jarvis-model' | Set-Content .env
    Write-Host "   created 'jarvis-model' ($Model with an 8192-token context) and set it in .env"
  } else { Write-Host "   could not create the larger-context variant; using $Model as is" -ForegroundColor Yellow }
} else {
  Write-Host "   Ollama is not installed. Install it from https://ollama.com/download , then run:  ollama pull $Model" -ForegroundColor Yellow
  Write-Host "   Until then JARVIS runs on its built-in rule engine."
}

Write-Host "== Tamil / Indian-language translation model" -ForegroundColor Cyan
if ($SkipTranslator -or $SkipModel) { Write-Host "   skipped" }
else {
  Write-Host "   downloading NLLB-200 600M (~630 MB, resumable; safe to re-run)..."
  & backend\.venv\Scripts\python.exe -m jarvis.translate
  if ($LASTEXITCODE -ne 0) { Write-Host "   download incomplete - re-run this script later. Tamil still works, with weaker replies." -ForegroundColor Yellow }
}

Write-Host "== Offline speech (wake word 'Hey Jarvis' + Whisper small, English and Tamil)" -ForegroundColor Cyan
Write-Host "   downloading the speech models (~490 MB, once)..."
& backend\.venv\Scripts\python.exe -c "from jarvis.config import load_settings; from faster_whisper.utils import download_model; import openwakeword; s = load_settings(); openwakeword.utils.download_models(['hey_jarvis']); download_model(s.stt_english_model, cache_dir=str(s.data_dir / 'models' / 'whisper')) if s.stt_english_model else None; download_model(s.stt_fast_model, cache_dir=str(s.data_dir / 'models' / 'whisper')); download_model(s.stt_local_model, cache_dir=str(s.data_dir / 'models' / 'whisper')); print('   ok')"
if ($LASTEXITCODE -ne 0) { Write-Host "   download incomplete - JARVIS fetches the models itself on first start." -ForegroundColor Yellow }

Write-Host "== Offline voices for spoken replies (Piper English ~63 MB, MMS Tamil ~109 MB)" -ForegroundColor Cyan
& backend\.venv\Scripts\python.exe -m jarvis.speak
if ($LASTEXITCODE -ne 0) { Write-Host "   voice download incomplete - JARVIS uses the Windows voice until you re-run this script." -ForegroundColor Yellow }

Write-Host "== 4/4  Done" -ForegroundColor Cyan
Write-Host "Start JARVIS:            scripts\windows\start.bat"
Write-Host "Start with Windows:      backend\.venv\Scripts\python.exe -m jarvis.desktop_app --install-autostart"
