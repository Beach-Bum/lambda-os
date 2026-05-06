# Agentix OS — Architecture Specification

This document is the source of truth for how Agentix operates the Logos technology stack. Agentix is the control plane — the management layer that handles the full lifecycle of Logos modules, nodes, and infrastructure with safety, auditability, and governance built in.

## Design principles

1. **Agentix operates Logos, not the other way around.** The relationship is control plane to substrate — like a hypervisor to a guest OS, or Kubernetes to containers. Agentix manages the lifecycle of everything running on Logos.
2. **Verify with Logos primitives.** Use `logoscore` / `logos_host` for runtime verification, not synthetic tests. The OS verifies its own components using its own runtime.
3. **Source integrity is non-negotiable.** Every operation snapshots the full workspace state (55+ submodules) before and after. Any mutation outside the sandbox = hard failure.
4. **Governance scales from one to many.** Human approval today. `lez-multisig` tomorrow. LEZ programs eventually. The safety contract doesn't weaken as governance decentralises — it strengthens.
5. **Audit everything, prove everything.** Every operation produces a JSONL event with full context. Phase 3 anchors these to Codex (tamper-evident) and LEZ (publicly verifiable).

## What Agentix manages

- **Module lifecycle**: install, upgrade, swap, remove, verify compatibility
- **Build orchestration**: Nix flake evaluation, `ws build`, override propagation across 52 repos
- **Policy enforcement**: RLN requirements, forbidden flake overrides, signed metadata, submodule drift
- **Verification**: sandboxed module loading via `logos_host` / `logoscore`, healthchecks, stdout hashing
- **Audit trail**: append-only JSONL with Logos extensions (modules_touched, versions, logoscore calls, safety attestations)
- **Proposal pipeline**: changes saved as patches, never activated without governance approval

## Non-goals (Phase 1)

