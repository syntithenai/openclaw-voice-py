#!/usr/bin/env bash
# =============================================================================
# setup-hotspot-kiosk.sh
# Ubuntu WiFi Hotspot Kiosk – redirects all HTTP/HTTPS from connected clients
# to the local web UI (default: port 18910).
#
# Conditions – hotspot will only be enabled if ONE of:
#   (a) Two or more wireless NICs are available (one for internet, one for AP)
#   (b) An ethernet interface has an active internet connection
#
# An existing WiFi internet connection is never disrupted.
#
# Usage:
#   sudo ./setup-hotspot-kiosk.sh [OPTIONS]
#
# Options:
#   --ssid       NAME        Hotspot SSID              (default: OpenClaw)
#   --password   PASS        WPA2 password (8+ chars)  (default: openclaw1)
#   --channel    NUM         WiFi channel 1-11         (default: 6)
#   --web-port   PORT        Local web UI HTTPS/UI port (default: 18910)
#   --ws-port    PORT        Local web UI websocket port (default: 18911)
#   --web-host   HOST        Local web UI host         (default: 0.0.0.0)
#   --http-redirect-port PORT Local HTTP→HTTPS redirect port (default: 18909)
#   --mux-port   PORT        Local protocol-sniff mux port for arbitrary TCP ports (default: 18908)
#   --no-https               Disable TLS helpers and use plain HTTP only
#   --cert-dir   DIR         Directory for generated hotspot certs
#   --cert-host  NAME        Certificate common name / primary DNS SAN
#   --ap-iface   IFACE       Force this AP interface   (auto-detected)
#   --subnet     A.B.C       Hotspot subnet /24        (default: 192.168.42)
#   --dry-run                Print what would be done without changing anything
#   --uninstall              Remove hotspot configuration and restore defaults
# =============================================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$SCRIPT_DIR"

# ─── Defaults ────────────────────────────────────────────────────────────────
AP_SSID="OpenClaw"
AP_PASSWORD="openclaw1"
AP_CHANNEL="6"
WEB_PORT="18910"
WS_PORT="18911"
WEB_HOST="0.0.0.0"
HTTP_REDIRECT_PORT="18909"
MUX_PORT="18908"
TLS_ENABLED=true
AP_IFACE_FORCE=""
SUBNET="192.168.42"      # will use .0/24, gateway = .1
DRY_RUN=false
UNINSTALL=false
CERT_DIR="$REPO_ROOT/certs/hotspot-kiosk"
CERT_HOST="openclaw-hotspot.local"
HTTPS_CERT_FILE=""
HTTPS_KEY_FILE=""

GATEWAY_IP="${SUBNET}.1"
DHCP_RANGE_START="${SUBNET}.10"
DHCP_RANGE_END="${SUBNET}.100"
DHCP_LEASE="12h"

HOSTAPD_CONF="/etc/hostapd/hostapd-kiosk.conf"
DNSMASQ_CONF="/etc/dnsmasq.d/hotspot-kiosk.conf"
IPTABLES_RULES="/etc/iptables/rules.v4"
NM_UNMANAGED_CONF="/etc/NetworkManager/conf.d/hotspot-kiosk-unmanaged.conf"
SYSTEMD_SERVICE="/etc/systemd/system/hotspot-kiosk.service"
HAPROXY_CFG="/etc/haproxy/haproxy-kiosk.cfg"
HAPROXY_SERVICE="/etc/systemd/system/hotspot-kiosk-haproxy.service"

# ─── Argument parsing ─────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --ssid)       AP_SSID="$2";        shift 2 ;;
        --password)   AP_PASSWORD="$2";    shift 2 ;;
        --channel)    AP_CHANNEL="$2";     shift 2 ;;
        --web-port)   WEB_PORT="$2";       shift 2 ;;
        --ws-port)    WS_PORT="$2";        shift 2 ;;
        --web-host)   WEB_HOST="$2";       shift 2 ;;
        --http-redirect-port) HTTP_REDIRECT_PORT="$2"; shift 2 ;;
        --mux-port)   MUX_PORT="$2";       shift 2 ;;
        --no-https)   TLS_ENABLED=false;    shift ;;
        --cert-dir)   CERT_DIR="$2";       shift 2 ;;
        --cert-host)  CERT_HOST="$2";      shift 2 ;;
        --ap-iface)   AP_IFACE_FORCE="$2"; shift 2 ;;
        --subnet)     SUBNET="$2"; GATEWAY_IP="${SUBNET}.1"
                      DHCP_RANGE_START="${SUBNET}.10"
                      DHCP_RANGE_END="${SUBNET}.100"
                      shift 2 ;;
        --dry-run)    DRY_RUN=true;        shift ;;
        --uninstall)  UNINSTALL=true;      shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

