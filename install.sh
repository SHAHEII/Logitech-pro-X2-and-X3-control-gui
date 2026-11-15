#!/usr/bin/env bash
# CachyOS / Arch installer for superstrike-hits + superstrike-gui
#   ./install.sh               install (asks for sudo for pacman + udev rules)
#   ./install.sh --uninstall   remove everything again
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="$HOME/.local/share/superstrike-hits"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
UNITS="$HOME/.config/systemd/user"
RULES=(42-logitech-superstrike.rules 43-superstrike-autoapply.rules)

if [[ "${1:-}" == "--uninstall" ]]; then
  systemctl --user disable superstrike-apply.service 2>/dev/null || true
  rm -rf "$DEST" "$BIN/superstrike-hits" "$BIN/superstrike-gui" "$APPS/superstrike-gui.desktop" \
         "$UNITS/superstrike-apply.service" "$HOME/.config/superstrike-hits.autoapply"
  systemctl --user daemon-reload 2>/dev/null || true
  for r in "${RULES[@]}"; do sudo rm -f "/etc/udev/rules.d/$r"; done
  sudo udevadm control --reload-rules
  echo "Uninstalled (your saved config ~/.config/superstrike-hits.conf was kept)."
  exit 0
fi

echo "==> pacman: python + pyside6"
sudo pacman -S --needed --noconfirm python pyside6

echo "==> installing app to $DEST"
mkdir -p "$DEST" "$BIN" "$APPS" "$UNITS"
install -m 755 "$HERE/superstrike-hits.py" "$HERE/superstrike-gui.py" "$DEST/"
install -m 644 "$HERE/superstrike_mock.py" "$DEST/"
printf '#!/bin/sh\nexec python3 "%s/superstrike-hits.py" "$@"\n' "$DEST" > "$BIN/superstrike-hits"
printf '#!/bin/sh\nexec python3 "%s/superstrike-gui.py" "$@"\n' "$DEST" > "$BIN/superstrike-gui"
chmod 755 "$BIN/superstrike-hits" "$BIN/superstrike-gui"
install -m 644 "$HERE/packaging/superstrike-gui.desktop" "$APPS/"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" || true

echo "==> udev rules (non-root hidraw access + auto-apply hook)"
sudo install -m 644 "$HERE/42-logitech-superstrike.rules" /etc/udev/rules.d/
sudo install -m 644 "$HERE/packaging/43-superstrike-autoapply.rules" /etc/udev/rules.d/
sudo udevadm control --reload-rules
sudo udevadm trigger --subsystem-match=hidraw

echo "==> systemd user service (does nothing until you tick 'auto-apply' in the GUI)"
install -m 644 "$HERE/packaging/superstrike-apply.service" "$UNITS/"
systemctl --user daemon-reload
systemctl --user enable superstrike-apply.service >/dev/null 2>&1 || true

case ":$PATH:" in *":$BIN:"*) ;; *)
  echo; echo "NOTE: $BIN is not in your PATH."
  echo "  fish: fish_add_path ~/.local/bin      bash/zsh: export PATH=\"\$HOME/.local/bin:\$PATH\"";;
esac
echo
echo "Done. Unplug + replug the receiver once, then:"
echo "  superstrike-gui                 (or 'SUPERSTRIKE Control' in the app menu)"
echo "  superstrike-hits --get          (CLI)"
