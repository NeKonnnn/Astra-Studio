#Requires -Version 5.1
<#
.SYNOPSIS
  AstraStudio one-liner installer for Windows (Docker Desktop).

.EXAMPLE
  irm https://raw.githubusercontent.com/NeKonnnn/Astra-Studio/main/scripts/install.ps1 | iex

.NOTES
  Env vars (set before piping):
    $env:ASTRA_HOME, ASTRA_VERSION, ASTRA_PROFILE (full|minimal),
    ASTRA_DEVICE (auto|gpu|cpu), ASTRA_REPO_URL, ASTRA_REPO_REF,
    ASTRA_HF_TOKEN, ASTRA_SKIP_PULL=1, ASTRA_FORCE_BUILD=1
#>

$ErrorActionPreference = "Stop"

function Write-Step([string]$Message) { Write-Host "==> $Message" -ForegroundColor Cyan }
function Write-Ok([string]$Message) { Write-Host "[ok] $Message" -ForegroundColor Green }
function Write-Warn([string]$Message) { Write-Host "[warn] $Message" -ForegroundColor Yellow }
function Write-Die([string]$Message) { Write-Host "[error] $Message" -ForegroundColor Red; exit 1 }

$AstraHome = if ($env:ASTRA_HOME) { $env:ASTRA_HOME } else { Join-Path $HOME "astrastudio" }
$AstraVersion = if ($env:ASTRA_VERSION) { $env:ASTRA_VERSION } else { "latest" }
$AstraProfile = if ($env:ASTRA_PROFILE) { $env:ASTRA_PROFILE } else { "full" }
$AstraDevice = if ($env:ASTRA_DEVICE) { $env:ASTRA_DEVICE } else { "auto" }
$RepoUrl = if ($env:ASTRA_REPO_URL) { $env:ASTRA_REPO_URL } else { "https://github.com/NeKonnnn/Astra-Studio.git" }
$RepoRef = if ($env:ASTRA_REPO_REF) { $env:ASTRA_REPO_REF } else { "main" }
$SkipPull = $env:ASTRA_SKIP_PULL -eq "1"
$ForceBuild = $env:ASTRA_FORCE_BUILD -eq "1"

Write-Host ("-" * 52)
Write-Host " AstraStudio installer (Windows)" -ForegroundColor Cyan
Write-Host ("-" * 52)
Write-Host "  home=$AstraHome  version=$AstraVersion  profile=$AstraProfile  device=$AstraDevice"

Write-Step "Checking prerequisites"
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Write-Die "git is required (https://git-scm.com/download/win)" }
if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
  Write-Die "Docker Desktop is required (https://www.docker.com/products/docker-desktop/)"
}

$composeOk = $false
try {
  docker compose version | Out-Null
  $composeOk = $true
} catch { $composeOk = $false }
if (-not $composeOk) { Write-Die "Docker Compose plugin is required (docker compose)" }

try {
  docker info | Out-Null
} catch {
  Write-Die "Docker Desktop is not running — start it and re-run"
}
Write-Ok "Docker OK"

# GPU detection (nvidia-smi on PATH if driver installed)
$HasGpu = $false
if ($AstraDevice -eq "gpu") {
  $HasGpu = $true
} elseif ($AstraDevice -eq "cpu") {
  $HasGpu = $false
} elseif (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
  try {
    & nvidia-smi | Out-Null
    if ($LASTEXITCODE -eq 0) { $HasGpu = $true }
  } catch { $HasGpu = $false }
}

$ComposeFiles = @("-f", "docker-compose.yml")
if ($HasGpu) {
  Write-Ok "GPU mode (NVIDIA detected or forced)"
  $ComposeFiles += @("-f", "docker-compose.gpu.yml")
} else {
  Write-Warn "CPU mode (no NVIDIA GPU or ASTRA_DEVICE=cpu)"
  $ComposeFiles += @("-f", "docker-compose.cpu.yml")
}

$ProfileArgs = @()
$SpeechEnabled = "true"
switch ($AstraProfile.ToLowerInvariant()) {
  "full" {
    $ProfileArgs = @("--profile", "speech", "--profile", "vision")
    $SpeechEnabled = "true"
  }
  "minimal" {
    $ProfileArgs = @()
    $SpeechEnabled = "false"
  }
  default { Write-Die "ASTRA_PROFILE must be full or minimal (got: $AstraProfile)" }
}

Write-Step "Preparing install directory: $AstraHome"
if (Test-Path (Join-Path $AstraHome ".git")) {
  Write-Ok "Existing git checkout"
  Push-Location $AstraHome
  try {
    git fetch --depth 1 origin $RepoRef 2>$null
    git checkout $RepoRef 2>$null
    git pull --ff-only origin $RepoRef 2>$null
  } catch {
    Write-Warn "git update skipped: $_"
  }
  Pop-Location
} elseif (Test-Path (Join-Path $AstraHome "docker-compose.yml")) {
  Write-Ok "Existing install directory — reusing"
} else {
  $parent = Split-Path -Parent $AstraHome
  if ($parent -and -not (Test-Path $parent)) { New-Item -ItemType Directory -Path $parent -Force | Out-Null }
  try {
    git clone --depth 1 --branch $RepoRef $RepoUrl $AstraHome
  } catch {
    git clone --depth 1 $RepoUrl $AstraHome
  }
}

Set-Location $AstraHome
if (-not (Test-Path "docker-compose.yml")) { Write-Die "docker-compose.yml not found in $AstraHome" }

Write-Step "Creating model/data directories"
@(
  "models\llm", "models\rag", "models\comfyui", "models\omnivoice", "models\omnivoice_cache",
  "diarize_models", "silero_models", "whisperx_models", "surya_models",
  "uploads", "memory", "ssl"
) | ForEach-Object {
  New-Item -ItemType Directory -Force -Path $_ | Out-Null
}
Write-Ok "Directories ready"

