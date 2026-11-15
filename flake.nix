{
  description = "SUPERSTRIKE Control: Linux CLI + GUI + tray for the Logitech G PRO X2/X3 SUPERSTRIKE (HITS, DPI, polling rate)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs: rec {
        superstrike = pkgs.callPackage ./nix/package.nix { };
        default = superstrike;
      });

      apps = forAll (pkgs: {
        default = { type = "app"; program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.default}/bin/superstrike-gui"; };
        cli = { type = "app"; program = "${self.packages.${pkgs.stdenv.hostPlatform.system}.default}/bin/superstrike-hits"; };
      });

      nixosModules.default = import ./nix/module.nix self;
      nixosModules.superstrike = self.nixosModules.default;

      overlays.default = final: prev: { superstrike = final.callPackage ./nix/package.nix { }; };

      checks = forAll (pkgs:
        let
          py = pkgs.python3.withPackages (ps: [ ps.pyside6 ps.pytest ]);
        in
        {
          package = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
          # the mock-mouse test-suite (backend, watcher, GUI offscreen, tray, installer)
          tests = pkgs.runCommand "superstrike-tests"
            { nativeBuildInputs = [ py pkgs.bash pkgs.desktop-file-utils ]; QT_QPA_PLATFORM = "offscreen"; }
            ''
              mkdir src && cp -r --no-preserve=mode ${./.}/. src/ && cd src && chmod +x install.sh
              export HOME=$TMPDIR/home TEST_HOME=$TMPDIR/home XDG_RUNTIME_DIR=$TMPDIR/run
              mkdir -p $HOME $XDG_RUNTIME_DIR
              patchShebangs install.sh
              python -m pytest -q -p no:cacheprovider tests
              touch $out
            '';
          # evaluates the non-flake entry point (nix/nixos.nix) with defaults
          module-nonflake = (nixpkgs.lib.nixosSystem {
            system = pkgs.stdenv.hostPlatform.system;
            modules = [
              ./nix/nixos.nix
              {
                programs.superstrike.enable = true;
                boot.loader.grub.enable = false;
                fileSystems."/" = { device = "none"; fsType = "tmpfs"; };
                system.stateVersion = "24.11";
              }
            ];
          }).config.system.build.etc;
          # evaluates the NixOS module
          module = (nixpkgs.lib.nixosSystem {
            system = pkgs.stdenv.hostPlatform.system;
            modules = [
              self.nixosModules.default
              {
                programs.superstrike = { enable = true; watcher.autoStart = true; trayAtLogin = true; };
                boot.loader.grub.enable = false;
                fileSystems."/" = { device = "none"; fsType = "tmpfs"; };
                system.stateVersion = "24.11";
              }
            ];
          }).config.system.build.etc;
        });
    };
}
