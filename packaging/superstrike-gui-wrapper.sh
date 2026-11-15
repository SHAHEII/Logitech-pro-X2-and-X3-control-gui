#!/bin/sh
# superstrike-gui launcher (installed to ~/.local/bin/superstrike-gui by install.sh).
# Uses absolute paths so it works from the app menu even when ~/.local/bin is not in the session PATH,
# logs to ~/.cache/superstrike/gui.log and shows a desktop notification if the app fails to start.
APP="@DEST@/superstrike-gui.py"
LOG="$HOME/.cache/superstrike/gui.log"
mkdir -p "$HOME/.cache/superstrike"
PY="${SUPERSTRIKE_PYTHON:-/usr/bin/python3}"
[ -x "$PY" ] || PY="$(command -v python3)"
export SUPERSTRIKE_WRAPPER=1
case " $* " in *" --diagnose "*) exec "$PY" "$APP" "$@";; esac
echo "--- $(date '+%F %T') launcher: $PY $APP $* (PATH=$PATH WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-} DISPLAY=${DISPLAY:-})" >> "$LOG"
"$PY" "$APP" "$@" 2>>"$LOG"
rc=$?
if [ "$rc" -ne 0 ]; then
  msg="SUPERSTRIKE Control exited with code $rc.
$(tail -n 6 "$LOG" 2>/dev/null)

Full log: $LOG   -   run 'superstrike-gui --diagnose' in a terminal."
  if command -v notify-send >/dev/null 2>&1; then
    notify-send -a "SUPERSTRIKE Control" -i input-mouse "SUPERSTRIKE Control failed to start" "$msg"
  elif command -v kdialog >/dev/null 2>&1; then
    kdialog --title "SUPERSTRIKE Control" --error "$msg"
  elif command -v zenity >/dev/null 2>&1; then
    zenity --error --text="$msg"
  fi
fi
exit "$rc"
