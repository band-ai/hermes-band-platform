{
  description = "Band platform adapter for the Hermes Agent gateway";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      # The plugin packaged as a Python package (format = pyproject).
      packages = forAll (pkgs:
        let
          py = pkgs.python311Packages;
          defaultBandSdk =
            if py ? "band-sdk" then py."band-sdk" else
            throw ''
              band-sdk is not available in this nixpkgs Python package set.
              Provide it explicitly:

                self.packages.${pkgs.system}.default.override {
                  bandSdk = <your band-sdk Python derivation>;
                }

              Build band-sdk from PyPI, poetry2nix, pip2nix, or an overlay, then
              pass that derivation here so hermes_band_platform can import band
              at runtime.
            '';
          plugin = py.callPackage (
            { buildPythonPackage, setuptools, bandSdk ? defaultBandSdk }:
            buildPythonPackage {
              pname = "hermes-band-platform";
              version = "1.0.0"; # x-release-please-version
              format = "pyproject";
              src = ./.;
              nativeBuildInputs = [ setuptools ];
              propagatedBuildInputs = [ bandSdk ];
              # Tests need the hermes-agent host on PYTHONPATH; skip in the build.
              doCheck = false;
              pythonImportsCheck = [ "hermes_band_platform" ];
              passthru.note = "Enable as Hermes plugin \"band\" after adding to the gateway Python environment.";
            }
          ) { };
        in
        {
          default = plugin;
        });
    };
}
