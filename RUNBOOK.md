# agentix-logos Runbook — Day 1 to Demo

This runbook takes you from zero to a working end-to-end demo: **Agentix safely operating a Logos module change**, with audit trail, on your local machine.

The demo is the only thing that matters in Phase 1. Everything else (decentralised audit on Codex, multisig governance, LEZ policy enforcement) is mechanical substitution once this works.

## Prerequisites

- macOS or Linux with [Nix](https://nixos.org/download.html) installed and flakes enabled (`experimental-features = nix-command flakes` in `~/.config/nix/nix.conf`)
- `git`, `gh` CLI authenticated, Python 3.12+, [`uv`](https://docs.astral.sh/uv/) installed
- ~20 GB disk for the Logos workspace clone + Nix store
- A working LLM controller (Claude Code, Cursor, etc.) reading [`docs/CLAUDE-LOGOS-CONTROLLER.md`](./docs/CLAUDE-LOGOS-CONTROLLER.md)

If `~/projects/agentix` doesn't exist, clone it first:

```bash
mkdir -p ~/projects
cd ~/projects
git clone git@github.com:Beach-Bum/Agentix.git agentix
cd agentix
uv tool install --editable . --reinstall
which agentix    # should print ~/.local/bin/agentix or similar
agentix --help
```

Set the path the rest of this runbook uses:

```bash
export AGENTIX_LOGOS_HOME=~/projects/agentix-logos
export LOGOS_WORKSPACE_HOME=~/projects/logos-workspace
```

---

## Day 1 — Get Logos building locally

**Goal:** confirm the substrate is healthy. If basecamp doesn't launch, no amount of Agentix work will demo anything.

```bash
cd ~/projects
git clone --recurse-submodules git@github.com:logos-co/logos-workspace.git
cd $LOGOS_WORKSPACE_HOME
export PATH="$PWD/scripts:$PATH"

# Sanity checks — should print without errors
ws develop
ws list

# The canonical "does the world work" smoke test (slow first time, ~20-40 min)
ws build logos-basecamp

# Launch in an isolated profile so it doesn't touch your existing basecamp state
./repos/logos-basecamp/result/bin/LogosBasecamp --user-dir /tmp/basecamp-day1
```

### Expected
- `ws list` lists ~43 repos with clone status `OK`.
- `ws build logos-basecamp` ends with a `result/` symlink under `repos/logos-basecamp/`.
- LogosBasecamp opens with Chat/Wallet/Storage panels visible.

### Failure modes
- **Submodule init fails:** rerun `./scripts/ws init`. Check SSH key access to `git@github.com:logos-co/*`.
- **`nix build` errors with `error: experimental Nix feature 'nix-command' is disabled`:** flakes aren't enabled. Add `experimental-features = nix-command flakes` to `~/.config/nix/nix.conf`.
- **macOS sandbox / xcrun errors:** install Xcode Command Line Tools (`xcode-select --install`).
- **`ws develop` complains about Qt:** run `ws check-qt logos-basecamp` and follow the suggested override.

**Stop and fix Day 1 before moving on.** A broken Logos build is the single most common reason Phase 1 stalls.

---

## Day 2 — Get Agentix running against logos-workspace

**Goal:** confirm Agentix can read logos-workspace state without mutating it, and the policy schema is in place.

```bash
cd ~/projects/agentix
uv run agentix --help

# Smoke checks against logos-workspace
agentix doctor --path $LOGOS_WORKSPACE_HOME
agentix status --path $LOGOS_WORKSPACE_HOME
agentix controller-plan --path $LOGOS_WORKSPACE_HOME --json | jq .
```

`doctor` will yell about missing `.agentix/policy.json`. Add a minimal one:

```bash
mkdir -p $LOGOS_WORKSPACE_HOME/.agentix
cp $AGENTIX_LOGOS_HOME/examples/policy.json $LOGOS_WORKSPACE_HOME/.agentix/policy.json
```

Make sure `.agentix/` is gitignored in logos-workspace (it likely already is — confirm):

```bash
grep -E '^\.agentix' $LOGOS_WORKSPACE_HOME/.gitignore || \
    echo -e "\n# agentix-logos local state\n.agentix/" >> $LOGOS_WORKSPACE_HOME/.gitignore
```

### Expected
- `agentix doctor` reports green on flake/git checks.
- `controller-plan --json` returns `allowed_commands`, `forbidden_commands`, `safety_boundaries`.
- `audit.jsonl` does **not** yet exist (no run has happened).

### Failure modes
- **`agentix: command not found`:** see Prerequisites — `uv tool install --editable .` and ensure `~/.local/bin` is on PATH.
- **`source_snapshot_failed` on submodule status:** Day 3 will exercise this; it's the riskiest assumption to validate. If it fails today, file the first integration bug.

---

## Day 3 — First no-op worktree run against Logos

**Goal:** validate Agentix's source-untouched invariant on a multi-submodule git repo. logos-workspace has ~43 submodules — this is the riskiest assumption of the whole integration.

```bash
agentix worktree-run "no-op: read logos-workspace state and report" \
    --path $LOGOS_WORKSPACE_HOME \
    --save-proposal --json | jq .
```

Then read the audit log:

```bash
agentix audit tail --path $LOGOS_WORKSPACE_HOME --lines 5 --json | jq .
agentix audit summary --path $LOGOS_WORKSPACE_HOME --json | jq .
```

### Expected
- One JSON line in `audit.jsonl` with `action="worktree_run"`, `result="ok"`, `source_modified=false`, `proposal_saved=true|false` depending on whether the no-op produced any diff.
- No mutations under `repos/*` in logos-workspace.

### Failure modes — and what to do
- **`error="source_workspace_mutated"` triggered by submodule pointer drift:** This is bug #1. The Agentix snapshot is HEAD-based; submodule SHA changes on `nix build` artifact resolution can register as a tracked-file mutation. Fix in `agentix_logos/workspace.py`: pre-snapshot, run `git submodule status --recursive` and snapshot **submodule SHAs separately** from tracked files. See `docs/BRIDGE-SPEC.md § Submodule snapshot extension`.
- **`error="source_snapshot_failed"`:** likely a submodule with detached HEAD or git hooks. Check `git submodule foreach 'git status --porcelain'` for any dirty submodule.

If snapshot drift is unavoidable for some submodules (e.g. Nix-managed result symlinks), update the policy file's `ignore_paths` list. **Do not** disable the snapshot invariant.

---

## Day 4 — Wire `logoscore` into the verify rung

**Goal:** Agentix's verify step calls `logoscore` from inside the worktree, and the proposal only saves if `logoscore` returns 0.

This is the single highest-leverage piece of Phase 1 — it's the moment Agentix gets eyes inside Logos.

```bash
cd $AGENTIX_LOGOS_HOME
uv venv
uv pip install -e .

# Smoke test: call logoscore from within an Agentix-style sandbox
uv run agentix-logos verify-logoscore \
    --workspace $LOGOS_WORKSPACE_HOME \
    --modules storage_module \
    --call "storage_module.healthcheck()" \
    --timeout 120 \
    --json | jq .
```

Expected JSON:

```json
{
  "status": "ok",
  "modules": ["storage_module"],
  "calls": [{
    "module": "storage_module",
    "method": "healthcheck",
    "args": [],
    "exit_code": 0,
    "stdout_sha256": "..."
  }],
  "duration_seconds": 4.7
}
```

### What this does under the hood

1. Resolve the modules using `logos-workspace`'s `ws build --auto-local` (or `nix build .#lib`) — produces the `.lgx` artifacts in a temp build output, **not** in the source workspace.
2. Run `logoscore -m <built-modules-dir> -l <modules> -c "<call>"` with a 120s timeout.
3. Hash the stdout (deterministic across runs for healthchecks; meaningful diff if drift).
4. Return JSON; never write to source.

### Failure modes
- **`logoscore: command not found`:** add `$LOGOS_WORKSPACE_HOME/scripts` to PATH (per the workspace README), or use the absolute path.
- **`Module not found` from logoscore:** the `.lgx` file isn't where logoscore expects. Check `metadata.json`'s `main` field; the build output structure should be `result/lib/<module>.lgx`.
- **Timeout:** healthcheck shouldn't take >5s. If it does, the module is doing real work in `init` — split it out or extend the timeout.

---

## Day 5 — End-to-end: smallest real change

**Goal:** the demo command works for a trivial real change.

Pick the smallest possible change. Recommended for Day 5: **pin `logos-storage-module` to a specific commit and verify it loads.**

```bash
# Inside ~/projects/agentix (not agentix-logos)
agentix controller-run "pin logos-storage-module to commit abc1234 in logos-workspace and verify it loads" \
    --path $LOGOS_WORKSPACE_HOME \
    --module auto

# Review the dry-run JSON. If it looks right:
agentix controller-run "pin logos-storage-module to commit abc1234 in logos-workspace and verify it loads" \
    --path $LOGOS_WORKSPACE_HOME \
    --module auto \
    --execute
```

(Replace `abc1234` with an actual short SHA from `cd $LOGOS_WORKSPACE_HOME/repos/logos-storage-module && git log --oneline -10`.)

### Expected
- A new patch under `$LOGOS_WORKSPACE_HOME/.agentix/proposals/2026-MM-DD-storage-pin.patch`.
- The audit line shows `proposal_saved=true`, `source_modified=false`, the new logos-specific fields (`modules_touched`, `logoscore_calls`).
- The patch contains `flake.lock` changes for `logos-storage-module` and possibly `scaffold.toml` `[modules.storage_module]` updates. Nothing else.

### Apply (human-only)

```bash
cd $LOGOS_WORKSPACE_HOME
agentix apply-verify --proposal .agentix/proposals/2026-MM-DD-storage-pin.patch
ws build logos-basecamp
./repos/logos-basecamp/result/bin/LogosBasecamp --user-dir /tmp/basecamp-day5
```

If basecamp launches and storage works on the new pin, **Phase 1 is unblocked**. Record a 90-second screen capture of the controller-run + apply + basecamp launch. That's the demo.

---

## Weeks 2-3 — harden into demoable

Once Day 5 works once, three more goals to exercise the breadth of the substrate:

1. **Module install** — add `logos-irc-module` to a basecamp profile. Goal file: `examples/goal-install-irc.md`.
2. **Module upgrade** — bump pinned ref of `logos-storage-module`, run logoscore healthcheck. Goal file: `examples/goal-upgrade-storage.md`.
3. **Module swap** — replace `logos-chat-legacy-module` with `logos-chat-module`, verify intro-bundle flow. Goal file: `examples/goal-swap-chat.md`.

Polish work in parallel:
- Extend audit log with the schema in [`docs/AUDIT-SCHEMA.md`](./docs/AUDIT-SCHEMA.md).
- Implement the `logos:` policy block enforcement (see [`docs/POLICY-SCHEMA.md`](./docs/POLICY-SCHEMA.md)). Smoke test with a deliberately-bad proposal that violates `require_rln_for_messaging_modules`.
- Update `docs/CLAUDE-LOGOS-CONTROLLER.md` with any new commands / patterns discovered during Days 1-5.

End state: a 90-second screen capture demonstrating Agentix proposing, verifying, and human-applying a Logos module change with full audit. **That's the demo.**

---

## Week 4 — land in the Logos ecosystem

When the demo works:

1. Open an issue in [`logos-co/ideas`](https://github.com/logos-co/ideas) titled "Agentix: agent control layer for Logos module operations" linking to the demo recording and `docs/ARCHITECTURE.md`.
2. PR a tutorial to [`logos-co/logos-docs`](https://github.com/logos-co/logos-docs) showing how to use `agentix-logos` to install a module.
3. Draft a LIP against [`logos-co/logos-lips`](https://github.com/logos-co/logos-lips) describing the audit schema + proposal format.
4. Apply for an `rfp` grant — [`logos-co/rfp`](https://github.com/logos-co/rfp) exists for exactly this kind of ecosystem work.

---

## Troubleshooting reference

| Symptom | Likely cause | Fix |
|---|---|---|
| `error="source_workspace_mutated"` | Submodule pointer drift, build artifact in untracked area | Snapshot submodule SHAs separately (`docs/BRIDGE-SPEC.md`); add path to `policy.json` `ignore_paths` |
| `error="source_snapshot_failed"` | Dirty submodule, detached HEAD, git hooks | `git submodule foreach 'git status --porcelain'`; clean dirty submodules |
| `error="timeout"` | logoscore hung, build took >1800s | Increase `--timeout`; investigate why init is slow |
| `agentix-logos: command not found` | venv not activated, or not pip-installed | `cd $AGENTIX_LOGOS_HOME && uv pip install -e .` |
| `logoscore: command not found` | scripts/ not on PATH | `export PATH="$LOGOS_WORKSPACE_HOME/scripts:$PATH"` |
| basecamp launches but module missing | `.lgx` not in profile's modules dir | `ws --auto-local` didn't pick up override; check `flake.lock` |
| Proposal patch is empty | Goal didn't actually mutate anything | Inspect with `--keep` and look in the temp worktree |

---

## What "done" looks like

You've completed Phase 1 when you can run, on a clean machine:

```bash
agentix controller-run "swap logos-chat-legacy-module for logos-chat-module in profile alice" \
    --path ~/projects/logos-workspace \
    --execute
```

…and end up with: one proposal patch, one audit line, source workspace bit-identical to before, basecamp launching with the new module after a human apply. That's the substrate match working. From there: every other piece (Codex audit, multisig apply, LEZ policy) is iteration on a working core.
