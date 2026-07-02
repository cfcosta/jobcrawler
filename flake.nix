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
          virtualenv = pythonSet.mkVirtualEnv "jobcrawler-dev-env" workspace.deps.all;
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

      packages = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          # The runtime virtualenv: `${appEnv}/bin/jobcrawl` is the §10 console script
          # (jobcrawler.cli:main), with the full pinned dependency closure.
          appEnv = pythonSets.${system}.mkVirtualEnv "jobcrawler-env" workspace.deps.default;
        in
        {
          default = appEnv;
        }
        # Container image only makes sense for Linux targets (it ships a Linux rootfs);
        # gating keeps `nix flake check` eval clean on darwin.
        // lib.optionalAttrs (lib.hasSuffix "-linux" system) {
          # `nix build .#dockerImage` → a loadable layered image (no Docker daemon needed
          # to build). Reuses the exact flake closure, so the image can never drift from
          # uv.lock the way a hand-rolled `pip install` Dockerfile would. Bundles Deno for
          # the §4.2 generation sandbox (dspy's Pyodide interpreter shells out to `deno`);
          # the run/crawl daemon itself never invokes Deno — only `generate`/`optimize` do.
          dockerImage = pkgs.dockerTools.buildLayeredImage {
            name = "jobcrawler";
            tag = "latest";
            contents = [
              appEnv # jobcrawl console script + Python runtime closure
              pkgs.deno # §4.2 RLM Pyodide sandbox (generate/optimize only)
              pkgs.cacert # TLS roots for httpx (crawl/board fetch + the §4.6 match LM + generate/optimize LM API)
              pkgs.coreutils # a minimal shell toolbox for debugging / exec
              pkgs.bashInteractive
            ];
            config = {
              # The primary deployment entrypoint: the self-scheduling daemon (§4.9).
              # Override with `generate`/`crawl`/`status` for one-shot/CI invocations.
              Cmd = [
                "jobcrawl"
                "run"
              ];
              WorkingDir = "/data";
              Env = [
                "PATH=/bin"
                "SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
                "NIX_SSL_CERT_FILE=${pkgs.cacert}/etc/ssl/certs/ca-bundle.crt"
                # Persist the Deno/Pyodide cache on the data volume so generation cold
                # starts don't re-download the WASM runtime every container restart.
                "DENO_DIR=/data/deno-cache"
                # Default the registry, match profile, and state onto the mounted /data
                # volume: an operator drops sites.yaml and PROFILE.md there, and the sqlite
                # store — run history plus the §4.7 positions ledger and the §4.6 match
                # cache/spend — persists alongside them. PROFILE.md is the §4.6 matcher's
                # sole input (an operator-authored persistent file, like sites.yaml); the
                # bare "PROFILE.md" default would resolve under WorkingDir anyway, but pin it
                # explicitly like the others so a stray cd can't relocate it.
                "JOBCRAWLER_SITES=/data/sites.yaml"
                "JOBCRAWLER_PROFILE=/data/PROFILE.md"
                "JOBCRAWLER_STATE_URL=sqlite:////data/jobcrawler.db"
                # The committed crawlers (and the §14.2 _pending staging dir) MUST live on
                # the writable /data volume so generate/promote/self-heal persist them: the
                # in-image package dir is part of the ephemeral container layer (lost on
                # restart / --rm), and is read-only on a native Nix-store deploy. /data is
                # both writable and durable.
                "JOBCRAWLER_CRAWLERS_DIR=/data/crawlers"
              ];
              # Readiness/config check (§10): `status` loads the registry + opens the state
              # store and renders the M4 surface — it catches a missing/broken sites.yaml or
              # an unreadable DB. It does NOT prove daemon liveness (that awaits the spec's
              # future HTTP health surface); it is a config/readiness probe, not a heartbeat.
              Healthcheck = {
                Test = [
                  "CMD"
                  "jobcrawl"
                  "status"
                ];
                Interval = 60000000000; # 60s (ns)
                Timeout = 10000000000; # 10s
                StartPeriod = 5000000000; # 5s
                Retries = 3;
              };
            };
          };
        }
      );

      checks = forAllSystems (
        system:
        let
          pkgs = nixpkgs.legacyPackages.${system};
          pythonSet = pythonSets.${system}.overrideScope editableOverlay;
          virtualenv = pythonSet.mkVirtualEnv "jobcrawler-check-env" workspace.deps.all;
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
          ty = pkgs.runCommandLocal "jobcrawler-ty-check" { } ''
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
          pytest = pkgs.runCommandLocal "jobcrawler-pytest" { } ''
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
