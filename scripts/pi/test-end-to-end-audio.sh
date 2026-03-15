#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v pactl >/dev/null 2>&1; then
  echo "pactl not found (need pipewire-pulse/pulseaudio tools)." >&2
  exit 1
fi

current_default="$(pactl info | sed -n 's/^Default Sink: //p')"
if [[ -z "$current_default" ]]; then
  echo "Could not determine current default sink." >&2
  exit 1
fi

target_sink="$(pactl list short sinks | awk 'BEGIN{IGNORECASE=1} /snapcast|pi[ _-]?one/ {print $2; exit}')"
if [[ -z "$target_sink" ]]; then
  echo "Could not find Snapcast sink (expected name containing snapcast or pi-two)." >&2
  pactl list short sinks
  exit 2
fi

restore_default() {
  pactl set-default-sink "$current_default" >/dev/null 2>&1 || true
}
trap restore_default EXIT

echo "Current default sink: $current_default"
echo "Target Snapcast sink: $target_sink"

echo "Switching default sink to Snapcast sink..."
pactl set-default-sink "$target_sink"

# Give clients a moment to pick up sink change.
sleep 1

echo "Playing test audio..."
observed_playing=false
if command -v paplay >/dev/null 2>&1 && [[ -f /usr/share/sounds/freedesktop/stereo/complete.oga ]]; then
  for _ in 1 2 3 4 5; do
    paplay -d "$target_sink" /usr/share/sounds/freedesktop/stereo/complete.oga >/dev/null 2>&1 || true
    if curl -s -X POST http://10.1.1.210:1780/jsonrpc \
      -H 'Content-Type: application/json' \
      -d '{"id":1,"jsonrpc":"2.0","method":"Server.GetStatus"}' \
      | python3 -c 'import sys,json; d=json.load(sys.stdin)["result"]["server"]; s=next((x for x in d["streams"] if x.get("id")=="Pi Two"), None); print("yes" if s and s.get("status")=="playing" else "no")' \
      | grep -q yes; then
      observed_playing=true
      break
    fi
    sleep 0.25
  done
elif command -v canberra-gtk-play >/dev/null 2>&1; then
  canberra-gtk-play -i bell -d "snapcast-e2e-test" || true
elif command -v speaker-test >/dev/null 2>&1; then
  speaker-test -D "$target_sink" -t sine -f 880 -l 1 || true
else
  echo "No local audio test player found (canberra-gtk-play/paplay/speaker-test)." >&2
fi

sleep 2

echo "\nRemote Snapcast status (Pi):"
"$ROOT_DIR/snapcast-status.sh" || true

echo "observed_pi_two_playing=$observed_playing"

echo "\nDone. Default sink restored to: $current_default"
