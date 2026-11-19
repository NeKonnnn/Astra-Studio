#!/bin/sh
# AstraStudio one-liner installer (Linux / WSL).
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/NeKonnnn/Astra-Studio/main/scripts/install.sh | sh
# Options via env (flags after | sh are eaten by sh):
#   ASTRA_HOME          install dir (default: $HOME/astrastudio)
#   ASTRA_VERSION       image tag (default: latest)
#   ASTRA_PROFILE       full | minimal (default: full)
#   ASTRA_DEVICE        auto | gpu | cpu (default: auto)
#   ASTRA_REPO_URL      git clone URL
#   ASTRA_REPO_REF      branch/tag (default: main)
#   ASTRA_HF_TOKEN      Hugging Face token (optional)
#   ASTRA_SKIP_PULL=1   skip docker compose pull
#   ASTRA_FORCE_BUILD=1 always docker compose build
#   ASTRA_INSTALL_DOCKER=0  do not auto-install Docker/git (default: 1)
# SPDX-License-Identifier: MIT
set -e

# Body in a function so piped `sh` reads the whole script before running.
_astra_main() {

RULE=""
_i=0
while [ "$_i" -lt 52 ]; do
  RULE="${RULE}-"
  _i=$((_i + 1))
done

if [ -n "${NO_COLOR:-}" ]; then
  C_OK= C_WARN= C_ERR= C_DIM= C_TITLE= C_RST=
elif [ -t 1 ] || [ -n "${FORCE_COLOR:-}" ]; then
  _ESC="$(printf '\033')"
  C_TITLE="${_ESC}[1;36m"
  C_DIM="${_ESC}[2m"
  C_OK="${_ESC}[32m"
  C_WARN="${_ESC}[33m"
  C_ERR="${_ESC}[31m"
  C_RST="${_ESC}[0m"
else
  C_OK= C_WARN= C_ERR= C_DIM= C_TITLE= C_RST=
fi

step() { printf "%s==>%s %s\n" "$C_TITLE" "$C_RST" "$*"; }
ok() { printf "%s[ok]%s %s\n" "$C_OK" "$C_RST" "$*"; }
warn() { printf "%s[warn]%s %s\n" "$C_WARN" "$C_RST" "$*"; }
die() { printf "%s[error]%s %s\n" "$C_ERR" "$C_RST" "$*" >&2; exit 1; }

ASTRA_HOME="${ASTRA_HOME:-$HOME/astrastudio}"
# absolute path for docker bind (self-update)
ASTRA_HOME="$(CDPATH= cd -- "$ASTRA_HOME" 2>/dev/null && pwd || printf "%s" "$ASTRA_HOME")"
ASTRA_VERSION="${ASTRA_VERSION:-latest}"
ASTRA_PROFILE="${ASTRA_PROFILE:-full}"
ASTRA_DEVICE="${ASTRA_DEVICE:-auto}"
ASTRA_REPO_URL="${ASTRA_REPO_URL:-https://github.com/NeKonnnn/Astra-Studio.git}"
ASTRA_REPO_REF="${ASTRA_REPO_REF:-main}"
ASTRA_SKIP_PULL="${ASTRA_SKIP_PULL:-0}"
ASTRA_FORCE_BUILD="${ASTRA_FORCE_BUILD:-0}"
ASTRA_INSTALL_DOCKER="${ASTRA_INSTALL_DOCKER:-1}"

printf "%s\n" "$RULE"
printf "%s AstraStudio installer%s\n" "$C_TITLE" "$C_RST"
printf "%s\n" "$RULE"
printf "  home=%s  version=%s  profile=%s  device=%s\n" \
  "$ASTRA_HOME" "$ASTRA_VERSION" "$ASTRA_PROFILE" "$ASTRA_DEVICE"

# --- prerequisites (auto-install Docker/git when possible) ---
step "Checking prerequisites"
command -v curl >/dev/null 2>&1 || die "curl is required"

_have_sudo() {
  if [ "$(id -u)" -eq 0 ]; then return 0; fi
  command -v sudo >/dev/null 2>&1 || return 1
  sudo -n true >/dev/null 2>&1 || sudo -v
}

_install_git() {
  step "Installing git"
  _have_sudo || die "git is missing and sudo is required to install it"
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -y
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y git
  elif command -v yum >/dev/null 2>&1; then
    sudo yum install -y git
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -Sy --noconfirm git
  else
    die "git is required; install it manually for your distro"
  fi
  command -v git >/dev/null 2>&1 || die "git install failed"
  ok "git installed"
}

_install_docker() {
  step "Installing Docker Engine"
  _have_sudo || die "Docker is missing and sudo is required to install it"
  curl -fsSL https://get.docker.com | sudo sh
  if command -v systemctl >/dev/null 2>&1; then
    sudo systemctl enable --now docker >/dev/null 2>&1 || sudo service docker start || true
  fi
  if [ "$(id -u)" -ne 0 ]; then
    sudo usermod -aG docker "$USER" 2>/dev/null || true
    warn "Added $USER to group 'docker' (new shells pick this up automatically)"
  fi
  command -v docker >/dev/null 2>&1 || die "Docker install finished but 'docker' not on PATH — open a new terminal and re-run"
  ok "Docker Engine installed"
}

if ! command -v git >/dev/null 2>&1; then
  if [ "$ASTRA_INSTALL_DOCKER" = "1" ]; then
    _install_git
  else
    die "git is required"
  fi
fi

if ! command -v docker >/dev/null 2>&1; then
  if [ "$ASTRA_INSTALL_DOCKER" = "1" ]; then
    _install_docker
  else
    die "Docker is required. Install Docker Engine/Desktop, then re-run."
  fi
fi

DOCKER_BIN="docker"
if ! docker info >/dev/null 2>&1; then
  if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
    warn "Docker needs root for this session — using sudo docker (re-login to drop sudo)"
    DOCKER_BIN="sudo docker"
  else
    # daemon may still be starting after fresh install
    _i=0
    while [ "$_i" -lt 30 ]; do
      if docker info >/dev/null 2>&1; then break; fi
      if command -v sudo >/dev/null 2>&1 && sudo docker info >/dev/null 2>&1; then
        DOCKER_BIN="sudo docker"
        break
      fi
      _i=$((_i + 1))
      sleep 2
    done
    if ! $DOCKER_BIN info >/dev/null 2>&1; then
      die "Docker daemon is not running. Start it and re-run."
    fi
  fi
fi

if $DOCKER_BIN compose version >/dev/null 2>&1; then
  COMPOSE="$DOCKER_BIN compose"
elif [ "$DOCKER_BIN" = "docker" ] && command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
elif [ "$DOCKER_BIN" = "sudo docker" ] && sudo docker-compose version >/dev/null 2>&1; then
  COMPOSE="sudo docker-compose"
else
  die "Docker Compose plugin is required (docker compose)"
fi

ok "Docker OK ($COMPOSE)"

# --- device detection ---
HAS_GPU=0
if [ "$ASTRA_DEVICE" = "gpu" ]; then
  HAS_GPU=1
elif [ "$ASTRA_DEVICE" = "cpu" ]; then
  HAS_GPU=0
elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  HAS_GPU=1
fi

if [ "$HAS_GPU" = "1" ]; then
  ok "GPU mode (NVIDIA detected or forced)"
  export ASTRA_LLM_VARIANT=cuda
  COMPOSE_LOCAL="-f docker-compose.yml -f docker-compose.gpu.yml"
else
  warn "CPU mode (no NVIDIA GPU or ASTRA_DEVICE=cpu)"
  export ASTRA_LLM_VARIANT=cpu
  COMPOSE_LOCAL="-f docker-compose.yml -f docker-compose.cpu.yml"
fi
# release.yml — только для pull готовых GHCR-образов; локальный build без него
COMPOSE_RELEASE="$COMPOSE_LOCAL -f docker-compose.release.yml"

PROFILE_ARGS=""
case "$ASTRA_PROFILE" in
  full)
    PROFILE_ARGS="--profile speech --profile vision"
    SPEECH_ENABLED=true
    ;;
  minimal)
    PROFILE_ARGS=""
    SPEECH_ENABLED=false
    ;;
  *)
    die "ASTRA_PROFILE must be full or minimal (got: $ASTRA_PROFILE)"
    ;;
