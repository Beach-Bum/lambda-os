"""Health history dashboard — node status over time.

Shows when the node was healthy, when it degraded, what upgrades
were applied, what rolled back. Like htop for your OS governance.

CLI: agentix-logos dashboard --path <workspace>
"""

from __future__ import annotations

import json
from pathlib import Path


def load_health_history(workspace: Path) -> list[dict]:
    """Load all audit events from the workspace."""
    audit_path = workspace / ".agentix" / "audit.jsonl"
    if not audit_path.exists():
        return []

    events = []
    for line in audit_path.read_text(encoding="utf-8").strip().splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def compute_uptime(events: list[dict]) -> dict:
    """Compute node uptime statistics from health check events."""
    health_checks = [e for e in events if e.get("action") == "health_check"]
    if not health_checks:
        return {"total_checks": 0, "healthy": 0, "degraded": 0, "uptime_pct": 0}

    healthy = sum(1 for e in health_checks if e.get("healthy"))
    total = len(health_checks)
    return {
        "total_checks": total,
        "healthy": healthy,
        "degraded": total - healthy,
        "uptime_pct": round(healthy / total * 100, 1) if total > 0 else 0,
    }


def get_proposal_stats(events: list[dict]) -> dict:
    """Count proposal events by type."""
    proposals = [e for e in events if e.get("action") == "proposal_generated"]
    rollbacks = [e for e in events if e.get("action") == "auto_rollback"]
    approvals = [e for e in events if e.get("action") == "governance_approve"]
    rejections = [e for e in events if e.get("action") == "governance_reject"]

    return {
        "generated": len(proposals),
        "rollbacks": len(rollbacks),
        "approvals": len(approvals),
        "rejections": len(rejections),
    }


def get_recent_timeline(events: list[dict], limit: int = 20) -> list[dict]:
    """Get the most recent events as a timeline."""
    return events[-limit:]


def format_dashboard(workspace: Path) -> dict:
    """Build the full dashboard data structure."""
    events = load_health_history(workspace)
    uptime = compute_uptime(events)
    proposals = get_proposal_stats(events)
    timeline = get_recent_timeline(events)

    # Current node status
    status_path = workspace / ".agentix" / "node-status.json"
    current_status = {}
    if status_path.exists():
        try:
            current_status = json.loads(status_path.read_text())
        except json.JSONDecodeError:
            pass

    # Proposals on disk
    proposals_dir = workspace / ".agentix" / "proposals"
    on_disk: dict[str, int] = {}
    if proposals_dir.exists():
        for f in proposals_dir.glob("*.json"):
            try:
                state = json.loads(f.read_text()).get("state", "unknown")
                on_disk[state] = on_disk.get(state, 0) + 1
            except json.JSONDecodeError:
                continue

    return {
        "node": {
            "healthy": current_status.get("healthy"),
            "last_check": current_status.get("timestamp"),
            "cycle": current_status.get("cycle"),
        },
        "uptime": uptime,
        "proposals": {
            "audit_trail": proposals,
            "on_disk": on_disk,
        },
        "timeline": timeline,
        "total_events": len(events),
    }


def print_dashboard(data: dict) -> str:
    """Format the dashboard as a human-readable string."""
    lines = []
    lines.append("AGENTIX OS — NODE DASHBOARD")
    lines.append("=" * 50)

    # Current status
    node = data.get("node", {})
    healthy = node.get("healthy")
    status = "HEALTHY" if healthy else ("DEGRADED" if healthy is False else "UNKNOWN")
    icon = "🟢" if healthy else ("🔴" if healthy is False else "⚪")
    lines.append(f"\n{icon} Status: {status}")
    if node.get("last_check"):
        lines.append(f"   Last check: {node['last_check'][:19]}")
    if node.get("cycle"):
        lines.append(f"   Cycle: {node['cycle']}")

    # Uptime
    up = data.get("uptime", {})
    if up.get("total_checks", 0) > 0:
        lines.append(f"\n📊 Uptime: {up['uptime_pct']}%")
        lines.append(f"   {up['healthy']} healthy / {up['degraded']} degraded / {up['total_checks']} total checks")

    # Proposals
    props = data.get("proposals", {})
    on_disk = props.get("on_disk", {})
    if on_disk:
        lines.append("\n📋 Proposals:")
        for state, count in sorted(on_disk.items()):
            icons = {"pending": "⏳", "approved": "✅", "applied": "🚀", "rejected": "❌", "failed": "💥"}
            lines.append(f"   {icons.get(state, '  ')} {state}: {count}")

    trail = props.get("audit_trail", {})
    if trail.get("rollbacks", 0) > 0:
        lines.append(f"\n🔄 Auto-rollbacks: {trail['rollbacks']}")

    # Recent timeline
    timeline = data.get("timeline", [])
    if timeline:
        lines.append(f"\n📜 Recent events ({len(timeline)}):")
        for event in timeline[-10:]:
            ts = event.get("timestamp", "")[:19]
            action = event.get("action", "?")
            extra = ""
            if action == "health_check":
                h = "✓" if event.get("healthy") else "✗"
                extra = f" [{h}]"
            elif action == "proposal_generated":
                extra = f" {event.get('module', '')}"
            elif action == "auto_rollback":
                extra = f" {event.get('module', '')} -> {event.get('target_sha', '')[:8]}"
            lines.append(f"   {ts}  {action}{extra}")

    lines.append(f"\n   Total audit events: {data.get('total_events', 0)}")
    return "\n".join(lines)
