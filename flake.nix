{
  description = "Band (Thenvoi) platform adapter for the Hermes Agent gateway";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      # The plugin packaged as a Python package (format = pyproject).
      #
      # NOTE: `thenvoi-sdk` is the runtime dependency and is not (yet) in
      # nixpkgs — you must provide it. Override `propagatedBuildInputs` (or
      # supply it via an overlay) so the plugin can import it at runtime, e.g.:
      #
      #   self.packages.${system}.default.override {
      #     # ... wire in a thenvoi-sdk derivation built from PyPI ...
      #   }
      packages = forAll (pkgs:
        let
          py = pkgs.python311Packages;
        in
        {
          default = py.buildPythonPackage {
            pname = "hermes-band-platform";
            version = "1.0.0";
            format = "pyproject";
            src = ./.;
            nativeBuildInputs = [ py.setuptools ];
            # thenvoi-sdk goes here once packaged (see note above).
            propagatedBuildInputs = [ ];
            # Tests need the hermes-agent host on PYTHONPATH; skip in the build.
            doCheck = false;
            pythonImportsCheck = [ ];
            passthru.note = "Provide thenvoi-sdk (PyPI) as a runtime dep; enable as plugin \"band\".";
          };
        });
    };
}
