"""Multi-profile basecamp management.

A workspace may run multiple basecamp instances in parallel using
``--user-dir`` isolation. Each profile gets its own port range and
module directory to prevent collisions.

This module provides:

- :func:`list_profiles` — discover declared profiles from scaffold.toml.
- :func:`allocate_profile_ports` — assign non-overlapping port bases
  within the policy's ``sandbox_port_range``.
- :func:`validate_profile_isolation` — refuse if profiles share ports
  or module directories.

See ``docs/POLICY-SCHEMA.md`` § ``sandbox_port_range`` for the port
allocation contract.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib  # Python 3.11+
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore


# Default Logos basecamp port — must be avoided for sandboxed profiles.
LOGOS_DEFAULT_PORT = 3040

# Ports allocated per profile (base, base+1, ..., base+PORTS_PER_PROFILE-1).
PORTS_PER_PROFILE = 10


@dataclass
class ProfileRef:
    """A declared basecamp profile.

    Attributes:
        name: Profile identifier (used as --user-dir suffix).
        user_dir: Path to the profile's user directory, relative to workspace
            or absolute.
        module_dir: Path to the profile's module directory, if distinct from
            the default.
        port_base: Explicitly declared port base, or None for auto-allocation.
        raw: Raw TOML dict for the block.
    """

    name: str
    user_dir: str
    module_dir: str | None = None
    port_base: int | None = None
    raw: dict = dataclasses.field(default_factory=dict)

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON output."""
        return {
            "name": self.name,
            "user_dir": self.user_dir,
            "module_dir": self.module_dir,
            "port_base": self.port_base,
        }


