# Changelog

## 1.0.1

Write DPI and polling rate into the format-8 onboard profile and stay in onboard mode, instead of switching to host mode. On SUPERSTRIKE firmware, host mode can freeze the pointer while leaving the control channel up, so the app looked successful while the cursor was dead.

- Persist DPI and polling rate with a read-modify-write of the active format-8 onboard profile (HID++ `0x8100`, CRC-16/CCITT-FALSE with init `0xFFFF`), then stay onboard — the same approach G HUB uses. Only format 8 is written; the rest of the sector (buttons / macros) is copied unchanged.
- Host mode is only a fallback when the profile layout is not format 8 (or a profile write fails) and `host_mode=auto`, or when host mode is requested explicitly.
- `superstrike-watch` is a safety net, not the persistence design. It re-applies if live DPI/rate drifted from the saved config, and no longer treats onboard mode itself as a failure. It scans for the HID++ node instead of opening the reconnect listener path (which after USB re-enumeration can be a non-HID++ hidraw device).
- GUI checkbox (still on by default): "Write DPI and polling rate into the onboard profile". Unticked is a live HID++ write only (`host_mode=never`).

## 0.4.0

Previous GUI fork release: DPI, polling rate up to 8000 Hz, system tray, keep-settings watcher, Arch/CachyOS installer and NixOS module.