function Set-DotEnv([string]$Key, [string]$Value) {
  $path = Join-Path $AstraHome ".env"
  $lines = @()
  if (Test-Path $path) { $lines = Get-Content -Path $path -ErrorAction SilentlyContinue }
  $found = $false
  $out = foreach ($line in $lines) {
    if ($line -match ("^" + [regex]::Escape($Key) + "=")) {
      $found = $true
      "$Key=$Value"
    } else {
      $line
    }
  }
  if (-not $found) { $out = @($out) + "$Key=$Value" }
  Set-Content -Path $path -Value $out -Encoding UTF8
}

function New-Secret([int]$Bytes = 24) {
  $buf = New-Object byte[] $Bytes
  [System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($buf)
  -join ($buf | ForEach-Object { $_.ToString("x2") })
}

Write-Step "Writing .env"
$envPath = Join-Path $AstraHome ".env"
if (-not (Test-Path $envPath)) {
  $example = Join-Path $AstraHome "env.example"
  if (Test-Path $example) {
    Copy-Item $example $envPath
  } else {
    New-Item -ItemType File -Path $envPath -Force | Out-Null
  }
  Set-DotEnv "ASTRA_VERSION" $AstraVersion
  Set-DotEnv "JWT_SECRET" (New-Secret 24)
  Set-DotEnv "JWT_REFRESH_SECRET" (New-Secret 24)
  Set-DotEnv "MONGODB_PASSWORD" ((New-Secret 12).Substring(0, 16))
  Set-DotEnv "POSTGRES_PASSWORD" ((New-Secret 12).Substring(0, 16))
  Set-DotEnv "MINIO_ROOT_PASSWORD" ((New-Secret 12).Substring(0, 16))
  Set-DotEnv "SVC_RAG_API_KEY" (New-Secret 16)
  Set-DotEnv "STT_SERVICE_ENABLED" $SpeechEnabled
  Set-DotEnv "TTS_SERVICE_ENABLED" $SpeechEnabled
  Set-DotEnv "DIARIZATION_SERVICE_ENABLED" $SpeechEnabled
  if (-not $HasGpu) {
    Set-DotEnv "COMFYUI_FORCE_CPU" "1"
    Set-DotEnv "OMNIVOICE_DEVICE" "cpu"
    Set-DotEnv "LLM_INFERENCE_REPLICAS" "1"
  }
  if ($env:ASTRA_HF_TOKEN) { Set-DotEnv "HF_TOKEN" $env:ASTRA_HF_TOKEN }
  Write-Ok "Created .env with generated secrets"
} else {
  Write-Ok "Keeping existing .env"
  if ($env:ASTRA_HF_TOKEN) { Set-DotEnv "HF_TOKEN" $env:ASTRA_HF_TOKEN }
}

$env:ASTRA_VERSION = $AstraVersion

Write-Step "Fetching container images (ASTRA_VERSION=$AstraVersion)"
$pullOk = $false
if ($ForceBuild) {
  Write-Warn "ASTRA_FORCE_BUILD=1 — building locally (slow)"
} elseif (-not $SkipPull) {
  $pullArgs = @("compose") + $ComposeFiles + $ProfileArgs + @("pull")
  & docker @pullArgs
  if ($LASTEXITCODE -eq 0) {
    $pullOk = $true
    Write-Ok "Pulled prebuilt images from GHCR"
  } else {
    Write-Warn "GHCR pull failed (images may not be published yet) — falling back to local build"
  }
}

if (-not $pullOk) {
  Write-Step "Building images locally (this can take a long time on first run)"
  $buildArgs = @("compose") + $ComposeFiles + $ProfileArgs + @("build")
  & docker @buildArgs
  if ($LASTEXITCODE -ne 0) { Write-Die "docker compose build failed" }
  Write-Ok "Local build finished"
}

Write-Step "Starting services (profile=$AstraProfile)"
$upArgs = @("compose") + $ComposeFiles + $ProfileArgs + @("up", "-d")
& docker @upArgs
if ($LASTEXITCODE -ne 0) { Write-Die "docker compose up failed" }
Write-Ok "Containers started"

Write-Step "Waiting for backend"
$ready = $false
for ($i = 0; $i -lt 90; $i++) {
  try {
    $r = Invoke-WebRequest -Uri "http://127.0.0.1:8000/health" -UseBasicParsing -TimeoutSec 3
    if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { $ready = $true; break }
  } catch {
    try {
      $r2 = Invoke-WebRequest -Uri "http://127.0.0.1:8000/docs" -UseBasicParsing -TimeoutSec 3
      if ($r2.StatusCode -ge 200) { $ready = $true; break }
    } catch { }
  }
  Start-Sleep -Seconds 2
}
if ($ready) { Write-Ok "Backend is responding" } else { Write-Warn "Backend not healthy yet — check: docker compose logs -f astrachat-backend" }

Write-Host ("-" * 52)
Write-Host " AstraStudio is up" -ForegroundColor Green
Write-Host "  UI:      http://localhost:3000"
Write-Host "  API:     http://localhost:8000"
Write-Host "  Install: $AstraHome"
Write-Host "  Login:   admin / admin123  (mock users; change before production)"
Write-Host ""
Write-Host "Next: download models in UI (Settings → Загрузка моделей) or place GGUF under models\llm\"
Write-Host "Logs:  cd `"$AstraHome`"; docker compose $($ComposeFiles -join ' ') $($ProfileArgs -join ' ') logs -f astrachat-backend"
Write-Host ("-" * 52)