if [[ -z "$HTTPS_CERT_FILE" ]]; then
    HTTPS_CERT_FILE="$CERT_DIR/${CERT_HOST}-cert.pem"
fi
if [[ -z "$HTTPS_KEY_FILE" ]]; then
    HTTPS_KEY_FILE="$CERT_DIR/${CERT_HOST}-key.pem"
fi

# ─── Helpers ──────────────────────────────────────────────────────────────────
info()  { echo -e "\033[1;34m[INFO]\033[0m  $*"; }
ok()    { echo -e "\033[1;32m[ OK ]\033[0m  $*"; }
warn()  { echo -e "\033[1;33m[WARN]\033[0m  $*"; }
error() { echo -e "\033[1;31m[ERR ]\033[0m  $*" >&2; }
die()   { error "$*"; exit 1; }

run() {
    if "$DRY_RUN"; then
        echo -e "\033[0;35m[DRY ]\033[0m  $*"
    else
        "$@"
    fi
}

write_file() {
    local path="$1"
    local content="$2"
    if "$DRY_RUN"; then
        echo -e "\033[0;35m[DRY ]\033[0m  Would write: $path"
        echo "--- content ---"
        echo "$content"
        echo "--- end ---"
    else
        echo "$content" > "$path"
    fi
}

upsert_env_var() {
    local env_file="$1"
    local key="$2"
    local value="$3"
    local escaped_value
    escaped_value=$(printf '%s' "$value" | sed 's/[&/]/\\&/g')

    if "$DRY_RUN"; then
        echo -e "\033[0;35m[DRY ]\033[0m  Would set ${key}=${value} in ${env_file}"
        return 0
    fi

    mkdir -p "$(dirname "$env_file")"
    touch "$env_file"
    if grep -qE "^${key}=" "$env_file"; then
        sed -i "s|^${key}=.*|${key}=${escaped_value}|" "$env_file"
    else
        printf '%s=%s\n' "$key" "$value" >> "$env_file"
    fi
}

validate_port() {
    local label="$1"
    local value="$2"
    [[ "$value" =~ ^[0-9]+$ ]] || die "$label must be numeric; got '$value'"
    (( value >= 1 && value <= 65535 )) || die "$label must be between 1 and 65535; got '$value'"
}

generate_tls_cert() {
    local helper_script="$REPO_ROOT/scripts/gen-ssl-cert.sh"

    if [[ -f "$HTTPS_CERT_FILE" && -f "$HTTPS_KEY_FILE" ]]; then
        ok "Using existing hotspot TLS certificate: $HTTPS_CERT_FILE"
        return 0
    fi

    info "Generating hotspot TLS certificate..."
    run mkdir -p "$CERT_DIR"
    if [[ -x "$helper_script" ]]; then
        run "$helper_script" \
            --cert-dir "$CERT_DIR" \
            --hostname "$CERT_HOST" \
            --extra-ip "$GATEWAY_IP" \
            --extra-dns "$AP_LOCAL_DNS" \
            --http-redirect-port "$HTTP_REDIRECT_PORT"
    else
        run openssl req -x509 -newkey rsa:4096 -sha256 -days 36500 -nodes \
            -keyout "$HTTPS_KEY_FILE" \
            -out "$HTTPS_CERT_FILE" \
            -subj "/CN=${CERT_HOST}/O=OpenClaw Voice/C=US" \
            -addext "subjectAltName=DNS:${CERT_HOST},DNS:localhost,DNS:${AP_LOCAL_DNS},IP:127.0.0.1,IP:${GATEWAY_IP}"
    fi
}

validate_port "WEB_PORT" "$WEB_PORT"
validate_port "WS_PORT" "$WS_PORT"
validate_port "MUX_PORT" "$MUX_PORT"
if "$TLS_ENABLED"; then
    validate_port "HTTP_REDIRECT_PORT" "$HTTP_REDIRECT_PORT"
fi

