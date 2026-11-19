#!/bin/sh
# AstraStudio updater (host or docker:cli helper with repo + docker.sock mounted).
# Usage (from install dir):
#   sh scripts/update.sh
# Env:
#   ASTRA_VERSION, ASTRA_LLM_VARIANT (cuda|cpu), ASTRA_PROFILE (full|minimal)
#   ASTRA_DEVICE (auto|gpu|cpu) — only used if ASTRA_LLM_VARIANT unset
set -e

ROOT="$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

STATUS_DIR="$ROOT/data"
STATUS_FILE="$STATUS_DIR/update-status.json"
mkdir -p "$STATUS_DIR"

_ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

_write_status() {
  phase="$1"
  msg="$2"
  ok_flag="${3:-true}"
  printf '{\n  "phase": "%s",\n  "message": "%s",\n  "ok": %s,\n  "updated_at": "%s"\n}\n' \
    "$phase" "$msg" "$ok_flag" "$(_ts)" >"$STATUS_FILE"
}

_write_status "starting" "Update started"

ASTRA_VERSION="${ASTRA_VERSION:-latest}"
ASTRA_PROFILE="${ASTRA_PROFILE:-full}"
ASTRA_DEVICE="${ASTRA_DEVICE:-auto}"

if [ -z "${ASTRA_LLM_VARIANT:-}" ]; then
  if [ "$ASTRA_DEVICE" = "cpu" ]; then
    ASTRA_LLM_VARIANT=cpu
  elif [ "$ASTRA_DEVICE" = "gpu" ]; then
    ASTRA_LLM_VARIANT=cuda
  elif command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
    ASTRA_LLM_VARIANT=cuda
  else
    ASTRA_LLM_VARIANT=cpu
  fi
fi
export ASTRA_VERSION ASTRA_LLM_VARIANT

if [ "$ASTRA_LLM_VARIANT" = "cpu" ]; then
  DEVICE_FILE="-f docker-compose.cpu.yml"
else
  DEVICE_FILE="-f docker-compose.gpu.yml"
fi

PROFILE_ARGS=""
case "$ASTRA_PROFILE" in
  minimal) PROFILE_ARGS="" ;;
  *) PROFILE_ARGS="--profile speech --profile vision" ;;
esac

COMPOSE_BASE="-f docker-compose.yml -f docker-compose.release.yml $DEVICE_FILE"

if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  _write_status "failed" "docker compose not found" false
  exit 1
fi

# shellcheck disable=SC2086
_run() { $COMPOSE $COMPOSE_BASE $PROFILE_ARGS "$@"; }

_write_status "git" "Pulling repository"
if [ -d .git ]; then
  git fetch --depth 1 origin "${ASTRA_REPO_REF:-main}" || true
  git pull --ff-only origin "${ASTRA_REPO_REF:-main}" || git pull --ff-only || true
else
  echo "No .git — skipping git pull (image-only update)"
fi

_write_status "pull" "Pulling container images (ASTRA_VERSION=$ASTRA_VERSION)"
# shellcheck disable=SC2086
if ! _run pull; then
  _write_status "failed" "docker compose pull failed" false
  exit 1
fi

_write_status "up" "Recreating containers"
# shellcheck disable=SC2086
if ! _run up -d; then
  _write_status "failed" "docker compose up failed" false
  exit 1
fi

VER="unknown"
[ -f VERSION ] && VER="$(tr -d ' \n\r' < VERSION)"
_write_status "done" "Updated to VERSION=$VER images=$ASTRA_VERSION" true
echo "AstraStudio update finished (VERSION=$VER)"
