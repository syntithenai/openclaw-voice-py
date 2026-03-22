#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "ERROR: Neither 'docker compose' nor 'docker-compose' is available."
  exit 1
fi

uid="$(id -u)"
default_runtime_dir="/run/user/${uid}"
runtime_dir="${XDG_RUNTIME_DIR:-$default_runtime_dir}"
pulse_socket="${runtime_dir}/pulse/native"

selected_service="orchestrator-linux-alsa"
audio_profile="${OPENCLAW_DOCKER_AUDIO_PROFILE:-alsa}"
audio_profile="$(printf '%s' "$audio_profile" | tr '[:upper:]' '[:lower:]')"

case "$audio_profile" in
  pulse)
    if [[ -S "$pulse_socket" ]]; then
      export XDG_RUNTIME_DIR="$runtime_dir"
      selected_service="orchestrator-linux-pulse"
    else
      echo "WARN: OPENCLAW_DOCKER_AUDIO_PROFILE=pulse but no PulseAudio socket at ${pulse_socket}; falling back to ALSA."
      selected_service="orchestrator-linux-alsa"
    fi
    ;;
  auto)
    if [[ -S "$pulse_socket" ]]; then
      export XDG_RUNTIME_DIR="$runtime_dir"
      selected_service="orchestrator-linux-pulse"
    fi
    ;;
  *)
    selected_service="orchestrator-linux-alsa"
    ;;
esac

fifo_host_path="${MUSIC_FIFO_HOST_PATH:-}"
if [[ -z "$fifo_host_path" && -f "$SCRIPT_DIR/.env.docker" ]]; then
  fifo_host_path="$(grep -E '^MUSIC_FIFO_HOST_PATH=' "$SCRIPT_DIR/.env.docker" | tail -n1 | cut -d= -f2- | tr -d '\"' | tr -d "'" || true)"
fi
fifo_host_path="${fifo_host_path:-/tmp/openclaw-music-fifo}"
fifo_pcm_path="${fifo_host_path%/}/music.pcm"
export MUSIC_FIFO_HOST_PATH="$fifo_host_path"

snapcast_enabled="${SNAPCAST_ENABLED:-}"
if [[ -z "$snapcast_enabled" && -f "$SCRIPT_DIR/.env.docker" ]]; then
  snapcast_enabled="$(grep -E '^SNAPCAST_ENABLED=' "$SCRIPT_DIR/.env.docker" | tail -n1 | cut -d= -f2- | tr -d '\"' | tr -d "'" || true)"
fi
snapcast_enabled="${snapcast_enabled:-false}"

mkdir -p "$fifo_host_path"
chmod 0777 "$fifo_host_path" || true
if [[ ! -p "$fifo_pcm_path" ]]; then
  rm -f "$fifo_pcm_path"
  mkfifo -m 0666 "$fifo_pcm_path" || true
fi
chmod 0666 "$fifo_pcm_path" || true

echo "Selected orchestrator service: ${selected_service}"
if [[ "$selected_service" == "orchestrator-linux-pulse" ]]; then
  echo "PulseAudio/PipeWire socket detected at: ${pulse_socket}"
  echo "Audio profile: pulse"
else
  echo "PulseAudio socket not found; falling back to ALSA (/dev/snd)."
  echo "Audio profile: alsa"
fi
echo "Shared music FIFO host path: ${fifo_host_path}"

"${COMPOSE[@]}" stop orchestrator orchestrator-linux-alsa orchestrator-linux-pulse >/dev/null 2>&1 || true
"${COMPOSE[@]}" rm -f orchestrator orchestrator-linux-alsa orchestrator-linux-pulse >/dev/null 2>&1 || true

if [[ "$snapcast_enabled" == "true" || "$snapcast_enabled" == "1" || "$snapcast_enabled" == "yes" ]]; then
  "${COMPOSE[@]}" --profile snapcast up -d whisper piper snapserver "$selected_service"
  started_services="whisper, piper, snapserver, ${selected_service}"
else
  "${COMPOSE[@]}" up -d whisper piper "$selected_service"
  started_services="whisper, piper, ${selected_service}"
fi

echo
echo "Started services: ${started_services}"
echo "Logs: ${COMPOSE[*]} logs -f ${selected_service}"
