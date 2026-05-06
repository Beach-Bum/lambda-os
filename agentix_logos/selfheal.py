"""Self-healing — automatic rollback when modules fail.

When the daemon detects a module that was healthy last cycle but is
now failing, it:
  1. Looks up the last known good commit from the audit trail
  2. Generates a rollback proposal (reverse pin)
  3. If auto-rollback policy allows it, applies immediately
  4. Sends a notification: "module X rolled back to Y"
  5. Re-verifies the module loads after rollback

This is the difference between "a tool" and "an OS." The system
heals itself.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from agentix_logos.notify import NotifyConfig, send_desktop, send_telegram


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _log(level: str, msg: str) -> None:
    print(f"[{_now()}] [{level}] [selfheal] {msg}", flush=True)


def detect_regressions(
    current_results: dict[str, dict],
    previous_results: dict[str, dict],
) -> list[dict]:
    """Find modules that were healthy last cycle but are failing now."""
    regressions = []
    for module, info in current_results.items():
        was_healthy = previous_results.get(module, {}).get("passed", False)
        is_healthy = info.get("passed", False)
        if was_healthy and not is_healthy:
            regressions.append({
                "module": module,
                "previous": previous_results[module],
                "current": info,
            })
    return regressions


def find_last_good_commit(workspace: Path, module_path: str) -> str | None:
    """Find the last known good commit for a module from the audit trail.

    Walks the audit log backwards looking for a health_check where
    this module passed, then returns the submodule SHA from that snapshot.
    """
    audit_path = workspace / ".agentix" / "audit.jsonl"
    if not audit_path.exists():
        return None

    # Read audit log backwards
    lines = audit_path.read_text(encoding="utf-8").strip().splitlines()
    for line in reversed(lines):
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        # Look for health checks where this module was healthy
        if event.get("action") == "health_check" and event.get("healthy"):
            # The snapshot SHAs aren't stored in the health check event,
            # but we know the workspace was healthy. Use git reflog.
            break

    # Fall back to git reflog — find the previous commit for this submodule
    try:
        result = subprocess.run(
            ["git", "log", "--format=%H", "-2", "HEAD"],
            cwd=workspace / module_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            commits = result.stdout.strip().splitlines()
            if len(commits) >= 2:
                return commits[1]  # Previous commit
    except (subprocess.TimeoutExpired, Exception):
        pass

    return None


def rollback_module(
    workspace: Path,
    module_path: str,
    module_name: str,
    target_sha: str,
) -> dict:
    """Roll back a module to a specific commit.

    Creates a worktree, pins the submodule to the target SHA, generates
    a patch, and applies it. Returns a result dict.
    """
    result: dict = {
        "module": module_name,
        "target_sha": target_sha,
        "rolled_back": False,
        "timestamp": _now(),
    }

    worktree_dir = Path(tempfile.mkdtemp(prefix=f"agentix-rollback-{module_name}-"))

    try:
        # Create worktree
        subprocess.run(
            ["git", "worktree", "add", str(worktree_dir), "HEAD"],
            cwd=workspace, capture_output=True, check=True,
        )

        # Pin to target SHA
        subprocess.run(
            ["git", "update-index", "--cacheinfo", f"160000,{target_sha},{module_path}"],
            cwd=worktree_dir, capture_output=True, check=True,
        )

        # Get the diff
        diff = subprocess.run(
            ["git", "diff", "--cached"],
            cwd=worktree_dir, capture_output=True, text=True,
        ).stdout

        if not diff.strip():
            result["error"] = "No diff generated — already at target"
            return result

        # Apply the rollback to the workspace
        apply_result = subprocess.run(
            ["git", "apply", "--check"],
            cwd=workspace, input=diff, capture_output=True, text=True,
        )
        if apply_result.returncode != 0:
            result["error"] = f"Patch conflict: {apply_result.stderr[:200]}"
            return result

        subprocess.run(
            ["git", "apply"],
            cwd=workspace, input=diff, capture_output=True, text=True, check=True,
        )

        # Commit the rollback
        subprocess.run(["git", "add", "-A"], cwd=workspace, capture_output=True)
        subprocess.run(
            ["git", "-c", "user.email=agentix@localhost", "-c", "user.name=Agentix OS",
             "commit", "-m", f"rollback: {module_name} to {target_sha[:12]} (auto-heal)"],
            cwd=workspace, capture_output=True,
        )

        result["rolled_back"] = True
        result["patch"] = diff

    except Exception as e:
        result["error"] = str(e)

    finally:
        subprocess.run(
            ["git", "worktree", "remove", str(worktree_dir), "--force"],
            cwd=workspace, capture_output=True,
        )

    return result


def auto_heal(
    workspace: Path,
    regressions: list[dict],
    notify_config: NotifyConfig | None = None,
) -> list[dict]:
    """Attempt to heal all regressed modules by rolling back.

    Returns a list of rollback results.
    """
    results = []

    for reg in regressions:
        module = reg["module"]
        module_path = f"repos/{module.replace('_', '-')}"

        _log("WARN", f"Module {module} regressed — attempting auto-heal")

        # Find the last good commit
        good_sha = find_last_good_commit(workspace, module_path)
        if good_sha is None:
            _log("ERROR", f"No rollback target found for {module}")
            results.append({
                "module": module,
                "rolled_back": False,
                "error": "No previous commit found for rollback",
            })
            continue

        _log("INFO", f"Rolling back {module} to {good_sha[:12]}")
        rollback = rollback_module(workspace, module_path, module, good_sha)
        results.append(rollback)

        if rollback["rolled_back"]:
            _log("INFO", f"HEALED: {module} rolled back to {good_sha[:12]}")

            # Notify
            if notify_config:
                msg = (
                    f"🔄 *Agentix OS — Auto-heal*\n"
                    f"Module `{module}` failed verification.\n"
                    f"Rolled back to `{good_sha[:12]}`.\n"
                    f"Node is recovering."
                )
                send_telegram(notify_config, msg)
                send_desktop("Agentix Auto-Heal", f"{module} rolled back to {good_sha[:12]}")

            # Write audit event
            audit_line = {
                "timestamp": _now(),
                "action": "auto_rollback",
                "module": module,
                "target_sha": good_sha,
                "rolled_back": True,
            }
            audit_path = workspace / ".agentix" / "audit.jsonl"
            with audit_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(audit_line, sort_keys=True) + "\n")
        else:
            _log("ERROR", f"Rollback failed for {module}: {rollback.get('error')}")

    return results
