# superstrike-hits-linux

Control the **Logitech G PRO X2 SUPERSTRIKE** click-haptics and actuation points
on **Linux**, without G HUB.

The PRO X2 SUPERSTRIKE uses Logitech's **HITS** (Haptic Inductive Trigger System):
inductive analog main switches + a vibration motor that simulates the "click", with
**6 click-haptics levels** and **10 actuation-point levels**. On Windows these are set
in G HUB. On Linux there was **no way to change them** — the mouse is stuck on whatever
the onboard profile last had.

This project reverse-engineers the relevant HID++ feature (**`0x1B0C`**, not present in
libratbag/Solaar) and gives you a small, dependency-free CLI to read and set those values.
As of writing, neither **Solaar** nor **libratbag** supports this device's HITS settings.

## Features

- Read / set **click-haptics** (1–6) and **actuation point** (1–10), per button.
- Pure Python standard library, talks to `/dev/hidraw` directly. No daemon, no deps.
- **Auto-detects** the receiver's HID++ control interface (FF00 usage page) — no need to
  guess the right `/dev/hidrawN`.
- Optional **persistence**: save your levels and re-apply automatically on connect/boot.

## Requirements

- A Logitech **LIGHTSPEED receiver** (`046d:c54d`) with the SUPERSTRIKE paired.
  (Kernel `hid-logitech-dj` already exposes it; wired/direct should also work.)
- Python 3.

## Install

```bash
git clone https://github.com/kazehana99k/Logitech-PRO-X2-SUPERSTRIKE-Linux-haptics-actuation-control-.git
cd Logitech-PRO-X2-SUPERSTRIKE-Linux-haptics-actuation-control-
chmod +x superstrike-hits.py

# non-root access (recommended): install the udev rule
sudo cp 42-logitech-superstrike.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
# then re-plug the receiver
```

## Usage

```bash
./superstrike-hits.py --get               # read current per-button settings
./superstrike-hits.py --haptics 4         # set click-haptics to level 4 (both buttons)
./superstrike-hits.py --actuation 7       # set actuation point to level 7
./superstrike-hits.py --haptics 6 --left  # left button only
./superstrike-hits.py --haptics 3 --actuation 8 --save   # set + remember as default
```

### Persistence (re-apply on every connect)

`--save` writes your levels to `~/.config/superstrike-hits.conf`; `--apply` reads and
applies them. Wire `--apply` to a udev/systemd hook so settings survive reboots and the
onboard profile being changed by Windows/G HUB. (See the repo wiki / issues for a sample
systemd unit.)

## GUI + CachyOS / Arch install (fork addition)

This fork adds **`superstrike-gui.py`**, a dark PySide6 window built on the functions in
`superstrike-hits.py`, so it behaves exactly like the CLI:

| Tab | What you can do |
|---|---|
| **Click (HITS)** | per-button **actuation** (1–10), **click haptics** (1–6, 1 = off), **rapid trigger** (1–5); link both buttons; defaults; read back; apply. Advanced: write raw bytes (`--raw-actuation` / `--raw-haptics`), checked against the device max and the multiple-of-4 rule |
| **Polling rate** | current rate and link type, one-click presets for every rate the mouse reports (up to 8000 Hz), and a custom number box capped at 8000 that snaps to the nearest supported rate, shown as "snapped 3000 → 2000" |
| **DPI** | current DPI, allowed range read from the mouse, X / Y / lift-off distance, presets 400–6400, values snapped to the mouse's steps |
| **Saved config & background** | `--save` / `--apply` with one click, view the saved file, auto-apply on plug-in/login, **Keep settings after sleep (background service)** with live status, tray options |
| **Feature table** | `--dump-features` as a table (0x1B0C highlighted) |

Also: device picker (`--device`), a log panel, status and error messages, and a **system tray icon**. Mouse I/O runs in a background thread, so the window never freezes.

### Install on CachyOS (fish is fine)

```bash
git clone <your fork URL> superstrike && cd superstrike   # or untar the release
./install.sh            # sudo pacman -S python pyside6, udev rules, ~/.local/bin launchers, systemd user unit
# unplug + replug the receiver once
superstrike-gui         # or "SUPERSTRIKE Control" in the app menu
superstrike-hits --get  # CLI still works the same
```
If `~/.local/bin` is not in your PATH, fish users run `fish_add_path ~/.local/bin` (only needed for typing the commands;
the app-menu entry uses an absolute path). To remove everything: `./install.sh --uninstall` (keeps your saved config).

### "SUPERSTRIKE Control" in the app menu does nothing?

Cause (fixed): the menu entry used `Exec=superstrike-gui`, which only works if `~/.local/bin` is on the *session* PATH.
KDE Plasma's menu doesn't read your fish/bash config, so the launch failed silently. Now:

