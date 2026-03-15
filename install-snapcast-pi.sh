#!/usr/bin/env bash
# install-snapcast-pi.sh
# Run on the Raspberry Pi at 10.1.1.210 to install Snapcast server + client
# and configure PipeWire snapcast-discover (if PipeWire is present).
#
# Usage:
#   ssh stever@10.1.1.210 'bash -s' < install-snapcast-pi.sh
#   -- or copy and run directly on the Pi --

set -euo pipefail

echo "==> Updating package lists..."
sudo apt-get update -qq

echo "==> Installing snapserver and snapclient..."
sudo apt-get install -y snapserver snapclient

echo "==> Snapcast versions installed:"
snapserver --version 2>/dev/null || true
snapclient --version 2>/dev/null || true

echo "==> Enabling and starting snapserver..."
sudo systemctl enable snapserver
sudo systemctl start  snapserver
sudo systemctl status snapserver --no-pager -l || true

echo "==> Configuring snapclient output (USB speaker preferred)..."
usb_device="$(aplay -l 2>/dev/null | awk 'BEGIN{IGNORECASE=1}
    /card [0-9]+:.*usb|usb.*audio|USB Audio/ {
        match($0,/card ([0-9]+)/,c)
        match($0,/device ([0-9]+)/,d)
        if (c[1] != "" && d[1] != "") {
            print "hw:" c[1] "," d[1]
            exit
        }
    }')"
if [[ -z "$usb_device" ]]; then
        usb_device="default"
fi

snap_opts="--hostID Pi-Two --stream 'Pi Two' --player alsa --soundcard ${usb_device}"
if grep -q '^SNAPCLIENT_OPTS=' /etc/default/snapclient; then
        sudo sed -i "s|^SNAPCLIENT_OPTS=.*|SNAPCLIENT_OPTS=\"${snap_opts}\"|" /etc/default/snapclient
else
        echo "SNAPCLIENT_OPTS=\"${snap_opts}\"" | sudo tee -a /etc/default/snapclient >/dev/null
fi

echo "==> Enabling snapclient (stream: Pi Two, device: ${usb_device})..."
sudo systemctl enable snapclient
sudo systemctl start  snapclient
sudo systemctl status snapclient --no-pager -l || true

# --- PipeWire snapcast-discover (optional, if PipeWire is running) ---
if command -v pipewire >/dev/null 2>&1; then
    echo "==> PipeWire detected - installing snapcast-discover config..."
    mkdir -p ~/.config/pipewire/pipewire.conf.d
    cat > ~/.config/pipewire/pipewire.conf.d/snapcast-discover.conf << 'EOF'
context.modules = [
{   name = libpipewire-module-snapcast-discover
    args = {
        snapcast.discover-local = true
        stream.rules = [
            {   matches = [
                    {   snapcast.ip = "~.*" }
                ]
                actions = {
                    create-stream = {
                        audio.rate     = 48000
                        audio.format   = S16LE
                        audio.channels = 2
                        audio.position = [ FL FR ]
                        node.description = "Pi Two"
                        node.name = "snapcast.pi-two"
                        snapcast.stream-name = "Pi Two"
                    }
                }
            }
        ]
    }
}
]
EOF
    systemctl --user restart pipewire pipewire-pulse 2>/dev/null && \
        echo "PipeWire restarted with snapcast-discover." || \
        echo "PipeWire restart skipped (no user session or not running)."
else
    echo "PipeWire not found - skipping snapcast-discover config."
fi

echo ""
echo "==> Done. Snapcast server is listening on port 1704 (control: 1705)."
echo "    Snapclient will connect to localhost by default."
echo "    Config files: /etc/snapserver.conf  /etc/default/snapclient"
