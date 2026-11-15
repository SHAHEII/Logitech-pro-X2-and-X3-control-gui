# NixOS module:  programs.superstrike.enable = true;
# `self` is the flake (flake.nix passes it); non-flake users import ./nixos.nix, which passes null.
self:
{ config, lib, pkgs, ... }:

let
  cfg = config.programs.superstrike;
in
{
  options.programs.superstrike = {
    enable = lib.mkEnableOption "SUPERSTRIKE Control (Logitech G PRO X2/X3 SUPERSTRIKE: CLI, GUI and tray)";

    package = lib.mkOption {
      type = lib.types.package;
      default =
        if self != null then self.packages.${pkgs.stdenv.hostPlatform.system}.default
        else pkgs.callPackage ./package.nix { };
      defaultText = lib.literalExpression "superstrike.packages.\${system}.default";
      description = "The superstrike package to use.";
    };

    udevRules = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = ''
        Install the udev rules that give the logged-in user access to the Logitech hidraw nodes
        (TAG+="uaccess", no root needed) and start superstrike-apply.service when the receiver is plugged in.
      '';
    };

    watcher = {
      enable = lib.mkOption {
        type = lib.types.bool;
        default = true;
        description = ''
          Install the superstrike-watch systemd user service, which re-applies your saved settings
          (host mode, DPI, polling rate, click settings) after the mouse sleeps/reconnects or the PC resumes.
          With autoStart = false, each user turns it on with the GUI toggle
          "Keep settings after sleep" (systemctl --user enable --now superstrike-watch.service).
        '';
      };
      autoStart = lib.mkOption {
        type = lib.types.bool;
        default = false;
        description = ''
          Start superstrike-watch for every user at graphical login (wantedBy graphical-session.target).
          It only runs once a saved config (~/.config/superstrike-hits.conf) exists. When true, the GUI
          toggle can't disable it; set this to false to manage it from the GUI instead.
        '';
      };
    };

    trayAtLogin = lib.mkOption {
      type = lib.types.bool;
      default = false;
      description = "Start superstrike-gui minimized to the system tray at login for every user (XDG autostart).";
    };
  };

  config = lib.mkIf cfg.enable {
    environment.systemPackages = [ cfg.package ];

    services.udev.packages = lib.mkIf cfg.udevRules [ cfg.package ];

    # ships superstrike-watch.service + superstrike-apply.service in /etc/systemd/user,
    # so `systemctl --user enable` (and the GUI toggle) work per user
    systemd.packages = lib.mkIf cfg.watcher.enable [ cfg.package ];
    systemd.user.services.superstrike-watch = lib.mkIf (cfg.watcher.enable && cfg.watcher.autoStart) {
      wantedBy = [ "graphical-session.target" ];
    };

    environment.etc."xdg/autostart/superstrike-gui-tray.desktop" = lib.mkIf cfg.trayAtLogin {
      text = ''
        [Desktop Entry]
        Type=Application
        Name=SUPERSTRIKE Control (tray)
        Exec=${cfg.package}/bin/superstrike-gui --tray
        Icon=superstrike-gui
        Terminal=false
        X-KDE-autostart-after=panel
      '';
    };
  };
}