esac

# --- clone / update repo ---
step "Preparing install directory: $ASTRA_HOME"
if [ -d "$ASTRA_HOME/.git" ]; then
  ok "Existing git checkout"
  (
    cd "$ASTRA_HOME"
    git fetch --depth 1 origin "$ASTRA_REPO_REF" || true
    git checkout "$ASTRA_REPO_REF" 2>/dev/null || true
    git pull --ff-only origin "$ASTRA_REPO_REF" 2>/dev/null || true
  )
elif [ -f "$ASTRA_HOME/docker-compose.yml" ]; then
  ok "Existing install directory (no .git) — reusing"
else
  mkdir -p "$(dirname "$ASTRA_HOME")"
  git clone --depth 1 --branch "$ASTRA_REPO_REF" "$ASTRA_REPO_URL" "$ASTRA_HOME" \
    || git clone --depth 1 "$ASTRA_REPO_URL" "$ASTRA_HOME"
fi

cd "$ASTRA_HOME"
[ -f docker-compose.yml ] || die "docker-compose.yml not found in $ASTRA_HOME"

# --- directories ---
step "Creating model/data directories"
mkdir -p \
  models/llm models/rag models/comfyui models/omnivoice models/omnivoice_cache \
  diarize_models silero_models whisperx_models surya_models \
  uploads memory ssl