* `install.sh` writes the menu entry with an **absolute** `Exec=/home/<you>/.local/bin/superstrike-gui` (+ `TryExec`),
  checks it with `desktop-file-validate` (if `desktop-file-utils` is installed) and refreshes the KDE menu cache.
* The `~/.local/bin/superstrike-gui` wrapper calls `/usr/bin/python3` directly, logs every start to
  **`~/.cache/superstrike/gui.log`**, and if the app exits with an error you get a desktop notification
  (`notify-send`, falling back to a `kdialog`/`zenity` dialog) with the last log lines.
* `superstrike-gui --diagnose` prints python/PySide6, display variables, the desktop + autostart files, `hidraw`
  permissions, udev rules, both services, the saved config and a live read of the mouse. Paste it in an issue.

Just re-run `./install.sh` after updating.

### DPI goes back to 800 after the mouse has been idle? → "Keep settings after sleep"

Cause: DPI / polling rate are only writable in **host mode** (see error 5 below). When the mouse idles it drops the
LIGHTSPEED link to save power; when you move it again it reconnects and boots on its **onboard profile** (e.g. 800 DPI),
forgetting host mode. The same happens when the PC suspends and the USB dongle is reset.

Fix: *Saved config & background* tab → tick **Keep settings after sleep (background service)**. This enables
`superstrike-watch.service` (a systemd `--user` service, ~0 % CPU) that re-applies your saved config
(host mode + DPI + polling rate + click settings) when:

* the dongle reports the mouse **reconnected**: HID++ 1.0 `0x41` connect notification or the HID++ 2.0 `0x1D4B`
  "wireless device status: reconfiguration needed" event (the same signals Solaar uses);
* a receiver `hidraw` node **appears** again (plug-in, USB reset after resume); a node that disappears is dropped and
  re-scanned every 3 s;
* the **PC resumes** from suspend (detected by `CLOCK_BOOTTIME` jumping ahead of `CLOCK_MONOTONIC`);
* a light safety-net check (every 30 s: ping + read profile mode) finds the mouse back in onboard mode.

It just sleeps in `select()` on the hidraw node (no busy polling) and only opens it read-only to listen. All writes,
from the service, the GUI and the tray, take one shared lock (`$XDG_RUNTIME_DIR/superstrike.lock`), so they never
interleave. While the service is on, every change you apply in the GUI/tray is saved automatically so it restores the
latest values. If you switch back to the onboard profile while it's on, it will put host mode back on the next wake;
turn it off if you want the onboard profile.

```bash
systemctl --user status superstrike-watch.service     # running?
journalctl --user -u superstrike-watch -f              # watch it re-apply when you wake the mouse
superstrike-watch --status                             # last re-apply / error (JSON)
superstrike-watch --once                               # apply the saved config once, now
```