[[ ${#AP_PASSWORD} -ge 8 ]] || die "Hotspot password must be at least 8 characters"
[[ "$WEB_PORT" != "$WS_PORT" ]] || die "WEB_PORT and WS_PORT must differ"
[[ "$WEB_PORT" != "$MUX_PORT" ]] || die "WEB_PORT and MUX_PORT must differ"
[[ "$WS_PORT" != "$MUX_PORT" ]] || die "WS_PORT and MUX_PORT must differ"
if "$TLS_ENABLED"; then
    [[ "$HTTP_REDIRECT_PORT" != "$WEB_PORT" ]] || die "HTTP_REDIRECT_PORT must differ from WEB_PORT"
    [[ "$HTTP_REDIRECT_PORT" != "$WS_PORT" ]] || die "HTTP_REDIRECT_PORT must differ from WS_PORT"
    [[ "$HTTP_REDIRECT_PORT" != "$MUX_PORT" ]] || die "HTTP_REDIRECT_PORT must differ from MUX_PORT"
fi

if [[ "$WEB_HOST" != "0.0.0.0" ]]; then
    warn "Hotspot mode requires the embedded web UI to bind on 0.0.0.0; overriding WEB_HOST=$WEB_HOST"
    WEB_HOST="0.0.0.0"
fi

AP_LOCAL_DNS="$(printf '%s' "$AP_SSID" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9]/-/g; s/-\{2,\}/-/g; s/^-//; s/-$//')"
AP_LOCAL_DNS="${AP_LOCAL_DNS:-openclaw}.local"

SYSTEMD_WANTS_EXTRA=""
SYSTEMD_STARTPOST_EXTRA=""
SYSTEMD_STOP_EXTRA=""
LOG_SERVICE_EXTRA=""
if "$TLS_ENABLED"; then
    SYSTEMD_WANTS_EXTRA=" hotspot-kiosk-haproxy.service"
    SYSTEMD_STARTPOST_EXTRA="ExecStartPost=/usr/bin/systemctl restart hotspot-kiosk-haproxy"
    SYSTEMD_STOP_EXTRA="ExecStop=/usr/bin/systemctl stop hotspot-kiosk-haproxy"
    LOG_SERVICE_EXTRA=" -u hotspot-kiosk-haproxy"
fi

# ─── Root check ───────────────────────────────────────────────────────────────
[[ $EUID -eq 0 ]] || die "This script must be run as root. Use: sudo $0 $*"

# ─── Uninstall ────────────────────────────────────────────────────────────────
if "$UNINSTALL"; then
    info "Removing hotspot-kiosk configuration..."
    run systemctl stop hotspot-kiosk   2>/dev/null || true
    run systemctl disable hotspot-kiosk 2>/dev/null || true
    run systemctl stop hotspot-kiosk-haproxy 2>/dev/null || true
    run systemctl disable hotspot-kiosk-haproxy 2>/dev/null || true
    run rm -f "$SYSTEMD_SERVICE" "$HAPROXY_SERVICE" "$HAPROXY_CFG" "$HOSTAPD_CONF" "$DNSMASQ_CONF" "$NM_UNMANAGED_CONF"
    run systemctl daemon-reload
    run systemctl restart NetworkManager 2>/dev/null || true
    run systemctl restart dnsmasq       2>/dev/null || true

    # Remove our iptables rules by tag
    info "Flushing hotspot iptables rules..."
    run iptables -t nat    -F PREROUTING  2>/dev/null || true
    run iptables -t nat    -F POSTROUTING 2>/dev/null || true
    run iptables           -F FORWARD     2>/dev/null || true

    ok "Uninstall complete. You may want to reboot to fully restore state."
    exit 0
fi

# ═════════════════════════════════════════════════════════════════════════════
# STEP 1 – Discover network interfaces
# ═════════════════════════════════════════════════════════════════════════════
info "Scanning network interfaces..."

# All wireless interfaces
WIFI_IFACES=()
for d in /sys/class/net/*/wireless; do
    [[ -d "$d" ]] || continue
    iface=$(basename "$(dirname "$d")")
    WIFI_IFACES+=("$iface")
done

# All ethernet interfaces (not lo, not wlan/wl*)
ETH_IFACES=()
for d in /sys/class/net/*; do
    iface=$(basename "$d")
    [[ "$iface" == "lo" ]] && continue
    [[ -d "/sys/class/net/$iface/wireless" ]] && continue
    # Must be a physical/virtual ethernet (has an address file)
    [[ -f "/sys/class/net/$iface/address" ]] || continue
    ETH_IFACES+=("$iface")
done

info "Wireless interfaces found: ${WIFI_IFACES[*]:-none}"
info "Ethernet interfaces found: ${ETH_IFACES[*]:-none}"

# ─── Check internet connectivity on an interface ──────────────────────────────
# Returns 0 if the interface has a default route and can reach the internet
has_internet_via() {
    local iface="$1"
    # Check there's a default route through this interface
    ip route show dev "$iface" | grep -q "^default" || \
    ip route show dev "$iface" | grep -q "^0.0.0.0" || \
    ip route show | grep -q "default.*$iface" || return 1

    # Quick connectivity probe (2s timeout)
    curl -s --interface "$iface" --max-time 2 \
        -o /dev/null "http://connectivitycheck.gstatic.com/generate_204" \
        2>/dev/null && return 0

    # Fallback: ping
    ping -I "$iface" -c 1 -W 2 8.8.8.8 &>/dev/null && return 0

    return 1
}

# Which WiFi interfaces currently carry internet?
WIFI_INTERNET=()
for iface in "${WIFI_IFACES[@]}"; do
    if has_internet_via "$iface" 2>/dev/null; then
        WIFI_INTERNET+=("$iface")
        info "  $iface → WiFi with internet"
    else
        info "  $iface → WiFi (no internet)"
    fi
done

# Which ethernet interfaces carry internet?
ETH_INTERNET=()
for iface in "${ETH_IFACES[@]}"; do
    if has_internet_via "$iface" 2>/dev/null; then
        ETH_INTERNET+=("$iface")
        info "  $iface → Ethernet with internet"
    fi
done

# ═════════════════════════════════════════════════════════════════════════════
# STEP 2 – Determine eligibility and choose AP interface
# ═════════════════════════════════════════════════════════════════════════════
AP_IFACE=""
INTERNET_IFACE=""

if [[ -n "$AP_IFACE_FORCE" ]]; then
    # User explicitly chose an AP interface
    AP_IFACE="$AP_IFACE_FORCE"
    info "Using forced AP interface: $AP_IFACE"
elif [[ ${#WIFI_IFACES[@]} -ge 2 ]]; then
    # Condition (a): Two or more wireless NICs
    # Use a WiFi that is NOT carrying internet for the AP
    for iface in "${WIFI_IFACES[@]}"; do
        skip=false
        for inet_iface in "${WIFI_INTERNET[@]}"; do
            [[ "$iface" == "$inet_iface" ]] && skip=true && break
        done
        if ! "$skip"; then
            AP_IFACE="$iface"
            break
        fi
    done

    if [[ -z "$AP_IFACE" ]]; then
        # All WiFi interfaces have internet – use the last one as AP
        # (rare edge case; warn the user)
        AP_IFACE="${WIFI_IFACES[-1]}"
        warn "All WiFi interfaces appear to have internet. Using $AP_IFACE for AP."
        warn "This may disrupt an existing connection on that interface."
    fi

    # Internet goes through the other WiFi(s) or ethernet
    if [[ ${#WIFI_INTERNET[@]} -gt 0 ]]; then
        for iface in "${WIFI_INTERNET[@]}"; do
            [[ "$iface" != "$AP_IFACE" ]] && INTERNET_IFACE="$iface" && break
        done
    fi
    [[ -z "$INTERNET_IFACE" && ${#ETH_INTERNET[@]} -gt 0 ]] && \
        INTERNET_IFACE="${ETH_INTERNET[0]}"

    ok "Condition (a): Two wireless NICs available."
    ok "  AP interface:       $AP_IFACE"
    ok "  Internet interface: ${INTERNET_IFACE:-unknown}"

elif [[ ${#ETH_INTERNET[@]} -gt 0 && ${#WIFI_IFACES[@]} -ge 1 ]]; then
    # Condition (b): Ethernet has internet, use WiFi for AP
    INTERNET_IFACE="${ETH_INTERNET[0]}"

    # Prefer a WiFi not currently serving internet
    for iface in "${WIFI_IFACES[@]}"; do
        skip=false
        for inet_iface in "${WIFI_INTERNET[@]}"; do
            [[ "$iface" == "$inet_iface" ]] && skip=true && break
        done
        if ! "$skip"; then
            AP_IFACE="$iface"
            break
        fi
    done
    [[ -z "$AP_IFACE" ]] && AP_IFACE="${WIFI_IFACES[0]}"

    ok "Condition (b): Ethernet internet ($INTERNET_IFACE) + WiFi ($AP_IFACE) for AP."

else
    error "Cannot enable hotspot. Neither condition is met:"
    error "  (a) Requires 2+ wireless NICs (found: ${#WIFI_IFACES[@]})"
    error "  (b) Requires ethernet with internet + at least 1 wireless NIC"
    error "      Ethernet with internet: ${ETH_INTERNET[*]:-none}"
    error "      Wireless NICs: ${WIFI_IFACES[*]:-none}"
    die "Aborting. Connect ethernet or add a second wireless adapter."
fi

if [[ -z "$AP_IFACE" ]]; then
    die "Could not determine an AP interface. Use --ap-iface to specify one."
fi

# ═════════════════════════════════════════════════════════════════════════════
# STEP 3 – Install dependencies
# ═════════════════════════════════════════════════════════════════════════════
info "Installing required packages..."
run apt-get update -qq
run apt-get install -y -qq \
    hostapd \
    dnsmasq \
    haproxy \
    iptables \
    iptables-persistent \
    iw \
    curl \
    openssl

if "$TLS_ENABLED"; then
    generate_tls_cert

    info "Writing embedded web UI TLS settings to $REPO_ROOT/.env..."
    upsert_env_var "$REPO_ROOT/.env" "WEB_UI_ENABLED" "true"
    upsert_env_var "$REPO_ROOT/.env" "WEB_UI_HOST" "$WEB_HOST"
    upsert_env_var "$REPO_ROOT/.env" "WEB_UI_PORT" "$WEB_PORT"
    upsert_env_var "$REPO_ROOT/.env" "WEB_UI_WS_PORT" "$WS_PORT"
    upsert_env_var "$REPO_ROOT/.env" "WEB_UI_SSL_CERTFILE" "$HTTPS_CERT_FILE"
    upsert_env_var "$REPO_ROOT/.env" "WEB_UI_SSL_KEYFILE" "$HTTPS_KEY_FILE"
    upsert_env_var "$REPO_ROOT/.env" "WEB_UI_HTTP_REDIRECT_PORT" "$HTTP_REDIRECT_PORT"
else
    info "Using plain HTTP kiosk mode; clearing embedded web UI TLS settings in $REPO_ROOT/.env..."
    upsert_env_var "$REPO_ROOT/.env" "WEB_UI_ENABLED" "true"
    upsert_env_var "$REPO_ROOT/.env" "WEB_UI_HOST" "$WEB_HOST"
    upsert_env_var "$REPO_ROOT/.env" "WEB_UI_PORT" "$WEB_PORT"
    upsert_env_var "$REPO_ROOT/.env" "WEB_UI_WS_PORT" "$WS_PORT"
    upsert_env_var "$REPO_ROOT/.env" "WEB_UI_SSL_CERTFILE" ""
    upsert_env_var "$REPO_ROOT/.env" "WEB_UI_SSL_KEYFILE" ""
    upsert_env_var "$REPO_ROOT/.env" "WEB_UI_HTTP_REDIRECT_PORT" "0"
fi

# ═════════════════════════════════════════════════════════════════════════════
# STEP 4 – Tell NetworkManager to leave the AP interface alone
# ═════════════════════════════════════════════════════════════════════════════
info "Configuring NetworkManager to ignore $AP_IFACE..."
write_file "$NM_UNMANAGED_CONF" "[keyfile]
unmanaged-devices=interface-name:${AP_IFACE}"

run systemctl reload NetworkManager 2>/dev/null || \
    run systemctl restart NetworkManager 2>/dev/null || true

# Give NM a moment to stop managing the interface
sleep 2

# ═════════════════════════════════════════════════════════════════════════════
# STEP 5 – Assign static IP to AP interface
# ═════════════════════════════════════════════════════════════════════════════
info "Assigning static IP $GATEWAY_IP to $AP_IFACE..."

# Bring interface up cleanly
run ip link set "$AP_IFACE" down
run ip addr flush dev "$AP_IFACE"
run ip link set "$AP_IFACE" up
run ip addr add "${GATEWAY_IP}/24" dev "$AP_IFACE"

# Make this survive reboots via a systemd-networkd config or a hook in the
# hotspot-kiosk service (handled in Step 11 service unit).

# ═════════════════════════════════════════════════════════════════════════════
# STEP 6 – Configure hostapd
# ═════════════════════════════════════════════════════════════════════════════
info "Writing hostapd configuration to $HOSTAPD_CONF..."

# Detect if hardware supports 802.11n (optional but faster)
HW_MODE="g"
HOSTAPD_EXTRA=""
if iw phy 2>/dev/null | grep -q "2402 MHz\|2412 MHz"; then
    # 2.4 GHz band confirmed
    HW_MODE="g"
    HOSTAPD_EXTRA="ieee80211n=1
ht_capab=[HT40][SHORT-GI-20][SHORT-GI-40]"
fi

write_file "$HOSTAPD_CONF" "# hostapd configuration – managed by setup-hotspot-kiosk.sh
interface=${AP_IFACE}
driver=nl80211
ssid=${AP_SSID}
hw_mode=${HW_MODE}
channel=${AP_CHANNEL}
macaddr_acl=0
auth_algs=1
ignore_broadcast_ssid=0

# WPA2
wpa=2
wpa_passphrase=${AP_PASSWORD}
wpa_key_mgmt=WPA-PSK
wpa_pairwise=TKIP
rsn_pairwise=CCMP

# Optional 802.11n
${HOSTAPD_EXTRA}

# Logging
logger_syslog=-1
logger_syslog_level=2"

# Point hostapd at our config
if ! "$DRY_RUN"; then
    if [[ -f /etc/default/hostapd ]]; then
        sed -i "s|#\?DAEMON_CONF=.*|DAEMON_CONF=\"${HOSTAPD_CONF}\"|" /etc/default/hostapd
    fi
fi

# ═════════════════════════════════════════════════════════════════════════════
# STEP 7 – Configure dnsmasq
# ═════════════════════════════════════════════════════════════════════════════
info "Writing dnsmasq configuration to $DNSMASQ_CONF..."

# Stop dnsmasq from binding to the internet interface
DNSMASQ_LISTEN_ARGS="interface=${AP_IFACE}
bind-interfaces"

write_file "$DNSMASQ_CONF" "# dnsmasq configuration – managed by setup-hotspot-kiosk.sh
# Only listen on the AP interface
${DNSMASQ_LISTEN_ARGS}

# DHCP pool for hotspot clients
dhcp-range=${DHCP_RANGE_START},${DHCP_RANGE_END},${DHCP_LEASE}

# Gateway / router
dhcp-option=3,${GATEWAY_IP}

# DNS server (point clients at us)
dhcp-option=6,${GATEWAY_IP}

# ── Captive portal DNS redirect ──────────────────────────────────────────
# Return the gateway IP for ALL DNS queries from hotspot clients.
# This causes every domain lookup to resolve to this machine, so any HTTP
# browser navigation lands on our web UI (via iptables redirect below).
address=/#/${GATEWAY_IP}

# Do NOT use /etc/hosts or /etc/resolv.conf for hotspot DNS
no-resolv
no-hosts

# Respond to captive portal detection probes (iOS, Android, Windows)
# so devices know a portal is present instead of showing 'no internet'.
# The actual content is served by the web UI redirect.
address=/captive.apple.com/${GATEWAY_IP}
address=/www.apple.com/${GATEWAY_IP}
address=/connectivitycheck.gstatic.com/${GATEWAY_IP}
address=/connectivitycheck.android.com/${GATEWAY_IP}
address=/clients3.google.com/${GATEWAY_IP}
address=/www.msftconnecttest.com/${GATEWAY_IP}
address=/www.msftncsi.com/${GATEWAY_IP}
address=/detectportal.firefox.com/${GATEWAY_IP}

# Logging
log-queries
log-facility=/var/log/dnsmasq-kiosk.log"

# ═════════════════════════════════════════════════════════════════════════════
# STEP 8 – Enable IP forwarding
# ═════════════════════════════════════════════════════════════════════════════
info "Enabling IP forwarding..."

if ! "$DRY_RUN"; then
    # Persist across reboots
    if ! grep -q "net.ipv4.ip_forward=1" /etc/sysctl.conf; then
        echo "net.ipv4.ip_forward=1" >> /etc/sysctl.conf
    fi
    sysctl -w net.ipv4.ip_forward=1 >/dev/null
else
    echo -e "\033[0;35m[DRY ]\033[0m  Would set net.ipv4.ip_forward=1"
fi

# ═════════════════════════════════════════════════════════════════════════════
# STEP 9 – Protocol mux for arbitrary HTTP/HTTPS ports
# ═════════════════════════════════════════════════════════════════════════════
if "$TLS_ENABLED"; then
    info "Writing HAProxy TCP mux configuration to $HAPROXY_CFG..."
    write_file "$HAPROXY_CFG" "global
    log /dev/log local0
    log /dev/log local1 notice
    pidfile /run/hotspot-kiosk-haproxy.pid
    maxconn 512

defaults
    log global
    mode tcp
    timeout connect 5s
    timeout client 30s
    timeout server 30s

frontend kiosk_mux
    bind 0.0.0.0:${MUX_PORT}
    tcp-request inspect-delay 3s
    use_backend kiosk_https if { req.ssl_hello_type 1 }
    default_backend kiosk_http

backend kiosk_http
    server redirector 127.0.0.1:${HTTP_REDIRECT_PORT}

backend kiosk_https
    server webui_tls 127.0.0.1:${WEB_PORT}"

    info "Writing HAProxy service unit to $HAPROXY_SERVICE..."
    write_file "$HAPROXY_SERVICE" "[Unit]
Description=OpenClaw Hotspot Kiosk TCP mux
After=network.target
Wants=network.target

[Service]
Type=simple
ExecStart=/usr/sbin/haproxy -W -db -f ${HAPROXY_CFG} -p /run/hotspot-kiosk-haproxy.pid
ExecReload=/bin/kill -USR2 \$MAINPID
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target"
fi

# ═════════════════════════════════════════════════════════════════════════════
# STEP 10 – iptables rules
# ═════════════════════════════════════════════════════════════════════════════
# The strategy:
#   • PREROUTING: Redirect TCP :80  → :HTTP_REDIRECT_PORT (plain HTTP redirector)
#   • PREROUTING: Redirect TCP :443 → :WEB_PORT           (native HTTPS UI)
#   • PREROUTING: Redirect any other TCP port → :MUX_PORT so HAProxy can sniff
#     whether the traffic is HTTP or TLS and forward appropriately.
#   • POSTROUTING: Masquerade AP client traffic on the internet interface.
#   • FORWARD: Allow forwarding between AP and internet interfaces.
# ─────────────────────────────────────────────────────────────────────────────
info "Applying iptables rules..."

apply_iptables() {
    local AP="$1"
    local WEB_P="$2"
    local WS_P="$3"
    local HTTP_REDIRECT_P="$4"
    local MUX_P="$5"
    local GW="$6"

    # ── Flush any previous hotspot rules ──────────────────────────────────
    iptables -t nat    -F PREROUTING  2>/dev/null || true
    iptables -t nat    -F POSTROUTING 2>/dev/null || true
    iptables           -F FORWARD     2>/dev/null || true

    if "$TLS_ENABLED"; then
        # ── Redirect HTTP (port 80) → local HTTP redirector ───────────────
        iptables -t nat -A PREROUTING \
            -i "$AP" -p tcp --dport 80 \
            -j REDIRECT --to-port "$HTTP_REDIRECT_P"

        # ── Redirect HTTPS (port 443) → native TLS web UI ─────────────────
        iptables -t nat -A PREROUTING \
            -i "$AP" -p tcp --dport 443 \
            -j REDIRECT --to-port "$WEB_P"

        # ── Redirect any other port → protocol sniff mux ──────────────────
        # The mux forwards TLS to the HTTPS UI and plaintext HTTP to the
        # local redirector, so both http://host:PORT and https://host:PORT
        # land on the embedded web UI instead of a sad browser error page.
        iptables -t nat -A PREROUTING \
            -i "$AP" -p tcp \
            ! --dport "$WEB_P" \
            ! --dport "$WS_P" \
            ! --dport "$HTTP_REDIRECT_P" \
            ! --dport "$MUX_P" \
            ! --dport 22 \
            -j REDIRECT --to-port "$MUX_P"
    else
        # Plain HTTP fallback mode.
        iptables -t nat -A PREROUTING \
            -i "$AP" -p tcp --dport 80 \
            -j REDIRECT --to-port "$WEB_P"
        iptables -t nat -A PREROUTING \
            -i "$AP" -p tcp --dport 443 \
            -j REDIRECT --to-port "$WEB_P"
        iptables -t nat -A PREROUTING \
            -i "$AP" -p tcp \
            ! --dport "$WEB_P" \
            ! --dport "$WS_P" \
            ! --dport 22 \
            -j REDIRECT --to-port "$WEB_P"
    fi

    # ── NAT / Masquerade for internet access from AP clients ──────────────
    # Only add masquerade if an internet interface was detected
    if [[ -n "${INTERNET_IFACE:-}" ]]; then
        iptables -t nat -A POSTROUTING \
            -s "${GW%.*}.0/24" \
            -o "$INTERNET_IFACE" \
            -j MASQUERADE

        # Allow forwarding both ways between AP and internet
        iptables -A FORWARD -i "$AP"               -o "$INTERNET_IFACE" -j ACCEPT
        iptables -A FORWARD -i "$INTERNET_IFACE"   -o "$AP"             -m state \
            --state RELATED,ESTABLISHED -j ACCEPT
    fi

    # ── Allow established/related back to AP clients ───────────────────────
    iptables -A FORWARD -i "$AP" -j ACCEPT
}

if "$DRY_RUN"; then
    info "[DRY] Would apply iptables rules for AP=$AP_IFACE WEB_PORT=$WEB_PORT WS_PORT=$WS_PORT"
else
    apply_iptables "$AP_IFACE" "$WEB_PORT" "$WS_PORT" "$HTTP_REDIRECT_PORT" "$MUX_PORT" "$GATEWAY_IP"

    # Persist iptables rules
    mkdir -p /etc/iptables
    iptables-save > "$IPTABLES_RULES"
    ok "iptables rules saved to $IPTABLES_RULES"
fi

# ═════════════════════════════════════════════════════════════════════════════
# STEP 11 – Create systemd service to orchestrate startup order
# ═════════════════════════════════════════════════════════════════════════════
info "Writing systemd service $SYSTEMD_SERVICE..."

write_file "$SYSTEMD_SERVICE" "[Unit]
Description=OpenClaw Hotspot Kiosk
After=network.target NetworkManager.service
Wants=hostapd.service dnsmasq.service${SYSTEMD_WANTS_EXTRA}

[Service]
Type=oneshot
RemainAfterExit=yes

# Re-apply static IP (in case NM reset it)
ExecStartPre=/sbin/ip link set ${AP_IFACE} down
ExecStartPre=/sbin/ip addr flush dev ${AP_IFACE}
ExecStartPre=/sbin/ip link set ${AP_IFACE} up
ExecStartPre=/sbin/ip addr add ${GATEWAY_IP}/24 dev ${AP_IFACE}

# Restore iptables rules
ExecStart=/sbin/iptables-restore ${IPTABLES_RULES}

# Start the AP and DHCP/DNS daemons
ExecStartPost=/usr/bin/systemctl restart hostapd
ExecStartPost=/usr/bin/systemctl restart dnsmasq
${SYSTEMD_STARTPOST_EXTRA}

ExecStop=/usr/bin/systemctl stop hostapd
ExecStop=/usr/bin/systemctl stop dnsmasq
${SYSTEMD_STOP_EXTRA}
ExecStop=/sbin/iptables -t nat -F PREROUTING
ExecStop=/sbin/iptables -t nat -F POSTROUTING
ExecStop=/sbin/iptables -F FORWARD

Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target"

# ─── Unmask and enable hostapd (disabled/masked by default on Ubuntu) ─────────
info "Enabling hostapd..."
run systemctl unmask hostapd
run systemctl enable hostapd

if "$TLS_ENABLED"; then
    info "Enabling hotspot TCP mux service..."
fi

# ─── Enable iptables-persistent so rules survive reboot ───────────────────
run systemctl enable netfilter-persistent 2>/dev/null || \
    run systemctl enable iptables-persistent 2>/dev/null || true

# ─── Enable and start the kiosk service ───────────────────────────────────
run systemctl daemon-reload
if "$TLS_ENABLED"; then
    run systemctl enable hotspot-kiosk-haproxy
fi
run systemctl enable hotspot-kiosk
run systemctl start hotspot-kiosk

# ═════════════════════════════════════════════════════════════════════════════
# STEP 12 – Verify
# ═════════════════════════════════════════════════════════════════════════════
if ! "$DRY_RUN"; then
    echo ""
    info "Verifying services..."
    sleep 3

    if systemctl is-active --quiet hostapd; then
        ok "hostapd is running"
    else
        warn "hostapd failed to start – check: journalctl -u hostapd"
    fi

    if systemctl is-active --quiet dnsmasq; then
        ok "dnsmasq is running"
    else
        warn "dnsmasq failed to start – check: journalctl -u dnsmasq"
    fi

    if "$TLS_ENABLED"; then
        if systemctl is-active --quiet hotspot-kiosk-haproxy; then
            ok "hotspot-kiosk-haproxy is running"
        else
            warn "hotspot-kiosk-haproxy failed to start – check: journalctl -u hotspot-kiosk-haproxy"
        fi
    fi

    if ip addr show "$AP_IFACE" | grep -q "$GATEWAY_IP"; then
        ok "$AP_IFACE has IP $GATEWAY_IP"
    else
        warn "$AP_IFACE does not have IP $GATEWAY_IP"
    fi

    if iptables -t nat -L PREROUTING -n | grep -q "REDIRECT.*$WEB_PORT"; then
        ok "iptables redirect rules are active (→ port $WEB_PORT)"
    else
        warn "iptables redirect rules not found"
    fi
fi

# ═════════════════════════════════════════════════════════════════════════════
echo ""
ok "Hotspot kiosk setup complete."
echo ""
echo "  SSID    : $AP_SSID"
echo "  Password: $AP_PASSWORD"
echo "  Gateway : $GATEWAY_IP"
if "$TLS_ENABLED"; then
    echo "  Web UI  : https://$GATEWAY_IP:$WEB_PORT  (HTTP→$HTTP_REDIRECT_PORT, TLS mux→$MUX_PORT, WS→$WS_PORT)"
    echo "  Cert    : $HTTPS_CERT_FILE"
    echo "  Key     : $HTTPS_KEY_FILE"
else
    echo "  Web UI  : http://$GATEWAY_IP:$WEB_PORT  (redirected from :80, :443, and all ports)"
fi
echo ""
echo "  Clients connecting to '$AP_SSID' and opening any browser URL"
echo "  will be redirected to the web UI."
echo ""
echo "  To remove: sudo $0 --uninstall"
echo "  Logs:      journalctl -u hostapd -u dnsmasq -u hotspot-kiosk${LOG_SERVICE_EXTRA}"
echo "             /var/log/dnsmasq-kiosk.log"

if "$TLS_ENABLED"; then
    echo ""
    echo "  .env updated with:"
    echo "    WEB_UI_ENABLED=true"
    echo "    WEB_UI_PORT=$WEB_PORT"
    echo "    WEB_UI_WS_PORT=$WS_PORT"
    echo "    WEB_UI_SSL_CERTFILE=$HTTPS_CERT_FILE"
    echo "    WEB_UI_SSL_KEYFILE=$HTTPS_KEY_FILE"
    echo "    WEB_UI_HTTP_REDIRECT_PORT=$HTTP_REDIRECT_PORT"
fi
