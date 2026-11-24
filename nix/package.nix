# Nix package for superstrike-hits (CLI), superstrike-gui (PySide6 GUI + tray) and superstrike-watch (background service)
{ lib
, stdenvNoCC
, python3
, makeWrapper
, qt6
, coreutils
, libnotify
, desktop-file-utils
}:

let
  pythonEnv = python3.withPackages (ps: [ ps.pyside6 ]);
in
stdenvNoCC.mkDerivation {
  pname = "superstrike-hits";
  version = "1.0.1";

  src = lib.cleanSourceWith {
    src = ../.;
    filter = path: type:
      let base = baseNameOf path; in
      !(lib.elem base [ ".git" "result" "__pycache__" ".pytest_cache" ".ruff_cache" "screenshots" ]);
  };

  nativeBuildInputs = [ makeWrapper qt6.wrapQtAppsHook desktop-file-utils ];
  buildInputs = [ qt6.qtbase qt6.qtwayland ];
  dontWrapQtApps = true; # we wrap the python launchers ourselves below
  dontBuild = true;

  installPhase = ''
    runHook preInstall
    share=$out/share/superstrike
    install -Dm644 superstrike-hits.py superstrike-gui.py superstrike-watch.py superstrike_mock.py -t $share
    install -Dm644 README.md LICENSE PROTOCOL.md -t $out/share/doc/superstrike

    # udev: non-root hidraw access (uaccess) + optional auto-apply hook
    install -Dm644 42-logitech-superstrike.rules -t $out/lib/udev/rules.d
    install -Dm644 packaging/43-superstrike-autoapply.rules -t $out/lib/udev/rules.d

    # icon + app-menu entry (bare Exec is fine on NixOS: the package's bin is on the session PATH)
    install -Dm644 packaging/superstrike-gui.svg $out/share/icons/hicolor/scalable/apps/superstrike-gui.svg
    sed -e "s|@BIN@/superstrike-gui|superstrike-gui|g" packaging/superstrike-gui.desktop \
      > superstrike-gui.desktop
    install -Dm644 superstrike-gui.desktop -t $out/share/applications
    desktop-file-validate $out/share/applications/superstrike-gui.desktop

    # systemd user units (picked up by systemd.packages in the NixOS module)
    mkdir -p $out/lib/systemd/user
    cat > $out/lib/systemd/user/superstrike-watch.service <<UNIT
    [Unit]
    Description=Keep Logitech SUPERSTRIKE settings after mouse sleep / reconnect / PC resume (superstrike-watch)
    ConditionPathExists=%h/.config/superstrike-hits.conf
    After=graphical-session.target

    [Service]
    Type=simple
    ExecStart=$out/bin/superstrike-watch
    Restart=on-failure
    RestartSec=5
    Nice=10
    CPUQuota=10%
    MemoryMax=64M

    [Install]
    WantedBy=default.target
    UNIT
    cat > $out/lib/systemd/user/superstrike-apply.service <<UNIT
    [Unit]
    Description=Re-apply saved Logitech SUPERSTRIKE settings (superstrike-hits --apply)
    ConditionPathExists=%h/.config/superstrike-hits.autoapply
    ConditionPathExists=%h/.config/superstrike-hits.conf

    [Service]
    Type=oneshot
    ExecStartPre=${coreutils}/bin/sleep 3
    ExecStart=$out/bin/superstrike-hits --apply

    [Install]
    WantedBy=default.target
    UNIT
    sed -i 's/^    //' $out/lib/systemd/user/*.service

    makeWrapper ${pythonEnv}/bin/python3 $out/bin/superstrike-hits --add-flags $share/superstrike-hits.py
    makeWrapper ${pythonEnv}/bin/python3 $out/bin/superstrike-watch --add-flags $share/superstrike-watch.py
    makeWrapper ${pythonEnv}/bin/python3 $out/bin/superstrike-gui --add-flags $share/superstrike-gui.py \
      --prefix PATH : ${lib.makeBinPath [ libnotify ]} \
      "''${qtWrapperArgs[@]}"
    runHook postInstall
  '';

  meta = {
    description = "Actuation, haptics, rapid trigger, DPI and polling-rate control (CLI + GUI + tray) for the Logitech G PRO X2/X3 SUPERSTRIKE";
    homepage = "https://github.com/SHAHEII/Logitech-pro-X2-and-X3-control-gui";
    license = lib.licenses.mit;
    platforms = lib.platforms.linux;
    mainProgram = "superstrike-gui";
  };
}
