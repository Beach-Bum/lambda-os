"""Notification system for Agentix OS.

Sends alerts via Telegram and desktop notifications when the daemon
detects events worth knowing about: upgrades available, proposals
pending, modules failing, policy violations.
"""

from __future__ import annotations

import os
import subprocess
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass
class NotifyConfig:
    telegram_token: str | None = None
    telegram_chat_id: str | None = None
    desktop: bool = True

    @classmethod
    def from_env(cls) -> NotifyConfig:
        return cls(
            telegram_token=os.environ.get("AGENTIX_TELEGRAM_TOKEN")
                or os.environ.get("TELEGRAM_BOT_TOKEN"),
            telegram_chat_id=os.environ.get("AGENTIX_TELEGRAM_CHAT_ID")
                or os.environ.get("TELEGRAM_CHAT_ID"),
            desktop=os.environ.get("AGENTIX_DESKTOP_NOTIFY", "1") != "0",
        )


def send_telegram(config: NotifyConfig, message: str) -> bool:
    """Send a message via Telegram bot API."""
    if not config.telegram_token or not config.telegram_chat_id:
        return False

    url = f"https://api.telegram.org/bot{config.telegram_token}/sendMessage"
    data = urllib.parse.urlencode({
        "chat_id": config.telegram_chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": "true",
    }).encode()

    try:
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status == 200
    except Exception:
        return False


def send_desktop(title: str, body: str, urgency: str = "normal") -> bool:
    """Send a desktop notification via notify-send."""
    try:
        subprocess.run(
            ["notify-send", f"--urgency={urgency}", title, body],
            capture_output=True,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def notify_health_check(config: NotifyConfig, report: dict) -> None:
    """Send notification after a health check cycle."""
    healthy = report.get("healthy", False)
    cycle = report.get("cycle", 0)
    modules = report.get("checks", {}).get("modules", {})
    upgrades = report.get("checks", {}).get("upgrades", [])
    proposals = report.get("checks", {}).get("proposals_generated", [])
    pending = report.get("checks", {}).get("governance_pending", 0)

    healthy_count = sum(1 for m in modules.values() if m.get("passed"))
    total_count = len(modules)

    # Only notify on interesting events
    if healthy and not proposals and not upgrades:
        return  # Boring cycle, skip

    status = "HEALTHY" if healthy else "DEGRADED"
    icon = "✅" if healthy else "⚠️"

    lines = [f"{icon} *Agentix OS — Cycle {cycle}*", f"Status: {status}"]

    if modules:
        lines.append(f"Modules: {healthy_count}/{total_count} healthy")

    if upgrades:
        lines.append(f"Upgrades: {len(upgrades)} available")

    if proposals:
        new_pending = sum(1 for p in proposals if p.get("state") == "pending")
        failed = sum(1 for p in proposals if p.get("state") == "failed")
        if new_pending:
            lines.append(f"New proposals: {new_pending} pending approval")
        if failed:
            lines.append(f"Failed proposals: {failed}")

    if pending:
        lines.append(f"Governance queue: {pending} awaiting approval")

    msg = "\n".join(lines)

    if config.telegram_token:
        send_telegram(config, msg)

    if config.desktop:
        send_desktop(
            f"Agentix OS — {status}",
            f"Cycle {cycle}: {healthy_count}/{total_count} modules, {len(upgrades)} upgrades",
            urgency="critical" if not healthy else "normal",
        )


def notify_proposal_event(config: NotifyConfig, proposal_id: str, action: str, module: str) -> None:
    """Notify on governance events (approve/reject)."""
    icons = {"approved": "✅", "rejected": "❌", "applied": "🚀"}
    icon = icons.get(action, "📋")
    msg = f"{icon} *Proposal {action}*\nModule: `{module}`\nID: `{proposal_id}`"

    if config.telegram_token:
        send_telegram(config, msg)
    if config.desktop:
        send_desktop(f"Proposal {action}", f"{module}: {proposal_id}")


def notify_degraded(config: NotifyConfig, failed_modules: list[str]) -> None:
    """Alert when node becomes degraded."""
    msg = (
        f"🔴 *Agentix OS — NODE DEGRADED*\n"
        f"Failed modules: {', '.join(failed_modules)}\n"
        f"Run: `agentix-logos governance status --path <ws>`"
    )
    if config.telegram_token:
        send_telegram(config, msg)
    if config.desktop:
        send_desktop(
            "Agentix OS — DEGRADED",
            f"Failed: {', '.join(failed_modules)}",
            urgency="critical",
        )
