# JARVIS one-click installer. Normally started by double-clicking Install-JARVIS.bat.
#   -Model qwen2.5:7b   use a larger model (needs ~16 GB RAM; slow without a GPU)
#   -NoModel            skip Ollama and the model (JARVIS then runs on its built-in rule engine)
#   -NoLaunch           install only, do not start JARVIS
param([string]$Model = "qwen2.5:3b", [switch]$NoModel, [switch]$NoLaunch)

$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
$root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
Set-Location $root

function Step([string]$n, [string]$text) { Write-Host ""; Write-Host "== $n  $text" -ForegroundColor Cyan }
function Note([string]$text) { Write-Host "   $text" }
function Warn([string]$text) { Write-Host "   $text" -ForegroundColor Yellow }
function Refresh-Path {
  $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
}
function Has-Command([string]$name) { return [bool](Get-Command $name -ErrorAction SilentlyContinue) }

function Find-Python {
  # Prefer 3.12 / 3.11: every dependency ships ready-made packages for them.
  if (Has-Command "py") {
    foreach ($v in @("3.12", "3.11")) {
      try {
        $p = & py "-$v" -c "import sys; print(sys.executable)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $p) { return ([string]$p).Trim() }
      } catch { }
    }
  }
  $candidates = @(
    "$env:LocalAppData\Programs\Python\Python312\python.exe", "$env:LocalAppData\Programs\Python\Python311\python.exe",
    "$env:ProgramFiles\Python312\python.exe", "$env:ProgramFiles\Python311\python.exe")
  foreach ($c in $candidates) { if (Test-Path $c) { return $c } }
  if (Has-Command "python") {
    try {
      $info = & python -c "import sys; print('%d.%d' % sys.version_info[:2]); print(sys.executable)" 2>$null
      if ($LASTEXITCODE -eq 0 -and $info -and (@("3.11", "3.12", "3.13") -contains ([string]$info[0]).Trim())) { return ([string]$info[1]).Trim() }
    } catch { }
  }
  return $null
}

function Install-WithWinget([string]$id, [string[]]$extra) {
  if (-not (Has-Command "winget")) { return $false }
  $wingetArgs = @("install", "-e", "--id", $id, "--silent", "--accept-package-agreements", "--accept-source-agreements") + $extra
  & winget @wingetArgs | Out-Host
  return $true   # winget returns non-zero for "already installed"; the caller re-checks instead of trusting the code
}

function Download([string]$url, [string]$dest) {
  Note "downloading $url"
  $ProgressPreference = "SilentlyContinue"   # the progress bar makes Invoke-WebRequest many times slower
  Invoke-WebRequest -Uri $url -OutFile $dest -UseBasicParsing
}

Write-Host ""
Write-Host "  JARVIS installer" -ForegroundColor Cyan
Write-Host "  Folder: $root"
$ramGB = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB)
$freeGB = [math]::Round((Get-PSDrive -Name $root.Substring(0, 1)).Free / 1GB)
Write-Host "  Memory: $ramGB GB    Free disk space: $freeGB GB"
if ($ramGB -lt 8) { Warn "Less than 8 GB of memory: the language model will be very slow. Consider running with -NoModel." }
if ($freeGB -lt 8) { Warn "Less than 8 GB free disk space: the model download (about 2 GB) plus dependencies may not fit." }

# ---------------------------------------------------------------- 1. Python
Step "1/6" "Python"
$python = Find-Python
if (-not $python) {
  Note "Python 3.11/3.12 not found - installing Python 3.12 for the current user (no administrator rights needed)"
  $done = Install-WithWinget "Python.Python.3.12" @("--scope", "user")
  Refresh-Path
  $python = Find-Python
  if (-not $python) {
    $exe = Join-Path $env:TEMP "python-3.12-installer.exe"
    Download "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe" $exe
    Start-Process -FilePath $exe -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1" -Wait
    Refresh-Path
    $python = Find-Python
  }
  if (-not $python) { throw "Python could not be installed automatically. Install Python 3.12 (64-bit) from https://www.python.org/downloads/ and run this installer again." }
}
Note "using $python"

