{
  description = "agentix-logos-module: Logos module exposing Agentix safety primitives";

  inputs = {
    logos-nix.url = "github:logos-co/logos-nix";
    nixpkgs.follows = "logos-nix/nixpkgs";
    logos-cpp-sdk.url = "github:logos-co/logos-cpp-sdk";
    logos-module.url = "github:logos-co/logos-module";
  };

  outputs = { self, nixpkgs, logos-nix, logos-cpp-sdk, logos-module }:
    let
      systems = [ "aarch64-darwin" "x86_64-darwin" "aarch64-linux" "x86_64-linux" ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f {
        pkgs = import nixpkgs { inherit system; };
        logosSdk = logos-cpp-sdk.packages.${system}.default;
        logosModule = logos-module.packages.${system}.default;
      });
    in
    {
      packages = forAllSystems ({ pkgs, logosSdk, logosModule }:
        let
          pname = "agentix-logos-module";
          version = "0.1.0";
          src = ./.;

          meta = with pkgs.lib; {
            description = "Agentix safety primitives as a native Logos module";
            homepage = "https://github.com/Beach-Bum/agentix-logos";
            license = licenses.mit;
            platforms = platforms.unix;
          };

          lib = pkgs.stdenv.mkDerivation {
            pname = "${pname}-lib";
            inherit version src meta;

            preConfigure = ''
              cd cpp
            '';

            nativeBuildInputs = [
              pkgs.cmake
              pkgs.ninja
              pkgs.pkg-config
              pkgs.qt6.wrapQtAppsNoGuiHook
            ];

            buildInputs = [
              pkgs.qt6.qtbase
              pkgs.qt6.qtremoteobjects
            ];

            cmakeFlags = [
              "-GNinja"
              "-DLOGOS_CPP_SDK_ROOT=${logosSdk}"
              "-DLOGOS_MODULE_ROOT=${logosModule}"
            ];

            installPhase = ''
              runHook preInstall
              mkdir -p $out/lib
              if [ -f modules/agentix_logos_module_plugin.so ]; then
                cp modules/agentix_logos_module_plugin.so $out/lib/
              elif [ -f modules/agentix_logos_module_plugin.dylib ]; then
                cp modules/agentix_logos_module_plugin.dylib $out/lib/
              else
                echo "Error: plugin library not found in build output"
                ls -la modules/ || true
                exit 1
              fi
              cp ${src}/metadata.json $out/metadata.json
              runHook postInstall
            '';
          };
        in
        {
          agentix-logos-module-lib = lib;
          inherit lib;
          default = lib;
        }
      );

      devShells = forAllSystems ({ pkgs, logosSdk, logosModule }: {
        default = pkgs.mkShell {
          nativeBuildInputs = [
            pkgs.cmake
            pkgs.ninja
            pkgs.pkg-config
          ];
          buildInputs = [
            pkgs.qt6.qtbase
            pkgs.qt6.qtremoteobjects
          ];
          shellHook = ''
            export LOGOS_CPP_SDK_ROOT="${logosSdk}"
            export LOGOS_MODULE_ROOT="${logosModule}"
            echo "agentix-logos-module dev shell"
            echo "SDK: $LOGOS_CPP_SDK_ROOT"
            echo "Module: $LOGOS_MODULE_ROOT"
          '';
        };
      });
    };
}