**Why not write the DPI into the onboard profile instead?** That would survive sleep with no service, but the X3's
onboard-profile memory format (`0x8100` sectors, CRC, the new "GPX2" layout) is not verified: libratbag's support
for this family (PR #1896) was never merged, and no one has tested profile writes on this mouse with any Linux tool. A wrong write can corrupt a profile and leave the mouse with odd DPI/buttons until G HUB repairs it, so
this tool deliberately doesn't do it.

### System tray

The app puts an icon in the tray (KDE: next to the clock). Right-click it for:

* **Open SUPERSTRIKE Control**, the current **DPI · polling rate** (read-only line),
* **DPI** presets (400 – 6400) and **Polling rate** presets (the rates the mouse reports, up to 8000 Hz),
* **Re-apply saved settings**, **Quit**.

Left-click toggles the window. Options in *Saved config & background*:

* *Closing the window keeps SUPERSTRIKE Control in the system tray* (on by default; use **Quit** in the tray menu to exit).
* *Start minimized to tray at login*: writes `~/.config/autostart/superstrike-gui.desktop` with `--tray`.

Only one copy runs: launching it again (menu, `superstrike-gui`) shows the existing window. The tray never listens to
the mouse itself; the background service does that, and both use the shared lock.

Try the GUI without a mouse: `python3 superstrike-gui.py --mock`.

### DPI says "hid++ error 5 (LOGITECH_INTERNAL) … fn=6"?

That's the mouse refusing DPI changes because it's in **onboard-profile mode**: its stored profile owns DPI.
This was seen on a real PRO X3 SUPERSTRIKE. The fix switches the mouse to **host mode** (HID++ `0x8100` setMode 2,
the same thing Solaar's "Onboard Profiles: Disabled" does) before writing DPI:

* GUI: *DPI* tab → "Switch to host mode automatically when applying DPI" (on by default), or the
  *Switch to host mode* / *Back to onboard profile* buttons.
* CLI: `--dpi` switches automatically (`--no-host-switch` turns that off), and `--onboard status|host|onboard` shows or sets the mode.
* Host mode is **lost when the mouse powers off, sleeps or reconnects**. Save your settings and turn on
  **Keep settings after sleep** (above) to restore them automatically.
  In host mode the onboard profile's button mapping and DPI stages don't apply. HITS click settings are kept in both modes.

Still failing? Turn on the GUI's *Debug: log raw HID++ frames* box, try again, press *Copy log* and send it,
or run `superstrike-hits --trace --dpi 1600 2> dpi-trace.txt`.

### Polling rate (up to 8000 Hz)

* `--rate 8000`, `--rate-list`. In the GUI, use the *Polling rate* tab. HID++ `0x8061` (extended report rate), falling back to `0x8060`.
* The mouse only runs at the **fixed rates it reports**. On the SUPERSTRIKE that's normally 125 / 250 / 500 / 1000 / 2000 / 4000 / 8000 Hz.
  There's no 400 Hz or 3000 Hz in the hardware. Any number you type (capped at 8000) is snapped to the nearest
  supported rate (a tie goes to the lower one), and only that rate is sent. Unsupported values are never sent.
* The current rate is read with a "wireless" link byte when the mouse is behind a receiver or dongle. Solaar PR #3352
  found that the X3 dongle otherwise answers for the wired link (it showed 1000 Hz while really at 8000).
* Like DPI, the rate belongs to the onboard profile. The app switches to host mode first (see above), and
  `--save` / auto-apply include `polling_hz`.
* 8000 Hz drains the battery much faster (Tom's Hardware: under 40 h on the X3, compared with 135 h at 1000 Hz).

### New CLI options in this fork

```bash
./superstrike-hits.py --rapid-trigger 1            # 1 = fastest reset .. 5
./superstrike-hits.py --dpi 1600                   # snapped to the nearest allowed value
./superstrike-hits.py --dpi 1600 --dpi-y 1200 --lod low
./superstrike-hits.py --dpi-list                   # every DPI the mouse accepts
./superstrike-hits.py --dump-features              # documented in PROTOCOL.md, now implemented
./superstrike-hits.py --onboard status             # onboard-profile vs host mode (host = DPI can be set)
./superstrike-hits.py --trace --get                # print raw HID++ frames (for bug reports)
./superstrike-hits.py --raw-actuation 0x10 --raw-haptics 0x0c --left
```

Where these come from:
* **Rapid trigger** is the `x` byte: `level << 2`, with bit 0 a firmware flag that is kept. The `0x09` seen in
  PROTOCOL.md decodes to level 2 + flag. The encoding comes from Solaar (PR #3132 + fix #3207), which was tested on a real X2.
* **DPI** uses HID++ `0x2202` (extended adjustable DPI, present on the X2), with a fallback to `0x2201`. Both use Solaar's wire format.
* `--save` now also stores `rapid_trigger` and `dpi`. Old config files still apply.
* Bug fix: HID++ error replies are now recognised. Before, every failed call waited about 2.4 s for retries.

### PRO X3 SUPERSTRIKE?

The X3 uses a new dongle **046d:c54f** (mouse WPID 40BE). According to Solaar PR #3352 (tested on a real X3),
that dongle speaks HID++ 2.0 with the mouse at **device index 1**, and the same `0x1B0C` tuning works unchanged.
This tool already tries index 1 first, on any Logitech hidraw node with the FF00 usage page. So the X3 *should*
work as-is, but **nobody has tested it with this tool yet**. The fork only adds c54f to the udev permission and
auto-apply rules. Nothing in the protocol code changed for it.

## How it works

Full reverse-engineering notes and the exact HID++ protocol (feature `0x1B0C`, the
`[button][actuation][x][haptics]` record format, level↔byte mappings, and how it was
captured from G HUB) are in **[PROTOCOL.md](PROTOCOL.md)**.

## Status / TODO

- [x] `x` = rapid trigger (level<<2 | flag), exposed as `--rapid-trigger` (per Solaar's hardware-tested encoding).
- [ ] Upstream as a libratbag device entry and/or a Solaar feature plugin for `0x1B0C`.
- [x] Packaged systemd/udev auto-apply (`install.sh`, toggle in the GUI).

## Credits

Reverse-engineered by [**kazehana99k**](https://github.com/kazehana99k) (USBPcap
differential capture of G HUB + on-device HID++ probing). Contributions / upstreaming welcome.

## Disclaimer

This is an independent, community project. It is **not affiliated with, authorized, or
endorsed by Logitech**. "Logitech", "G PRO", "PRO X2 SUPERSTRIKE", "LIGHTSPEED" and any
related names are trademarks of their respective owners and are used here only nominatively,
to describe hardware compatibility. The protocol was reverse-engineered for interoperability
with hardware the author owns; no Logitech software, firmware, or other copyrighted material
is included or redistributed. The software is provided "as is", without warranty of any kind —
use at your own risk, and you are responsible for complying with the laws and license terms
applicable to you.

## License

MIT — see [LICENSE](LICENSE).
