{
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      pkgs = nixpkgs.legacyPackages.x86_64-linux;
      pythonEnv = pkgs.python3.withPackages (ps: with ps; [
        anytree
        backoff
        cloudpickle
        flask
        gdown
        graphviz
        ipdb
        matplotlib
        numpy
        opencv4
        openai
        pandas
        pillow
        plotly
        psutil
        pyyaml
        requests
        scipy
        seaborn
        tqdm
        transformers
        werkzeug
      ]);
    in
    {
      devShells.x86_64-linux.default = pkgs.mkShell.override {
        stdenv = pkgs.clangStdenv;
      } {
        buildInputs = with pkgs; [
          git
          just
          nodejs_22
          pythonEnv
          unzip
          uv
        ];
        LD_LIBRARY_PATH = pkgs.lib.makeLibraryPath (with pkgs; [
          libxkbcommon
          libX11
          libxcb
          libXcursor
          libXext
          libXi
          libXrandr
          libXinerama
          libxkbcommon
          libuuid
        ]);
      };
    };
}
