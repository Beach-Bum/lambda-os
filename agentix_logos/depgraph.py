"""Dependency graph — topological upgrade ordering.

Reads the workspace's flake.nix to build a dependency graph, then
sorts upgrades so leaves are upgraded first, parents last. If a
leaf upgrade breaks verification, the parent upgrade is skipped.

This prevents cascading failures: upgrading logos-cpp-sdk (the root
of the tree) before its dependents are verified would break everything.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path


def _log(msg: str) -> None:
    from agentix_logos.daemon import _log as dlog
    dlog("INFO", f"[depgraph] {msg}")


def build_dep_graph(workspace: Path) -> dict[str, list[str]]:
    """Build a dependency graph from the workspace flake.nix.

    Returns a dict mapping each module to its list of dependencies
    (modules it depends on). Parsed from `inputs.<name>.follows`
    declarations in the workspace flake.
    """
    flake_path = workspace / "flake.nix"
    if not flake_path.exists():
        return {}

    content = flake_path.read_text()
    graph: dict[str, list[str]] = defaultdict(list)

    # Parse input declarations and their follows
    # Pattern: logos-chat-module = { ... inputs.logos-cpp-sdk.follows = "logos-cpp-sdk"; ... }
    current_input = None
    for line in content.splitlines():
        # Detect input block start
        input_match = re.match(r'\s*([\w-]+)\s*=\s*\{', line)
        if input_match:
            current_input = input_match.group(1)
            if current_input not in graph:
                graph[current_input] = []

        # Detect follows declaration (dependency)
        follows_match = re.match(r'\s*inputs\.([\w-]+)\.follows\s*=\s*"([\w-]+)"', line)
        if follows_match and current_input:
            dep_name = follows_match.group(2)
            if dep_name not in ("nixpkgs", "logos-nix"):  # Skip infra deps
                graph[current_input].append(dep_name)

    return dict(graph)


def topological_sort(graph: dict[str, list[str]]) -> list[list[str]]:
    """Sort modules into levels for safe upgrade ordering.

    Level 0 = leaves (no dependencies), Level 1 = depends only on
    Level 0, etc. Modules within the same level can be upgraded
    in parallel.
    """
    if not graph:
        return []

    # Compute in-degree (how many things depend on this module)
    all_nodes = set(graph.keys())
    for deps in graph.values():
        all_nodes.update(deps)

    # Reverse graph: for each module, which modules depend on it?
    dependents: dict[str, set[str]] = defaultdict(set)
    for module, deps in graph.items():
        for dep in deps:
            dependents[dep].add(module)

    # Kahn's algorithm for topological sort by levels
    in_degree: dict[str, int] = {node: 0 for node in all_nodes}
    for module, deps in graph.items():
        in_degree[module] = len([d for d in deps if d in all_nodes])

    levels: list[list[str]] = []
    remaining = set(all_nodes)

    while remaining:
        # Find all nodes with in-degree 0 (leaves at this level)
        level = [n for n in remaining if in_degree.get(n, 0) == 0]
        if not level:
            # Cycle detected — break it by picking the node with lowest in-degree
            level = [min(remaining, key=lambda n: in_degree.get(n, 0))]

        levels.append(sorted(level))

        # Remove this level and update in-degrees
        for node in level:
            remaining.discard(node)
            for dependent in dependents.get(node, set()):
                if dependent in remaining:
                    in_degree[dependent] = max(0, in_degree[dependent] - 1)

    return levels


def order_upgrades(
    workspace: Path,
    upgrades: list[dict],
) -> list[list[dict]]:
    """Order upgrades by dependency level.

    Returns a list of levels. Each level is a list of upgrade dicts
    that can be safely applied together. Level 0 (leaves) first.
    """
    graph = build_dep_graph(workspace)
    levels = topological_sort(graph)

    if not levels:
        # No graph available — return all upgrades as a single level
        return [upgrades] if upgrades else []

    # Map module names to their upgrade dicts
    upgrade_by_module: dict[str, dict] = {}
    for u in upgrades:
        upgrade_by_module[u["module"]] = u

    # Assign upgrades to levels
    ordered: list[list[dict]] = []
    assigned = set()
    for level in levels:
        level_upgrades = []
        for module in level:
            if module in upgrade_by_module and module not in assigned:
                level_upgrades.append(upgrade_by_module[module])
                assigned.add(module)
        if level_upgrades:
            ordered.append(level_upgrades)

    # Any upgrades not in the graph go in the last level
    unassigned = [u for u in upgrades if u["module"] not in assigned]
    if unassigned:
        ordered.append(unassigned)

    return ordered


def print_upgrade_plan(ordered_levels: list[list[dict]]) -> str:
    """Format the upgrade plan as a readable string."""
    lines = ["UPGRADE PLAN (dependency-ordered)", "=" * 40]

    for i, level in enumerate(ordered_levels):
        lines.append(f"\nLevel {i} ({'leaves' if i == 0 else 'depends on level ' + str(i-1)}):")
        for u in level:
            lines.append(f"  {u['module']:45s} {u['current'][:8]} -> {u['available'][:8]}")

    lines.append(f"\nTotal: {sum(len(lv) for lv in ordered_levels)} upgrades in {len(ordered_levels)} levels")
    return "\n".join(lines)