- Decentralised audit log on Codex (Phase 3)
- Multisig governance for apply (Phase 3)
- Policy enforcement on LEZ (Phase 4)
- Self-hosting (Agentix configuring the Logos node it's running on) (Phase 4)

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│  GOVERNANCE                                                        │
│    Phase 1: human operator                                        │
│    Phase 3: lez-multisig (N-of-M cryptographic approval)          │
│    Phase 4: LEZ program (provable, on-chain policy enforcement)   │
├──────────────────────────────────────────────────────────────────┤
│  AGENTIX CONTROL PLANE  (this repo)                               │
│                                                                    │
│    ┌─────────────────────────────────────────────────────────┐   │
│    │ workspace.py   — state capture, snapshot, drift detect  │   │
│    │ verify.py      — sandbox verification (logoscore/host)  │   │
│    │ policy.py      — rule enforcement (RLN, overrides, etc) │   │
│    │ audit.py       — tamper-evident event log                │   │
│    │ modules.py     — module registry + metadata parsing      │   │
│    │ profiles.py    — multi-profile basecamp management       │   │
│    │ lez.py         — LEZ program pin tracking                │   │
│    │ mix.py         — Mix node configuration                  │   │
│    │ keys.py        — IFT key registry for signed metadata    │   │
│    └─────────────────────────────────────────────────────────┘   │
│                                                                    │
│    CLI: agentix-logos {snapshot, verify-logoscore, policy-check,  │
│         workspace-status, modules, audit}                         │
│                                                                    │
│    Control plane module: exposes API to Logos runtime via IPC     │
├──────────────────────────────────────────────────────────────────┤
│  LOGOS SUBSTRATE                                                   │
│    logoscore / logos_host — module runtime                         │
│    basecamp — desktop shell                                       │
│    52 repos as git submodules under logos-workspace/repos/        │
│    Nix flakes with `follows` for dependency propagation           │
│    Codex — content-addressed storage (Phase 3 audit target)       │
│    LEZ — execution zone (Phase 3 governance, Phase 4 policy)      │
│    Waku / Mix — messaging / privacy layer                         │
├──────────────────────────────────────────────────────────────────┤
│  NIX / NIXOS                                                       │
│    Reproducible builds, content-addressed, declarative config     │
└──────────────────────────────────────────────────────────────────┘
```

## Component spec

### `agentix_logos.workspace` — `logos-workspace` adapter

Wraps the `ws` CLI and Nix flake commands. Pure read/build operations only — no writes to source.

```python
class LogosWorkspace:
    def __init__(self, path: Path):
        self.path = path

    def status(self) -> dict:
        """ws status — list dirty submodules, current branches, dep graph."""

    def list_modules(self) -> list[ModuleRef]:
        """Parse logos-modules submodule list and scaffold.toml [modules.*]."""

    def build(self, target: str, *, override: dict[str, Path] | None = None,
              output_dir: Path | None = None, timeout: int = 1800) -> BuildResult:
        """nix build .#<target> with optional --override-input. Output goes to
        a temp dir under output_dir, never to the source workspace."""

    def graph(self, target: str) -> DepGraph:
        """ws graph <target> — dependency graph for a target repo."""

    def submodule_snapshot(self) -> dict[str, str]:
        """Return submodule path → commit SHA. Used by the extended source
        snapshot to detect submodule pointer drift."""

    def allocate_sandbox_port(self, base: int = 13000) -> int:
        """Find an unused port for sandbox localnet — never user's real ports."""
```

### `agentix_logos.modules` — Logos module registry parser

Parses module metadata into typed records.

```python
@dataclass
class ModuleRef:
    name: str               # e.g. "logos-storage-module"
    flake_ref: str          # e.g. "github:logos-co/logos-storage-module/v0.3.2"
    role: Literal["project", "dependency"]
    metadata: ModuleMetadata
    has_rln: bool           # True if module declares RLN spam protection
    main_artifact: Path     # path under result/lib

@dataclass
class ModuleMetadata:
    name: str
    main: str | dict[str, str]   # may be platform-keyed
    dependencies: list[str]
    capabilities: list[str]
    # …other metadata.json fields
```

### `agentix_logos.verify_logoscore` — logoscore verify rung

The integration point. Phase 1's most important code.

```python
@dataclass
class LogoscoreCall:
    module: str
    method: str
    args: list[str]
    exit_code: int
    stdout_sha256: str
    stderr_sha256: str
    duration_seconds: float

def verify_logoscore(
    worktree: Path,                       # MUST be a temp worktree, never source
    modules: list[str],
    calls: list[str],                     # e.g. ["storage_module.healthcheck()"]
    timeout: int = 120,
    modules_dir: Path | None = None,      # default: <worktree>/modules-built
) -> tuple[bool, list[LogoscoreCall]]:
    """
    1. Build modules into worktree's local result dir (never user's profile)
    2. Run `logoscore -m <built-modules-dir> -l <modules> -c <call>` per call
    3. Capture stdout/stderr, hash deterministically
    4. Return (all_passed, [LogoscoreCall, ...])
    """
```

**Critical invariants:**
- The build output directory **must** be inside the worktree, never `~/.local/share/...` or any user state path.
- The `logoscore` subprocess runs with `LOGOS_USER_DIR=<temp>` to fully isolate from the user's basecamp.
- A single call timeout is `timeout` seconds; the whole `verify_logoscore` invocation has a budget enforced by Agentix's `--timeout`.

### `agentix_logos.policy` — extends `agentix.policy`

Reads `policy.json`'s `logos:` block and emits violations.

```python
@dataclass
class LogosPolicy:
    require_rln_for_messaging_modules: bool = True
    require_metadata_json: bool = True
    require_signed_metadata: bool = False    # Phase 2 — needs IFT key registry
    forbid_flake_overrides: list[str] = field(
        default_factory=lambda: ["nixpkgs", "logos-blockchain"]
    )
    module_install_via: Literal["ws --auto-local", "manual"] = "ws --auto-local"
    forbidden_paths_for_writes: list[str] = field(
        default_factory=lambda: [
            "~/.local/share/logos-basecamp",
            "~/Library/Application Support/LogosBasecamp",
            "logos-workspace/repos/*",
        ]
    )
    sandbox_port_range: tuple[int, int] = (13000, 14000)

def check_logos_policy(
    workspace: Path,
    proposal_diff: str,
    modules_touched: list[ModuleRef],
) -> list[PolicyViolation]:
    """Return list of violations. Empty list = clean."""
```

### `agentix_logos.audit` — extended audit fields

Wraps `agentix.audit_log.audit` to enrich events with Logos data.

```python
def audit_logos_run(
    path: Path,
    base_event: dict,
    modules_touched: list[ModuleRef],
    modules_versions: dict[str, tuple[str, str]],   # module → (before, after)
    logoscore_calls: list[LogoscoreCall],
    nix_flake_lock_changed: bool,
    lez_program_pin_changed: bool,
    policy_violations: list[PolicyViolation],
    workspace_commit: str,
) -> None:
    """Append one enriched JSONL line to <path>/.agentix/audit.jsonl."""
```

See [`AUDIT-SCHEMA.md`](./AUDIT-SCHEMA.md) for the full field list.

## Source-workspace-untouched extension

Agentix's base snapshot covers HEAD, `git diff HEAD --`, and SHA-256 of every untracked file. For multi-submodule repos like `logos-workspace`, this is necessary but not sufficient.

### Submodule snapshot extension

Before each worktree run, also snapshot:

```python
{
    "repos/logos-storage-module": "abcd1234...",   # current submodule SHA
    "repos/logos-chat-module": "ef567890...",
    # ... for every submodule
}
```

After the run, compare. **Any submodule SHA change in source workspace → `error="source_workspace_mutated"`.**

The only allowed mutation remains: a single new patch under `.agentix/proposals/`. Everything else — including a submodule pointer drift — fails closed.

### Build artifact paths

`nix build` and `ws build` produce `result/` symlinks. To prevent these from being detected as untracked-file mutations:

- **In source workspace:** `result/` is gitignored upstream. Snapshot ignores `result*` symlinks under `repos/*/`.
- **In worktree:** all builds during a controller run target the worktree's `result/`, never the source's.

The `policy.json` file's `ignore_paths` list captures any additional paths that legitimately drift outside our control.

## Sandbox isolation

Beyond Agentix's git-worktree isolation, `agentix-logos` adds:

1. **`LOGOS_USER_DIR` redirection.** Every `logoscore`, `lgs basecamp launch`, and `nix bundle` invocation in the controller path runs with `LOGOS_USER_DIR=<worktree>/sandbox-user-dir` so it can't touch the user's real basecamp state.
2. **Network namespace where available.** On Linux, optional unshare-based net namespace for the inner subprocess (forbids Mix discovery against the real network during sandbox runs).
3. **Sandbox port allocation.** Any localnet started in the controller path uses a port in `policy.sandbox_port_range`, never the default 3040.

## Audit log file format

`.agentix/audit.jsonl` is append-only newline-delimited JSON. One line per controller-run / worktree-run. Schema in [`AUDIT-SCHEMA.md`](./AUDIT-SCHEMA.md). The Phase 3 migration to Codex preserves this format — each line becomes a Codex object addressed by SHA-256 of the line.

## Public release hygiene

`agentix-logos` inherits Agentix's `public-check` / `export-public` workflow. Run before any public push:

```bash
agentix public-check --path ~/projects/agentix-logos
agentix export-public --path ~/projects/agentix-logos --dest /tmp/agentix-logos-public --yes
```

This strips `MEMORY.md`, `.claude/`, `.agentix/audit.jsonl`, transcripts, editor temp files. Public docs are preserved.

## Phase boundaries

### Phase 1 (now): local demo
- Phase 1 of this spec is implemented
- All operations local
- Human applies on local machine
- Audit log local

### Phase 2 (months 2-3): module + breadth
- `agentix-logos-module` — Agentix exposed as a Logos module
- Three more demo goals (deploy LEZ program, configure mix nodes, multi-profile basecamp)
- Policy schema grows: `require_signed_metadata`, `lez_program_pinned`

### Phase 3 (months 4-6): decentralised
- Audit log → Codex (CID-addressed). See [`CODEX-AUDIT-ANCHOR.md`](./CODEX-AUDIT-ANCHOR.md) for the full design.
- Proposals → Codex
- Apply rung → `lez-multisig` signature threshold
- Discovery via Mix

### Phase 4 (months 6-12): self-hosting + LEZ policy
- Policy enforcement as a LEZ program (provable refusal)
- Agents-as-LEZ-programs (private execution)
- Self-hosting (Agentix configures the Logos node it's running on)

## Open questions

These are tracked here so they don't get lost:

1. **Submodule snapshot performance.** With ~43 submodules, the SHA snapshot adds latency. Benchmark: target <2s for full snapshot. If exceeded, parallelize.
2. **logoscore deterministic stdout.** Some healthchecks include timestamps. The verify rung needs a way to mask non-deterministic output. Proposed: `logoscore --deterministic-mode` flag PR'd upstream, or post-process stdout to strip timestamps before hashing.
3. **Cross-platform `LOGOS_USER_DIR`.** macOS uses `~/Library/Application Support`, Linux uses `~/.local/share`. Bridge needs platform detection.
4. **LEZ program ID resolution.** When a goal touches LEZ programs, the proposal includes a program ID hash. We need a deterministic way to compute this from the source. `lgs deploy --dry-run` is the candidate.
5. **Network policy enforcement.** S2 (no live localnet) and S6 (LIP edits) need teeth — currently policy is advisory-by-prompt. Phase 2 adds runtime checks.

These move into issues once the repo is past scaffolding.
