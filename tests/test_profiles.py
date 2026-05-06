"""Tests for agentix_logos.profiles (P4 / PER-20)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from agentix_logos.profiles import (
    PORTS_PER_PROFILE,
    allocate_profile_ports,
    list_profiles,
    validate_profile_isolation,
)

# ───────────────────────────────────────────────────��─────────────────
# list_profiles
# ───────────────────────────��────────────────────────────────���────────


def test_list_profiles_valid(tmp_path: Path):
    """Well-formed [profiles.*] blocks parse into ProfileRef list."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text(
        textwrap.dedent(
            """
            [profiles.dev]
            user_dir = "sandbox/dev"
            module_dir = "sandbox/dev/modules"
            port_base = 13000

            [profiles.test]
            user_dir = "sandbox/test"
            port_base = 13010
            """
        )
    )
    profiles = list_profiles(ws)
    assert len(profiles) == 2
    assert profiles[0].name == "dev"
    assert profiles[0].user_dir == "sandbox/dev"
    assert profiles[0].module_dir == "sandbox/dev/modules"
    assert profiles[0].port_base == 13000
    assert profiles[1].name == "test"
    assert profiles[1].module_dir is None


def test_list_profiles_no_scaffold(tmp_path: Path):
    """Missing scaffold.toml returns empty list."""
    ws = tmp_path / "ws"
    ws.mkdir()
    assert list_profiles(ws) == []


def test_list_profiles_no_profiles_section(tmp_path: Path):
    """scaffold.toml without [profiles] returns empty list."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text("[modules.foo]\nflake = 'x'\n")
    assert list_profiles(ws) == []


def test_list_profiles_rejects_bad_user_dir(tmp_path: Path):
    """Non-string user_dir raises ValueError."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text(
        textwrap.dedent(
            """
            [profiles.bad]
            user_dir = 42
            """
        )
    )
    with pytest.raises(ValueError, match="user_dir must be a string"):
        list_profiles(ws)


# ─────────────────────────────────────────────────────────────────────
# allocate_profile_ports
# ──────────────────────────────��──────────────────────────────────────


def test_allocate_two_profiles(tmp_path: Path):
    """Two profiles get sequential non-overlapping port bases."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text("[modules.foo]\nflake = 'x'\n")
    alloc = allocate_profile_ports(ws, 2, sandbox_port_range=(13000, 14000))
    assert len(alloc) == 2
    bases = sorted(alloc.values())
    assert bases[0] == 13000
    assert bases[1] == 13010
    assert bases[1] - bases[0] >= PORTS_PER_PROFILE


def test_allocate_five_profiles(tmp_path: Path):
    """Five profiles fit within default range."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text("")
    alloc = allocate_profile_ports(ws, 5, sandbox_port_range=(13000, 14000))
    assert len(alloc) == 5
    bases = sorted(alloc.values())
    for i in range(1, len(bases)):
        assert bases[i] - bases[i - 1] >= PORTS_PER_PROFILE


def test_allocate_too_many_profiles(tmp_path: Path):
    """Port range too small raises ValueError."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text("")
    with pytest.raises(ValueError, match="Cannot allocate"):
        allocate_profile_ports(ws, 200, sandbox_port_range=(13000, 13050))


def test_allocate_respects_explicit_port_base(tmp_path: Path):
    """Profiles with explicit port_base are preserved in allocation."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text(
        textwrap.dedent(
            """
            [profiles.fixed]
            user_dir = "sandbox/fixed"
            port_base = 13050
            """
        )
    )
    alloc = allocate_profile_ports(ws, 3, sandbox_port_range=(13000, 14000))
    assert alloc["fixed"] == 13050
    # Other allocations should not overlap with 13050
    for name, base in alloc.items():
        if name != "fixed":
            assert abs(base - 13050) >= PORTS_PER_PROFILE


# ─────────────────────────────────────────────────────────────────────
# validate_profile_isolation
# ────────────────────────────���────────────────────────────────────────


def test_validate_isolated_profiles(tmp_path: Path):
    """Properly isolated profiles produce no violations."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text(
        textwrap.dedent(
            """
            [profiles.dev]
            user_dir = "sandbox/dev"
            module_dir = "sandbox/dev/modules"
            port_base = 13000

            [profiles.test]
            user_dir = "sandbox/test"
            module_dir = "sandbox/test/modules"
            port_base = 13010
            """
        )
    )
    assert validate_profile_isolation(ws) == []


def test_validate_port_collision(tmp_path: Path):
    """Two profiles with overlapping port ranges produce a violation."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text(
        textwrap.dedent(
            """
            [profiles.a]
            user_dir = "sandbox/a"
            port_base = 13000

            [profiles.b]
            user_dir = "sandbox/b"
            port_base = 13005
            """
        )
    )
    violations = validate_profile_isolation(ws)
    port_violations = [v for v in violations if v.field == "port_base"]
    assert len(port_violations) >= 1
    assert "overlap" in port_violations[0].details


def test_validate_module_dir_overlap(tmp_path: Path):
    """Two profiles sharing a module_dir produce a violation."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text(
        textwrap.dedent(
            """
            [profiles.a]
            user_dir = "sandbox/a"
            module_dir = "shared/modules"
            port_base = 13000

            [profiles.b]
            user_dir = "sandbox/b"
            module_dir = "shared/modules"
            port_base = 13010
            """
        )
    )
    violations = validate_profile_isolation(ws)
    mod_violations = [v for v in violations if v.field == "module_dir"]
    assert len(mod_violations) == 1
    assert "shared/modules" in mod_violations[0].details


def test_validate_default_port_collision(tmp_path: Path):
    """A profile with port_base near Logos default (3040) is flagged."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text(
        textwrap.dedent(
            """
            [profiles.danger]
            user_dir = "sandbox/danger"
            port_base = 3040

            [profiles.safe]
            user_dir = "sandbox/safe"
            port_base = 13000
            """
        )
    )
    violations = validate_profile_isolation(ws)
    default_violations = [
        v for v in violations if "3040" in v.details and v.field == "port_base"
    ]
    assert len(default_violations) >= 1


def test_validate_user_dir_overlap(tmp_path: Path):
    """Two profiles sharing a user_dir produce a violation."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text(
        textwrap.dedent(
            """
            [profiles.a]
            user_dir = "sandbox/shared"
            port_base = 13000

            [profiles.b]
            user_dir = "sandbox/shared"
            port_base = 13010
            """
        )
    )
    violations = validate_profile_isolation(ws)
    udir_violations = [v for v in violations if v.field == "user_dir"]
    assert len(udir_violations) == 1
    assert "sandbox/shared" in udir_violations[0].details


def test_validate_single_profile_no_violations(tmp_path: Path):
    """A single profile cannot conflict with itself."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "scaffold.toml").write_text(
        textwrap.dedent(
            """
            [profiles.solo]
            user_dir = "sandbox/solo"
            port_base = 13000
            """
        )
    )
    assert validate_profile_isolation(ws) == []
