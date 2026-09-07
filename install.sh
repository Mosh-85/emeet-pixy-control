#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -eq 0 ]]; then
    echo "Run ./install.sh as your normal desktop user, not as root." >&2
    exit 1
fi

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
DATA_HOME=${XDG_DATA_HOME:-"$HOME/.local/share"}
APP_HOME="$DATA_HOME/emeet-pixy-control"
VENV="$APP_HOME/venv"
DESKTOP_DIR="$DATA_HOME/applications"
ICON_DIR="$DATA_HOME/icons/hicolor/scalable/apps"
BIN_DIR=${XDG_BIN_HOME:-"$HOME/.local/bin"}
DESKTOP_FILE="$DESKTOP_DIR/emeet-pixy-control.desktop"
TEST_DESKTOP_FILE="$DESKTOP_DIR/emeet-pixy-control-foreground.desktop"
ICON_FILE="$ICON_DIR/emeet-pixy-control.svg"
USER_SERVICE_DIR="$HOME/.config/systemd/user"
USER_SERVICE_FILE="$USER_SERVICE_DIR/emeet-pixy-control.service"

need=()
for cmd in python3 ffmpeg v4l2-ctl v4l2loopback-ctl modprobe systemctl; do
    command -v "$cmd" >/dev/null 2>&1 || need+=("$cmd")
done

if ((${#need[@]})); then
    echo "Missing required commands: ${need[*]}" >&2
    echo >&2
    if command -v pacman >/dev/null 2>&1; then
        echo "Arch/CachyOS prerequisites:" >&2
        echo "  sudo pacman -S --needed python python-pip v4l-utils ffmpeg v4l2loopback-utils" >&2
        echo "On vanilla Arch, install the headers matching your kernel if DKMS needs them." >&2
    elif command -v apt >/dev/null 2>&1; then
        echo "Debian/Ubuntu prerequisites:" >&2
        echo '  sudo apt install python3 python3-venv v4l-utils ffmpeg v4l2loopback-utils v4l2loopback-dkms linux-headers-$(uname -r)' >&2
    fi
    exit 1
fi

if ! python3 -m venv --help >/dev/null 2>&1; then
    echo "Python venv support is required (often package python3-venv)." >&2
    exit 1
fi

mkdir -p "$APP_HOME" "$DESKTOP_DIR" "$ICON_DIR" "$BIN_DIR" "$USER_SERVICE_DIR"

if [[ ! -x "$VENV/bin/python" ]]; then
    python3 -m venv "$VENV"
fi

"$VENV/bin/python" -m pip install --upgrade "$ROOT"

install -m 0644 "$ROOT/assets/emeet-pixy-control.svg" "$ICON_FILE"

exec_path="$VENV/bin/emeet-pixy-control"
foreground_exec="$exec_path --foreground"

sed "s#@EXEC@#$exec_path#g" "$ROOT/deploy/emeet-pixy-control.desktop.in" > "$DESKTOP_FILE"
chmod 0644 "$DESKTOP_FILE"

sed "s#@EXEC@#$foreground_exec#g" "$ROOT/deploy/emeet-pixy-control.desktop.in" | \
    sed "s#Name=EMEET PIXY Control#Name=EMEET PIXY Control (Preview Test)#" > "$TEST_DESKTOP_FILE"
chmod 0644 "$TEST_DESKTOP_FILE"

rm -f "$BIN_DIR/emeet-pixy-privacy-toggle" \
    "$BIN_DIR/emeet-pixy-privacy-on" \
    "$BIN_DIR/emeet-pixy-privacy-off"
install -m 0755 "$ROOT/deploy/emeet-pixy-privacy-toggle" "$BIN_DIR/emeet-pixy-privacy-toggle"

cat > "$USER_SERVICE_FILE" <<SERVICE
[Unit]
Description=EMEET PIXY Control background service
After=graphical-session.target

[Service]
Type=simple
ExecStart=$exec_path --background
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
SERVICE
chmod 0644 "$USER_SERVICE_FILE"

if systemctl --user --version >/dev/null 2>&1; then
    if command -v loginctl >/dev/null 2>&1; then
        sudo loginctl enable-linger "$USER" || true
    fi
    systemctl --user daemon-reload
    systemctl --user enable emeet-pixy-control.service
    systemctl --user restart emeet-pixy-control.service || true
fi

sudo install -m 0644 "$ROOT/deploy/70-emeet-pixy.rules" /etc/udev/rules.d/70-emeet-pixy.rules
sudo install -d -m 0755 /usr/local/lib/emeet-pixy-control
sudo install -m 0755 "$ROOT/deploy/virtual-camera-device" /usr/local/lib/emeet-pixy-control/virtual-camera-device
sudo install -m 0644 "$ROOT/deploy/emeet-pixy-virtual-camera.service" /etc/systemd/system/emeet-pixy-virtual-camera.service

sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=hidraw || true
sudo systemctl daemon-reload
sudo systemctl enable emeet-pixy-virtual-camera.service

if [[ -d "/lib/modules/$(uname -r)" ]] && modinfo v4l2loopback >/dev/null 2>&1; then
    if ! sudo systemctl restart emeet-pixy-virtual-camera.service; then
        echo "Warning: virtual-camera service could not start. Check:" >&2
        echo "  systemctl status emeet-pixy-virtual-camera.service" >&2
    fi
else
    echo "Warning: v4l2loopback is not available for the currently running kernel." >&2
    echo "The service is enabled and should start after you install the module or reboot into the updated kernel." >&2
fi

if getent group video >/dev/null 2>&1 && ! id -nG "$USER" | tr ' ' '\n' | grep -qx video; then
    sudo usermod -aG video "$USER"
    echo "Added $USER to the video group. Log out and back in before using Virtual Camera mode." >&2
fi

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$DESKTOP_DIR" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -f -t "$DATA_HOME/icons/hicolor" >/dev/null 2>&1 || true

cat <<MSG

EMEET PIXY Control installed.

Launch it from your application menu: EMEET PIXY Control
Foreground testing launch: EMEET PIXY Control (Preview Test)
Background autostart: enabled for your user session
Quick privacy actions:
  $BIN_DIR/emeet-pixy-privacy-toggle
CLI backend: $VENV/bin/emeet-pixy-cli

If HID controls are denied, unplug/replug the PIXY once (or log out/in).
Virtual camera service status:
  systemctl status emeet-pixy-virtual-camera.service
MSG
