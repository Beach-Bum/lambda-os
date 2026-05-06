# Agentix Logos Node — NixOS module
#
# Defines a Logos node as a NixOS system service managed by Agentix.
# This is the OS: logoscore runs as a daemon, modules are loaded from
# Nix-built artifacts, Agentix monitors and manages the lifecycle.
#
# Usage in your NixOS configuration:
#   imports = [ ./nixos/logos-node.nix ];
#   services.logos-node = {
#     enable = true;
#     workspacePath = "/home/ned/projects/logos-workspace";
#     modules = [ "capability_module" "package_manager" "package_downloader" ];
#   };

{ config, lib, pkgs, ... }:

let
  cfg = config.services.logos-node;
  agentixCfg = config.services.agentix;
in
{
  options.services.logos-node = {
    enable = lib.mkEnableOption "Logos node managed by Agentix";

    workspacePath = lib.mkOption {
      type = lib.types.path;
      description = "Path to logos-workspace checkout";
    };

    resultPath = lib.mkOption {
      type = lib.types.path;
      default = "${cfg.workspacePath}/result";
      description = "Path to the built logos-basecamp result (contains bin/, lib/, modules/)";
    };

    modules = lib.mkOption {
      type = lib.types.listOf lib.types.str;
      default = [ "capability_module" ];
      description = "Logos modules to load at startup";
    };

    dataDir = lib.mkOption {
      type = lib.types.path;
      default = "/var/lib/logos-node";
      description = "Persistent data directory for the Logos node";
    };

    user = lib.mkOption {
      type = lib.types.str;
      default = "logos";
      description = "User to run the Logos node as";
    };

    group = lib.mkOption {
      type = lib.types.str;
      default = "logos";
      description = "Group to run the Logos node as";
    };
  };

  options.services.agentix = {
    enable = lib.mkEnableOption "Agentix control plane for the Logos node";

    checkIntervalSec = lib.mkOption {
      type = lib.types.int;
      default = 300;
      description = "Seconds between health check cycles";
    };

    policyPath = lib.mkOption {
      type = lib.types.path;
      default = "${cfg.workspacePath}/.agentix/policy.json";
      description = "Path to Agentix policy.json";
    };

    proposalsDir = lib.mkOption {
      type = lib.types.path;
      default = "${cfg.workspacePath}/.agentix/proposals";
      description = "Directory for saved proposals";
    };

    auditLog = lib.mkOption {
      type = lib.types.path;
      default = "${cfg.workspacePath}/.agentix/audit.jsonl";
      description = "Path to audit JSONL log";
    };
  };

  config = lib.mkIf cfg.enable {
    # Create the logos user and group
    users.users.${cfg.user} = lib.mkIf (cfg.user == "logos") {
      isSystemUser = true;
      group = cfg.group;
      home = cfg.dataDir;
      createHome = true;
    };
    users.groups.${cfg.group} = lib.mkIf (cfg.group == "logos") {};

    # Logos node: loads modules via logos_host
    systemd.services.logos-node = {
      description = "Logos Node — module runtime managed by Agentix";
      after = [ "network.target" ];
      wantedBy = [ "multi-user.target" ];

      environment = {
        LOGOS_USER_DIR = cfg.dataDir;
        QT_QPA_PLATFORM = "offscreen";
        LD_LIBRARY_PATH = "${cfg.resultPath}/lib";
      };

      serviceConfig = {
        Type = "simple";
        User = cfg.user;
        Group = cfg.group;
        StateDirectory = "logos-node";
        WorkingDirectory = cfg.dataDir;

        # Load the first module — logos_host is a single-module loader,
        # so we start one instance per module
        ExecStart = let
          moduleName = builtins.head cfg.modules;
          pluginPath = "${cfg.resultPath}/modules/${moduleName}/${moduleName}_plugin.so";
        in "${cfg.resultPath}/bin/logos_host --name ${moduleName} --path ${pluginPath}";

        Restart = "on-failure";
        RestartSec = 5;

        # Sandboxing
        ProtectSystem = "strict";
        ProtectHome = "read-only";
        ReadWritePaths = [ cfg.dataDir "${cfg.workspacePath}/.agentix" ];
        PrivateTmp = true;
        NoNewPrivileges = true;
      };
    };

    # Per-module services (one logos_host per module)
    systemd.services = builtins.listToAttrs (map (moduleName: {
      name = "logos-module-${moduleName}";
      value = {
        description = "Logos Module: ${moduleName}";
        after = [ "logos-node.service" ];
        wantedBy = [ "multi-user.target" ];
        partOf = [ "logos-node.service" ];

        environment = {
          LOGOS_USER_DIR = cfg.dataDir;
          QT_QPA_PLATFORM = "offscreen";
          LD_LIBRARY_PATH = "${cfg.resultPath}/lib";
        };

        serviceConfig = {
          Type = "simple";
          User = cfg.user;
          Group = cfg.group;
          ExecStart = "${cfg.resultPath}/bin/logos_host --name ${moduleName} --path ${cfg.resultPath}/modules/${moduleName}/${moduleName}_plugin.so";
          Restart = "on-failure";
          RestartSec = 5;
          ProtectSystem = "strict";
          ProtectHome = "read-only";
          ReadWritePaths = [ cfg.dataDir ];
          PrivateTmp = true;
          NoNewPrivileges = true;
        };
      };
    }) (builtins.tail cfg.modules));

    # Agentix control plane daemon
    systemd.services.agentix = lib.mkIf agentixCfg.enable {
      description = "Agentix Control Plane — manages the Logos node lifecycle";
      after = [ "logos-node.service" ];
      wantedBy = [ "multi-user.target" ];

      environment = {
        LOGOS_WORKSPACE = toString cfg.workspacePath;
        AGENTIX_POLICY = toString agentixCfg.policyPath;
        AGENTIX_AUDIT_LOG = toString agentixCfg.auditLog;
        AGENTIX_PROPOSALS_DIR = toString agentixCfg.proposalsDir;
        AGENTIX_CHECK_INTERVAL = toString agentixCfg.checkIntervalSec;
      };

      serviceConfig = {
        Type = "simple";
        ExecStart = "${pkgs.writeShellScript "agentix-daemon" ''
          ${agentixDaemonScript}
        ''}";
        Restart = "on-failure";
        RestartSec = 30;

        # Agentix needs read access to workspace, write to .agentix/
        ProtectSystem = "strict";
        ProtectHome = "read-only";
        ReadWritePaths = [
          "${cfg.workspacePath}/.agentix"
          cfg.dataDir
          "/tmp"
        ];
        PrivateTmp = true;
        NoNewPrivileges = true;
      };
    };

    # Agentix timer for periodic health checks (alternative to daemon loop)
    systemd.timers.agentix-healthcheck = lib.mkIf agentixCfg.enable {
      description = "Agentix periodic health check";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "60";
        OnUnitActiveSec = "${toString agentixCfg.checkIntervalSec}";
        Unit = "agentix-healthcheck.service";
      };
    };

    systemd.services.agentix-healthcheck = lib.mkIf agentixCfg.enable {
      description = "Agentix health check cycle";
      serviceConfig = {
        Type = "oneshot";
        ExecStart = "${pkgs.writeShellScript "agentix-healthcheck" ''
          set -euo pipefail
          export PATH="${pkgs.git}/bin:${pkgs.python313}/bin:$PATH"
          WORKSPACE="$LOGOS_WORKSPACE"

          echo "[$(date -Iseconds)] Agentix health check starting"

          # 1. Snapshot
          agentix-logos snapshot --path "$WORKSPACE" --json \
            > /tmp/agentix-snapshot-latest.json

          # 2. Verify each module loads
          for module_dir in ${cfg.resultPath}/modules/*/; do
            module=$(basename "$module_dir")
            echo "  Verifying: $module"
            agentix-logos verify-logoscore \
              --workspace "$WORKSPACE" \
              --modules "$module" \
              --call "$module.load()" \
              --modules-dir "${cfg.resultPath}/modules" \
              --backend logos_host \
              --timeout 10 \
              --json >> /tmp/agentix-verify-latest.json 2>&1 || true
          done

          # 3. Policy check
          agentix-logos policy-check --path "$WORKSPACE" --json \
            > /tmp/agentix-policy-latest.json

          echo "[$(date -Iseconds)] Agentix health check complete"
        ''}";

        Environment = [
          "LOGOS_WORKSPACE=${toString cfg.workspacePath}"
          "PATH=${pkgs.git}/bin:${pkgs.python313}/bin:/run/current-system/sw/bin"
        ];
      };
    };
  };
}