ok "Directories ready"

# --- .env ---
step "Writing .env"
rand_hex() {
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 24
  else
    # portable fallback
    dd if=/dev/urandom bs=24 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n'
  fi
}

if [ ! -f .env ]; then
  if [ -f env.example ]; then
    cp env.example .env
  else
    touch .env
  fi
  JWT="$(rand_hex)"
  JWT_R="$(rand_hex)"
  MONGO_PW="$(rand_hex | cut -c1-16)"
  PG_PW="$(rand_hex | cut -c1-16)"
  MINIO_PW="$(rand_hex | cut -c1-16)"
  RAG_KEY="$(rand_hex | cut -c1-24)"

  # Portable-ish in-place env updates
  _set_env() {
    key="$1"
    val="$2"
    if grep -q "^${key}=" .env 2>/dev/null; then
      # avoid sed -i portability issues
      tmp="$(mktemp)"
      awk -v k="$key" -v v="$val" 'BEGIN{FS=OFS="="} $1==k{$0=k"="v} {print}' .env >"$tmp" && mv "$tmp" .env
    else
      printf "%s=%s\n" "$key" "$val" >>.env
    fi
  }

  _set_env ASTRA_VERSION "$ASTRA_VERSION"
  _set_env ASTRA_UPDATES_ENABLED true
  _set_env ASTRA_HOST_ROOT_ON_HOST "$ASTRA_HOME"
  _set_env ASTRA_HOST_ROOT /astra-host
  _set_env ASTRA_PROFILE "$ASTRA_PROFILE"
  _set_env ASTRA_GITHUB_REPO NeKonnnn/Astra-Studio
  _set_env JWT_SECRET "$JWT"
  _set_env JWT_REFRESH_SECRET "$JWT_R"
  _set_env MONGODB_PASSWORD "$MONGO_PW"
  _set_env POSTGRES_PASSWORD "$PG_PW"
  _set_env MINIO_ROOT_PASSWORD "$MINIO_PW"
  _set_env SVC_RAG_API_KEY "$RAG_KEY"
  _set_env STT_SERVICE_ENABLED "$SPEECH_ENABLED"
  _set_env TTS_SERVICE_ENABLED "$SPEECH_ENABLED"
  _set_env DIARIZATION_SERVICE_ENABLED "$SPEECH_ENABLED"
  _set_env ASTRA_LLM_VARIANT "$ASTRA_LLM_VARIANT"
  if [ "$HAS_GPU" = "0" ]; then
    _set_env COMFYUI_FORCE_CPU 1
    _set_env OMNIVOICE_DEVICE cpu
    _set_env LLM_INFERENCE_REPLICAS 1
  fi
  if [ -n "${ASTRA_HF_TOKEN:-}" ]; then
    _set_env HF_TOKEN "$ASTRA_HF_TOKEN"
  fi
  ok "Created .env with generated secrets"
