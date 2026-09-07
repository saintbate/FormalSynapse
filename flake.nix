{
  description = "FormalSynapse — open-source formal sign-off gate (Yosys + sby)";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-25.05";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "x86_64-darwin"
        "aarch64-darwin"
      ];
      forAll = nixpkgs.lib.genAttrs systems;
    in
    {
      packages = forAll (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python311;
          formalsynapse = python.pkgs.buildPythonPackage {
            pname = "formalsynapse";
            version = "0.1.0";
            src = self;
            pyproject = true;
            build-system = [ python.pkgs.hatchling ];
            doCheck = false;
          };
        in
        {
          inherit formalsynapse;
          default = formalsynapse;
        }
      );

      apps = forAll (system: {
        default = {
          type = "app";
          program = "${self.packages.${system}.formalsynapse}/bin/fsyn";
        };
      });

      devShells = forAll (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
        in
        {
          default = pkgs.mkShell {
            packages = [
              pkgs.python311
              pkgs.uv
              pkgs.yosys
              pkgs.symbiyosys
              pkgs.z3
              pkgs.verilator
            ];
            shellHook = ''
              echo "FormalSynapse: nixpkgs yosys/sby/z3 for BMC."
              echo "Full OSS CAD Suite (yosys-slang) still comes from scripts/install_toolchain.sh."
              export FSYN_TOOLCHAIN_DIR="''${FSYN_TOOLCHAIN_DIR:-$HOME/.local/opt/oss-cad-suite}"
              export PATH="$FSYN_TOOLCHAIN_DIR/bin:$PATH"
            '';
          };
        }
      );
    };
}
