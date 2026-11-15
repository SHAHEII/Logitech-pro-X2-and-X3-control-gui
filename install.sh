#!/usr/bin/env bash
# CachyOS / Arch installer for superstrike-hits + superstrike-gui + superstrike-watch
#   ./install.sh               install (asks for sudo for pacman + udev rules)
#   ./install.sh --uninstall   remove everything again (keeps your saved config)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/superstrike-hits"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
UNITS="$HOME/.config/systemd/user"
AUTOSTART="${XDG_CONFIG_HOME:-$HOME/.config}/autostart/superstrike-gui.desktop"
ICON="$HOME/.local/share/icons/hicolor/scalable/apps/superstrike-gui.svg"
RULES=(42-logitech-superstrike.rules 43-superstrike-autoapply.rules)
SUDO="${SUDO-sudo}"   # tests run with SUDO= and a fake HOME

if [[ "${1:-}" == "--uninstall" ]]; then
  pkill -f "$DEST/superstrike-gui.py" 2>/dev/null || true
  systemctl --user disable --now superstrike-watch.service 2>/dev/null || true
  systemctl --user disable superstrike-apply.service 2>/dev/null || true
  rm -rf "$DEST" "$BIN/superstrike-hits" "$BIN/superstrike-gui" "$BIN/superstrike-watch" \
         "$APPS/superstrike-gui.desktop" "$AUTOSTART" "$ICON" \
         "$UNITS/superstrike-apply.service" "$UNITS/superstrike-watch.service" \
         "$HOME/.config/superstrike-hits.autoapply" "$HOME/.config/superstrike/superstrike-gui.conf"
  systemctl --user daemon-reload 2>/dev/null || true
  command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
  if [[ "${SKIP_ROOT:-}" != 1 ]]; then
    for r in "${RULES[@]}"; do $SUDO rm -f "/etc/udev/rules.d/$r"; done
    $SUDO udevadm control --reload-rules
  fi
  echo "Uninstalled (kept: saved config ~/.config/superstrike-hits.conf and logs ~/.cache/superstrike)."
  exit 0
fi

if [[ "${SKIP_ROOT:-}" != 1 ]]; then
  echo "==> pacman: python + pyside6"
  $SUDO pacman -S --needed --noconfirm python pyside6
fi

echo "==> installing app to $DEST"
mkdir -p "$DEST" "$BIN" "$APPS" "$UNITS" "$HOME/.cache/superstrike"
install -m 755 "$HERE/superstrike-hits.py" "$HERE/superstrike-gui.py" "$HERE/superstrike-watch.py" "$DEST/"
install -m 644 "$HERE/superstrike_mock.py" "$HERE/README.md" "$DEST/"
printf '#!/bin/sh\nexec /usr/bin/python3 "%s/superstrike-hits.py" "$@"\n' "$DEST" > "$BIN/superstrike-hits"
printf '#!/bin/sh\nexec /usr/bin/python3 "%s/superstrike-watch.py" "$@"\n' "$DEST" > "$BIN/superstrike-watch"
sed "s|@DEST@|$DEST|g" "$HERE/packaging/superstrike-gui-wrapper.sh" > "$BIN/superstrike-gui"
chmod 755 "$BIN/superstrike-hits" "$BIN/superstrike-gui" "$BIN/superstrike-watch"

echo "==> app menu entry (absolute Exec path: the KDE menu does not use your shell PATH)"
sed "s|@BIN@|$BIN|g" "$HERE/packaging/superstrike-gui.desktop" > "$APPS/superstrike-gui.desktop"
install -Dm 644 "$HERE/packaging/superstrike-gui.svg" "$ICON"
chmod 644 "$APPS/superstrike-gui.desktop"
if command -v desktop-file-validate >/dev/null; then
  desktop-file-validate "$APPS/superstrike-gui.desktop" && echo "    desktop-file-validate: OK"
else
  echo "    (desktop-file-validate not installed - skipped; it is in the 'desktop-file-utils' package)"
fi
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true
# keep an existing login-autostart entry pointing at the absolute wrapper
if [[ -f "$AUTOSTART" ]]; then
  sed -i "s|^Exec=.*|Exec=$BIN/superstrike-gui --tray|" "$AUTOSTART"
fi

if [[ "${SKIP_ROOT:-}" != 1 ]]; then
  echo "==> udev rules (non-root hidraw access + auto-apply hook)"
  $SUDO install -m 644 "$HERE/42-logitech-superstrike.rules" /etc/udev/rules.d/
  $SUDO install -m 644 "$HERE/packaging/43-superstrike-autoapply.rules" /etc/udev/rules.d/
  $SUDO udevadm control --reload-rules
  $SUDO udevadm trigger --subsystem-match=hidraw
fi

echo "==> systemd user services"
install -m 644 "$HERE/packaging/superstrike-apply.service" "$HERE/packaging/superstrike-watch.service" "$UNITS/"
systemctl --user daemon-reload 2>/dev/null || true
systemctl --user enable superstrike-apply.service >/dev/null 2>&1 || true
if systemctl --user is-enabled superstrike-watch.service >/dev/null 2>&1; then
  systemctl --user restart superstrike-watch.service || true   # pick up the new version
fi

echo
echo "Done. Unplug + replug the receiver once, then start 'SUPERSTRIKE Control' from the app menu"
echo "(or run: $BIN/superstrike-gui). In the 'Saved config' tab tick"
echo "  'Keep settings after sleep (background service)'  -> stops the DPI going back to 800 after idle"
echo "If the menu entry still does nothing: $BIN/superstrike-gui --diagnose   (log: ~/.cache/superstrike/gui.log)"