# ---------------------------------------------------------------- 2. Ollama
Step "2/6" "Ollama (runs the language model on this computer)"
$ollamaDir = Join-Path $env:LocalAppData "Programs\Ollama"
if ($NoModel) { Note "skipped (-NoModel)" }
else {
  if (-not (Has-Command "ollama") -and (Test-Path (Join-Path $ollamaDir "ollama.exe"))) { $env:Path += ";$ollamaDir" }
  if (-not (Has-Command "ollama")) {
    Note "Ollama not found - installing"
    $done = Install-WithWinget "Ollama.Ollama" @()
    Refresh-Path
    if (-not (Has-Command "ollama") -and (Test-Path (Join-Path $ollamaDir "ollama.exe"))) { $env:Path += ";$ollamaDir" }
    if (-not (Has-Command "ollama")) {
      $exe = Join-Path $env:TEMP "OllamaSetup.exe"
      Download "https://ollama.com/download/OllamaSetup.exe" $exe
      Start-Process -FilePath $exe -ArgumentList "/VERYSILENT", "/NORESTART" -Wait
      Refresh-Path
      if (Test-Path (Join-Path $ollamaDir "ollama.exe")) { $env:Path += ";$ollamaDir" }
    }
  }
  if (Has-Command "ollama") {
    $up = $false
    for ($i = 0; $i -lt 30 -and -not $up; $i++) {
      try { Invoke-WebRequest -Uri "http://localhost:11434/api/version" -UseBasicParsing -TimeoutSec 2 | Out-Null; $up = $true }
      catch {
        if ($i -eq 0) { Note "starting the Ollama service"; Start-Process -FilePath "ollama" -ArgumentList "serve" -WindowStyle Hidden }
        Start-Sleep -Seconds 1
      }
    }
    if ($up) { Note "Ollama is running" } else { Warn "Ollama did not answer on http://localhost:11434 - the model download may fail. Start Ollama from the Start menu and run this installer again." }
  } else {
    Warn "Ollama could not be installed automatically. Install it from https://ollama.com/download and run this installer again."
    Warn "Continuing without a model: JARVIS will use its built-in rule engine."
    $NoModel = $true
  }
}

# ---------------------------------------------------------------- 3 + 4. JARVIS itself and the model
Step "3/6" "JARVIS (Python environment, dependencies, settings, language model)"
Note "this is the long step: several minutes, more on a slow connection"
$setup = Join-Path $PSScriptRoot "setup.ps1"
if ($NoModel) { & $setup -Python $python -SkipModel } else { & $setup -Python $python -Model $Model }
if (-not (Test-Path "backend\.venv\Scripts\python.exe")) { throw "The Python environment was not created. See the messages above." }

# ---------------------------------------------------------------- 5. Diagnostics
Step "4/6" "Checking what works on this computer"
$report = Join-Path $root "doctor-report.txt"
$ErrorActionPreference = "Continue"   # the report may write warnings to the error stream; that must not abort the installer
& "backend\.venv\Scripts\python.exe" -m jarvis.doctor 2>&1 | ForEach-Object { "$_" } | Tee-Object -FilePath $report | Out-Host
$ErrorActionPreference = "Stop"
Note "saved to $report  (send this file if you need help)"

# ---------------------------------------------------------------- 6. Shortcut
Step "5/6" "Desktop shortcut"
try {
  $shell = New-Object -ComObject WScript.Shell
  $shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "JARVIS.lnk"))
  $shortcut.TargetPath = Join-Path $root "scripts\windows\start.bat"
  $shortcut.WorkingDirectory = $root
  $shortcut.WindowStyle = 7          # start minimised: the JARVIS window is the Edge app window, not this console
  $shortcut.Description = "JARVIS personal assistant"
  $shortcut.Save()
  Note "created 'JARVIS' on your desktop"
} catch { Warn "could not create the shortcut: $($_.Exception.Message). Start JARVIS with scripts\windows\start.bat instead." }

# ---------------------------------------------------------------- 7. Launch
Step "6/6" "Starting JARVIS"
if ($NoLaunch) { Note "skipped (-NoLaunch). Start it with the desktop shortcut." }
else {
  Start-Process -FilePath (Join-Path $root "scripts\windows\start.bat") -WorkingDirectory $root -WindowStyle Minimized
  Note "JARVIS is starting. A window opens in a few seconds; there is also an icon in the system tray."
}

Write-Host ""
Write-Host "  Done." -ForegroundColor Green
Write-Host "  - Start JARVIS any time with the 'JARVIS' shortcut on your desktop."
Write-Host "  - Ctrl+Alt+J opens the Voice screen."
Write-Host "  - Screen reading, mouse/keyboard and browser control are OFF until you enable them in Settings > Capabilities."
Write-Host "  - Guide: docs\WINDOWS.md"
exit 0
