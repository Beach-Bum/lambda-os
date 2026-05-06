# Agentix OS — NixOS module for managing Logos nodes
#
# Add to your NixOS configuration:
#   imports = [ /path/to/agentix-logos/nixos/agentix-logos.nix ];
#   services.agentix-logos = {
#     enable = true;
#     workspacePath = "/home/ned/projects/logos-workspace";
#   };

{ config, lib, pkgs, ... }:

let
  cfg = config.services.agentix-logos;
in
{
  options.services.agentix-logos = {
    enable = lib.mkEnableOption "Agentix OS control plane for Logos";

    workspacePath = lib.mkOption {
      type = lib.types.str;
      description = "Path to logos-workspace checkout";
    };

    checkIntervalSec = lib.mkOption {
      type = lib.types.int;
      default = 300;
      description = "Seconds between daemon health check cycles";
    };

    user = lib.mkOption {
      type = lib.types.str;
      default = "ned";
      description = "User to run the daemon as (needs access to workspace)";
    };

    telegramToken = lib.mkOption {
      type = lib.types.str;
      default = "";
      description = "Telegram bot token for notifications (set via env or config)";
    };

    telegramChatId = lib.mkOption {
      type = lib.types.str;
      default = "";
      description = "Telegram chat ID for notifications";
    };

    desktopNotify = lib.mkOption {
      type = lib.types.bool;
      default = true;
      description = "Send desktop notifications via notify-send";
    };
  };

  config = lib.mkIf cfg.enable {
    systemd.services.agentix-daemon = {
      description = "Agentix OS — Logos node control plane";
      after = [ "network-online.target" ];
      wants = [ "network-online.target" ];
      wantedBy = [ "multi-user.target" ];

      environment = {
        LOGOS_WORKSPACE = cfg.workspacePath;
        AGENTIX_CHECK_INTERVAL = toString cfg.checkIntervalSec;
        AGENTIX_DESKTOP_NOTIFY = if cfg.desktopNotify then "1" else "0";
        # Telegram config via environment
        TELEGRAM_BOT_TOKEN = cfg.telegramToken;
        TELEGRAM_CHAT_ID = cfg.telegramChatId;
        # Display for desktop notifications
        DISPLAY = ":0";
        DBUS_SESSION_BUS_ADDRESS = "unix:path=/run/user/1000/bus";
        # Python/uv
        HOME = "/home/${cfg.user}";
        PATH = lib.makeBinPath [
          pkgs.git
          pkgs.python313
          "/home/${cfg.user}/.local/bin"
          "/run/current-system/sw/bin"
        ];
      };

      serviceConfig = {
        Type = "simple";
        User = cfg.user;
        ExecStart = "/home/${cfg.user}/.local/bin/agentix-daemon";
        Restart = "on-failure";
        RestartSec = 30;

        # Logging
        StandardOutput = "journal";
        StandardError = "journal";
        SyslogIdentifier = "agentix";

        # Security hardening
        ProtectSystem = "strict";
        ProtectHome = "read-only";
        ReadWritePaths = [
          "${cfg.workspacePath}/.agentix"
          "/tmp"
        ];
        PrivateTmp = true;
        NoNewPrivileges = true;
      };
    };

    # Timer for periodic git fetch (so upgrade detection sees latest remotes)
    systemd.services.agentix-fetch = {
      description = "Agentix — fetch latest remotes for upgrade detection";
      serviceConfig = {
        Type = "oneshot";
        User = cfg.user;
        ExecStart = "${pkgs.writeShellScript "agentix-fetch" ''
          cd ${cfg.workspacePath}
          ${pkgs.git}/bin/git submodule foreach --recursive 'git fetch origin --quiet 2>/dev/null || true'
        ''}";
      };
    };

    systemd.timers.agentix-fetch = {
      description = "Agentix — periodic remote fetch";
      wantedBy = [ "timers.target" ];
      timerConfig = {
        OnBootSec = "5min";
        OnUnitActiveSec = "1h";
        Unit = "agentix-fetch.service";
      };
    };
  };
}