else
  ok "Keeping existing .env"
  if [ -n "${ASTRA_HF_TOKEN:-}" ]; then
    tmp="$(mktemp)"
    awk -v v="$ASTRA_HF_TOKEN" 'BEGIN{FS=OFS="="} $1=="HF_TOKEN"{$0="HF_TOKEN="v} {print}' .env >"$tmp" && mv "$tmp" .env
  fi
fi

export ASTRA_VERSION

# --- pull or build ---
step "Fetching container images (ASTRA_VERSION=$ASTRA_VERSION)"
PULL_OK=0
if [ "$ASTRA_FORCE_BUILD" = "1" ]; then
  warn "ASTRA_FORCE_BUILD=1 — building locally (slow)"
else
  if [ "$ASTRA_SKIP_PULL" != "1" ]; then
    # shellcheck disable=SC2086
    if $COMPOSE $COMPOSE_RELEASE $PROFILE_ARGS pull; then
      PULL_OK=1
      ok "Pulled prebuilt images from GHCR"
      COMPOSE_FILES="$COMPOSE_RELEASE"
    else
      warn "GHCR pull failed (images may not be published yet) — falling back to local build"
      COMPOSE_FILES="$COMPOSE_LOCAL"
    fi
  else
    COMPOSE_FILES="$COMPOSE_LOCAL"
  fi
fi

if [ "$PULL_OK" != "1" ]; then
  COMPOSE_FILES="$COMPOSE_LOCAL"
  step "Building images locally (this can take a long time on first run)"
  # shellcheck disable=SC2086
  $COMPOSE $COMPOSE_FILES $PROFILE_ARGS build
  ok "Local build finished"
fi

# --- up ---
step "Starting services (profile=$ASTRA_PROFILE)"
# shellcheck disable=SC2086
$COMPOSE $COMPOSE_FILES $PROFILE_ARGS up -d
ok "Containers started"

step "Waiting for backend"
_i=0
while [ "$_i" -lt 90 ]; do
  if curl -fsS "http://127.0.0.1:8000/health" >/dev/null 2>&1 \
    || curl -fsS "http://127.0.0.1:8000/docs" >/dev/null 2>&1; then
    ok "Backend is responding"
    break
  fi
  _i=$((_i + 1))
  sleep 2
done

printf "%s\n" "$RULE"
printf "%s AstraStudio is up%s\n" "$C_OK" "$C_RST"
printf "  UI:      http://localhost:3000\n"
printf "  API:     http://localhost:8000\n"
printf "  Install: %s\n" "$ASTRA_HOME"
printf "  Login:   admin / admin123  (mock users; change before production)\n"
printf "\n"
printf "%sNext:%s download models in UI (Settings → Загрузка моделей) or place GGUF under models/llm/\n" "$C_DIM" "$C_RST"
printf "%sLogs:%s  cd %s && %s %s %s logs -f astrachat-backend\n" \
  "$C_DIM" "$C_RST" "$ASTRA_HOME" "$COMPOSE" "$COMPOSE_FILES" "$PROFILE_ARGS"
printf "%s\n" "$RULE"

}

_astra_main "$@"