@dataclass
class IsolationViolation:
    """A profile isolation check failure.

    Attributes:
        profiles: Names of the conflicting profiles.
        field: Which field conflicts (port_base, module_dir, etc.).
        details: Human-readable explanation.
    """

    profiles: list[str]
    field: str
    details: str

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON output."""
        return dataclasses.asdict(self)


def list_profiles(workspace: Path) -> list[ProfileRef]:
    """Discover declared profiles from ``scaffold.toml``.

    Profiles are declared under ``[profiles.<name>]`` blocks.

    Args:
        workspace: Path to the logos-workspace root.

    Returns:
        List of ProfileRef. Empty if scaffold.toml is missing or has
        no ``[profiles]`` section.

    Raises:
        ValueError: If the ``[profiles]`` block is structurally invalid.
    """
    scaffold_path = workspace / "scaffold.toml"
    if not scaffold_path.exists():
        return []

    with scaffold_path.open("rb") as f:
        data = tomllib.load(f)

    profiles_block = data.get("profiles")
    if profiles_block is None:
        return []
    if not isinstance(profiles_block, dict):
        raise ValueError(
            f"[profiles] must be a table; got {type(profiles_block).__name__}"
        )

    refs: list[ProfileRef] = []
    for name, body in profiles_block.items():
        if not isinstance(body, dict):
            continue
        user_dir = body.get("user_dir")
        if not isinstance(user_dir, str):
            raise ValueError(
                f"[profiles.{name}].user_dir must be a string; "
                f"got {type(user_dir).__name__}"
            )
        module_dir = body.get("module_dir")
        if module_dir is not None and not isinstance(module_dir, str):
            raise ValueError(
                f"[profiles.{name}].module_dir must be a string or absent; "
                f"got {type(module_dir).__name__}"
            )
        port_base = body.get("port_base")
        if port_base is not None and not isinstance(port_base, int):
            raise ValueError(
                f"[profiles.{name}].port_base must be an int or absent; "
                f"got {type(port_base).__name__}"
            )
        refs.append(
            ProfileRef(
                name=name,
                user_dir=user_dir,
                module_dir=module_dir,
                port_base=port_base,
                raw=body,
            )
        )
    return refs


def allocate_profile_ports(
    workspace: Path,
    profile_count: int,
    sandbox_port_range: tuple[int, int] = (13000, 14000),
) -> dict[str, int]:
    """Assign non-overlapping port bases for profiles.

    Each profile gets PORTS_PER_PROFILE consecutive ports starting at
    its allocated base. Allocation starts at sandbox_port_range[0] and
    increments by PORTS_PER_PROFILE for each profile.

    The Logos default port (3040) is never used.

    Args:
        workspace: Path to the workspace (used to read existing profiles).
        profile_count: Number of profiles to allocate ports for.
        sandbox_port_range: (low, high) from policy.sandbox_port_range.

    Returns:
        Dict mapping profile index (as string "profile_0", "profile_1", ...)
        to port base. If existing profiles in scaffold.toml have explicit
        port_base values, those are used and remaining slots are filled.

    Raises:
        ValueError: If the port range is too small to fit all profiles.
    """
    low, high = sandbox_port_range
    capacity = (high - low) // PORTS_PER_PROFILE
    if profile_count > capacity:
        raise ValueError(
            f"Cannot allocate {profile_count} profiles in port range "
            f"[{low}, {high}); max capacity is {capacity} "
            f"(each profile needs {PORTS_PER_PROFILE} ports)"
        )

    # Read existing profiles for explicit port_base declarations.
    existing = list_profiles(workspace)
    explicit: dict[str, int] = {}
    for prof in existing:
        if prof.port_base is not None:
            explicit[prof.name] = prof.port_base

    # Allocate remaining slots sequentially, skipping explicit ones.
    used_bases: set[int] = set(explicit.values())
    allocation: dict[str, int] = dict(explicit)

    next_base = low
    profiles_needed = profile_count - len(explicit)
    allocated = 0
    idx = 0

    while allocated < profiles_needed and next_base + PORTS_PER_PROFILE <= high:
        if next_base not in used_bases:
            name = f"profile_{len(explicit) + idx}"
            allocation[name] = next_base
            allocated += 1
            idx += 1
        next_base += PORTS_PER_PROFILE

    return allocation


def validate_profile_isolation(workspace: Path) -> list[IsolationViolation]:
    """Check that declared profiles don't share ports or module dirs.

    Args:
        workspace: Path to the logos-workspace root.

    Returns:
        List of IsolationViolation. Empty means profiles are properly isolated.
    """
    try:
        profiles = list_profiles(workspace)
    except ValueError as exc:
        return [
            IsolationViolation(
                profiles=[],
                field="profiles",
                details=f"[profiles] block is malformed: {exc}",
            )
        ]

    if len(profiles) < 2:
        return []

    violations: list[IsolationViolation] = []

    # Check port_base collisions. Two profiles with the same port_base
    # (or overlapping ranges) cannot coexist.
    port_map: dict[int, list[str]] = {}
    for prof in profiles:
        if prof.port_base is not None:
            # Check for overlap: any base within PORTS_PER_PROFILE of another
            for existing_base, existing_names in list(port_map.items()):
                if abs(prof.port_base - existing_base) < PORTS_PER_PROFILE:
                    violations.append(
                        IsolationViolation(
                            profiles=existing_names + [prof.name],
                            field="port_base",
                            details=(
                                f"Port ranges overlap: {existing_names[0]} "
                                f"(base={existing_base}) and {prof.name} "
                                f"(base={prof.port_base}) are within "
                                f"{PORTS_PER_PROFILE} ports of each other"
                            ),
                        )
                    )
            port_map.setdefault(prof.port_base, []).append(prof.name)

    # Check port_base == LOGOS_DEFAULT_PORT collision
    for prof in profiles:
        if prof.port_base is not None:
            if abs(prof.port_base - LOGOS_DEFAULT_PORT) < PORTS_PER_PROFILE:
                violations.append(
                    IsolationViolation(
                        profiles=[prof.name],
                        field="port_base",
                        details=(
                            f"Profile {prof.name} port_base={prof.port_base} "
                            f"conflicts with Logos default port {LOGOS_DEFAULT_PORT}"
                        ),
                    )
                )

    # Check module_dir collisions
    module_dir_map: dict[str, list[str]] = {}
    for prof in profiles:
        if prof.module_dir is not None:
            module_dir_map.setdefault(prof.module_dir, []).append(prof.name)
    for mdir, names in module_dir_map.items():
        if len(names) > 1:
            violations.append(
                IsolationViolation(
                    profiles=names,
                    field="module_dir",
                    details=(
                        f"Profiles {', '.join(names)} share module_dir={mdir!r}; "
                        f"each profile must have a distinct module directory"
                    ),
                )
            )

    # Check user_dir collisions
    user_dir_map: dict[str, list[str]] = {}
    for prof in profiles:
        user_dir_map.setdefault(prof.user_dir, []).append(prof.name)
    for udir, names in user_dir_map.items():
        if len(names) > 1:
            violations.append(
                IsolationViolation(
                    profiles=names,
                    field="user_dir",
                    details=(
                        f"Profiles {', '.join(names)} share user_dir={udir!r}; "
                        f"each profile must have a distinct user directory"
                    ),
                )
            )

    return violations
