#!/usr/bin/env bash
# Generate a self-signed TLS certificate for local HTTPS use.
# Default SANs cover the machine hostname, localhost, 127.0.0.1, and all
# currently assigned LAN IPs. Extra DNS names and IPs can be added explicitly.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
CERT_DIR="$REPO_ROOT/certs"
CERT_HOSTNAME="$(hostname)"
DAYS=36500
HTTP_REDIRECT_PORT="18909"
EXTRA_IPS=()
EXTRA_DNS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --cert-dir) CERT_DIR="$2"; shift 2 ;;
    --hostname|--cn) CERT_HOSTNAME="$2"; shift 2 ;;
    --days) DAYS="$2"; shift 2 ;;
    --extra-ip) EXTRA_IPS+=("$2"); shift 2 ;;
    --extra-dns) EXTRA_DNS+=("$2"); shift 2 ;;
    --http-redirect-port) HTTP_REDIRECT_PORT="$2"; shift 2 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

collect_host_ips() {
  hostname -I 2>/dev/null | tr ' ' '\n' | sed '/^$/d'
}

append_unique() {
  local value="$1"
  shift
  local existing
  for existing in "$@"; do
    [[ "$existing" == "$value" ]] && return 0
  done
  return 1
}

DNS_SANS=("$CERT_HOSTNAME" "localhost")
IP_SANS=("127.0.0.1")

while IFS= read -r ip; do
  [[ -n "$ip" ]] || continue
  if ! append_unique "$ip" "${IP_SANS[@]}"; then
    IP_SANS+=("$ip")
  fi
done < <(collect_host_ips)

for value in "${EXTRA_IPS[@]}"; do
  [[ -n "$value" ]] || continue
  if ! append_unique "$value" "${IP_SANS[@]}"; then
    IP_SANS+=("$value")
  fi
done

for value in "${EXTRA_DNS[@]}"; do
  [[ -n "$value" ]] || continue
  if ! append_unique "$value" "${DNS_SANS[@]}"; then
    DNS_SANS+=("$value")
  fi
done

SAN_ENTRIES=()
for dns_name in "${DNS_SANS[@]}"; do
  SAN_ENTRIES+=("DNS:${dns_name}")
done
for ip_addr in "${IP_SANS[@]}"; do
  SAN_ENTRIES+=("IP:${ip_addr}")
done
SAN_STRING="$(IFS=,; echo "${SAN_ENTRIES[*]}")"

mkdir -p "$CERT_DIR"

CERT_FILE="$CERT_DIR/${CERT_HOSTNAME}-cert.pem"
KEY_FILE="$CERT_DIR/${CERT_HOSTNAME}-key.pem"

echo "Generating certificate for CN=$CERT_HOSTNAME"
echo "SANs: $SAN_STRING"
echo "Valid for $DAYS days (~$(( DAYS / 365 )) years)"

openssl req -x509 -newkey rsa:4096 -sha256 -days "$DAYS" -nodes \
  -keyout "$KEY_FILE" \
  -out    "$CERT_FILE" \
  -subj   "/CN=${CERT_HOSTNAME}/O=OpenClaw Voice/C=US" \
  -addext "subjectAltName=${SAN_STRING}"

echo ""
echo "Certificate written to:"
echo "  cert: $CERT_FILE"
echo "  key:  $KEY_FILE"
echo ""
openssl x509 -in "$CERT_FILE" -noout -subject -dates -ext subjectAltName
echo ""
echo "Suggested .env settings:"
echo "  WEB_UI_SSL_CERTFILE=$CERT_FILE"
echo "  WEB_UI_SSL_KEYFILE=$KEY_FILE"
echo "  WEB_UI_HTTP_REDIRECT_PORT=$HTTP_REDIRECT_PORT"
