{
  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";

    treefmt-nix = {
      url = "github:numtide/treefmt-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-nix = {
      url = "github:pyproject-nix/pyproject.nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    uv2nix = {
      url = "github:pyproject-nix/uv2nix";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };

    pyproject-build-systems = {
      url = "github:pyproject-nix/build-system-pkgs";
      inputs.pyproject-nix.follows = "pyproject-nix";
      inputs.uv2nix.follows = "uv2nix";
      inputs.nixpkgs.follows = "nixpkgs";
    };
  };

  outputs =
    {
      nixpkgs,
      treefmt-nix,
      pyproject-nix,
      uv2nix,
      pyproject-build-systems,
      ...
    }:
    let
      inherit (nixpkgs) lib;
      forAllSystems = lib.genAttrs lib.systems.flakeExposed;

      workspace = uv2nix.lib.workspace.loadWorkspace { workspaceRoot = ./.; };

      overlay = workspace.mkPyprojectOverlay { sourcePreference = "wheel"; };

      editableOverlay = workspace.mkEditablePyprojectOverlay { root = "$REPO_ROOT"; };

      pythonSets = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          python = pkgs.python3;
        in
        (pkgs.callPackage pyproject-nix.build.packages { inherit python; }).overrideScope (
          lib.composeManyExtensions [
            pyproject-build-systems.overlays.wheel
            overlay
          ]
        )
      );

      treefmt = forAllSystems (
        system:
        treefmt-nix.lib.evalModule nixpkgs.legacyPackages.${system} {
          projectRootFile = "flake.nix";

          settings = {
            allow-missing-formatter = true;
            verbose = 0;

            global.excludes = [ "*.lock" ];

            formatter.nixfmt.options = [ "--strict" ];
          };

          programs = {
            nixfmt.enable = true;
            ruff-format.enable = true;
          };
        }
      );

    in
    {
      formatter = forAllSystems (system: treefmt.${system}.config.build.wrapper);

      devShells = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          pythonSet = pythonSets.${system}.overrideScope editableOverlay;
          virtualenv = pythonSet.mkVirtualEnv "bizup-autocrawler-dev-env" workspace.deps.all;
        in
        {
          default = pkgs.mkShell {
            packages = [
              virtualenv
              pkgs.uv
              pkgs.deno # RLM default interpreter (Pyodide via Deno) for app/dev
              treefmt.${system}.config.build.wrapper
            ];
            env = {
              UV_NO_SYNC = "1";
              UV_PYTHON = pythonSet.python.interpreter;
              UV_PYTHON_DOWNLOADS = "never";
            };
            shellHook = ''
              unset PYTHONPATH
              export REPO_ROOT=$(git rev-parse --show-toplevel)
            '';
          };
        }
      );

      packages = forAllSystems (system: {
        default = pythonSets.${system}.mkVirtualEnv "bizup-autocrawler-env" workspace.deps.default;
      });

      checks = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          pythonSet = pythonSets.${system}.overrideScope editableOverlay;
          virtualenv = pythonSet.mkVirtualEnv "bizup-autocrawler-check-env" workspace.deps.all;
          # Only the inputs the checks need, so unrelated changes don't bust the cache.
          # Both run against the editable install via $REPO_ROOT, so src/tests/pyproject
          # is the whole closure (the §4.4 sandbox subprocesses inherit this env).
          checkSrc = lib.fileset.toSource {
            root = ./.;
            fileset = lib.fileset.unions [
              ./src
              ./tests
              ./pyproject.toml
            ];
          };
        in
        {
          ty = pkgs.runCommandLocal "bizup-autocrawler-ty-check" { } ''
            cp -r ${checkSrc} build
            chmod -R u+w build
            export HOME="$TMPDIR"
            export REPO_ROOT="$PWD/build"
            cd build
            ${virtualenv}/bin/ty check --python ${virtualenv}
            touch "$out"
          '';

          # Release gate (tests-ci): the full pytest suite runs as a flake check, so
          # `nix flake check` fails CI on a broken test or type error. The suite is
          # hermetic — the RLM interpreter is injected/mocked (no Deno) and nothing
          # touches the network — so it runs in the build sandbox. The §9 dspy tripwire
          # (test_dspy_pin) rides along here, making the version assumption a CI gate,
          # not just a runtime test. The §4.4 isolation subprocesses (sys.executable
          # `-m`) inherit REPO_ROOT, so the editable package resolves inside the child.
          pytest = pkgs.runCommandLocal "bizup-autocrawler-pytest" { } ''
            cp -r ${checkSrc} build
            chmod -R u+w build
            export HOME="$TMPDIR"
            export REPO_ROOT="$PWD/build"
            # The §4.4 isolation child eagerly builds an httpx.Client, whose SSL context
            # reads SSL_CERT_FILE; the hermetic sandbox ships no system CA bundle, so
            # point it at pkgs.cacert. No test touches the network (the suite is
            # interpreter-mocked and offline) — the bundle only lets Client() construct.
            export SSL_CERT_FILE="${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
            export NIX_SSL_CERT_FILE="$SSL_CERT_FILE"
            cd build
            ${virtualenv}/bin/pytest -q -p no:cacheprovider
            touch "$out"
          '';
        }
      );
    };
}
