# Claude Logos Controller — LLM Session Contract

**Read this before doing any work in `agentix-logos` or `logos-workspace`.**

This document extends the Agentix [`docs/CLAUDE-CODE.md`](https://github.com/Beach-Bum/Agentix/blob/main/docs/CLAUDE-CODE.md) contract with Logos-specific primitives. The base contract (no `sudo`, no `rebuild-nixos`, no live-system mutation, source-workspace-untouched) carries through unchanged.

You are helping develop `agentix-logos` and operate `logos-workspace` safely. The substrate is Nix flakes; the runtime is `logoscore`; the apps are Logos modules and basecamp.

## Core safety rule

You must not directly activate or mutate:

- The live NixOS system (Agentix base rule)
- The user's primary `logos-basecamp` profile (`~/.local/share/logos-basecamp/`, `~/Library/Application Support/LogosBasecamp/`, etc.)
- Any submodule under `logos-workspace/repos/` — those are Logos's source of truth
- Any `flake.lock` in `logos-workspace/` outside of an Agentix worktree
- Any LEZ wallet, deployed program, or live localnet
- Any Logos delivery / chat / mix node connected to the testnet

You may inspect, plan, and use Agentix sandbox commands. The human controls all final apply, deploy, and rebuild actions.

## Forbidden commands

Never run these directly:

```
sudo
rebuild-nixos
nixos-rebuild switch
nix bundle ... --replace-existing-mod
nix-collect-garbage
rm -rf

# Logos-specific forbidden
ws build --output ~/.local/share/...     # writing build output to user state
lgs deploy                                # deploys a real LEZ program
lgs wallet topup                          # claims real testnet faucet
lgs localnet start                        # without --bind to a temp dir
lgs basecamp launch <profile>             # with the user's real profile
agentix apply-verify ...                  # human-only
```

Never directly edit:

- `/etc/nixos`
- `~/.ssh`
- `~/.config/logos-*` and equivalent platform paths
- `logos-workspace/repos/*/` (any submodule source)
- Any `flake.lock` outside the worktree
- Any wallet keystore (`.scaffold/state/wallet.state`, `~/.config/logos-execution-zone-wallet`)

## Allowed commands

You may run these in dry-run / inspection / sandbox mode:

```bash
# Agentix base — read-only
agentix doctor --path ~/projects/logos-workspace
agentix status --path ~/projects/logos-workspace
agentix controller-plan --path ~/projects/logos-workspace --json
agentix proposals list --path ~/projects/logos-workspace --json
agentix audit tail --path ~/projects/logos-workspace --json
agentix audit summary --path ~/projects/logos-workspace --json

# Agentix sandbox — never mutates source
agentix controller-run "<goal>" --path ~/projects/logos-workspace
agentix controller-run "<goal>" --path ~/projects/logos-workspace --execute
agentix worktree-run "<goal>" --path ~/projects/logos-workspace --json
agentix worktree-run "<goal>" --path ~/projects/logos-workspace --save-proposal --json
agentix agent-loop "<goal>" --path ~/projects/logos-workspace --dry-run

# agentix-logos — extends the above
agentix-logos verify-logoscore --workspace ~/projects/logos-workspace --modules <mod1,mod2> --call "<m.method()>" --json
agentix-logos workspace-status --path ~/projects/logos-workspace --json
agentix-logos modules list --path ~/projects/logos-workspace --json
agentix-logos modules describe <module> --path ~/projects/logos-workspace --json
agentix-logos policy-check --path ~/projects/logos-workspace --json

# Logos read-only inspection
ws list
ws status
ws dirty
ws graph <repo>
lm <module-path>
logoscore --version

# Logos read-only via scaffold (in a project dir)
lgs doctor --json
lgs basecamp doctor --json
lgs basecamp modules --show
lgs localnet status --json    # only against a sandbox localnet
```

## Required workflow for Logos goals

For any goal that touches `logos-workspace`:

1. **Read the contract.** Run `agentix controller-plan --path ~/projects/logos-workspace --json` and summarize:
   - Allowed commands
   - Forbidden commands
   - Source-workspace-untouched invariant
   - Audit log location

2. **Read Logos state.** Run:
   ```
   agentix-logos workspace-status --path ~/projects/logos-workspace --json
   agentix-logos modules list --path ~/projects/logos-workspace --json
   ```
   Summarize the relevant module versions, dirty submodules, and any captured `[modules.*]` state in `scaffold.toml`.

3. **Plan dry-run.** Run:
   ```
   agentix controller-run "<goal>" --path ~/projects/logos-workspace
   ```
   Summarize:
   - `would_run_worktree`
   - `would_save_proposal`
   - any forbidden command violations from `agentix-logos policy-check`

4. **Wait for explicit human approval** before executing.

5. **Execute in sandbox.** Run:
   ```
   agentix controller-run "<goal>" --path ~/projects/logos-workspace --execute
   ```

6. **Verify with logoscore.** The verify rung is:
   ```
   agentix-logos verify-logoscore \
       --worktree <temp-worktree-from-step-5> \
       --modules <modules-touched> \
       --call "<module>.healthcheck()" \
       --json
   ```

7. **Report.** Summarize:
   - `passed`
   - `source_modified`
   - `proposal_saved`
   - `modules_touched`
   - `logoscore_calls[].exit_code`
   - `nix_flake_lock_changed`
   - `stops_before_apply`
   - `stops_before_rebuild`

8. **Stop.** The human reviews `<workspace>/.agentix/proposals/<latest>.patch` and decides whether to apply.

## Logos-specific safety invariants

Beyond the Agentix base contract:

### S1. No writes outside the worktree

`ws build` and `nix build` outputs **must** go to either:
- `result/` symlinks inside the temp worktree, or
- A temp directory under `/tmp` that is cleaned up after the run

If a goal would write to `~/.local/share/logos-basecamp/`, `~/Library/Application Support/LogosBasecamp/`, or any user-state path, refuse and ask the human to amend the goal.

### S2. No live localnet

`logos-scaffold localnet start` without `--bind 127.0.0.1:<sandbox-port>` is forbidden — it conflicts with the user's real localnet. If a goal needs localnet, it runs **inside the worktree** with a sandbox port allocated by `agentix-logos workspace.allocate_sandbox_port()`.

### S3. No wallet operations

Any `lgs wallet topup`, `lgs deploy`, or wallet-state modification is forbidden in the controller path. These are human-only because they consume testnet faucet funds and produce on-chain side effects.

### S4. Submodule pointer integrity

If the goal causes a submodule SHA in `logos-workspace/repos/*` to drift, the snapshot must catch it. Treat any `repos/*` SHA change outside of an explicit `flake.lock` update as `source_workspace_mutated`.

### S5. Forbidden flake input overrides

Never run `--override-input` against:
- `nixpkgs` (system-wide ABI implications)
- `logos-blockchain` (consensus-affecting)
- Any flake input pinned by a LIP-stable spec

These overrides are human-only because they have ecosystem-wide side effects.

### S6. No LIP edits in the controller path

`logos-co/logos-lips` is canon. If a goal would modify or propose a LIP, the controller drafts the LIP in `<worktree>/proposed-lips/` as a new file and saves it as a proposal patch. The human opens the actual PR.

## Audit log expectations

Every controller-run / worktree-run produces one JSON line in `<workspace>/.agentix/audit.jsonl`. For Logos goals, the line includes the extended fields from [`docs/AUDIT-SCHEMA.md`](./AUDIT-SCHEMA.md):

- `agentix_logos_version`
- `logos_workspace_commit`
- `modules_touched: ["storage_module", "chat_module"]`
- `modules_versions: {"storage_module": "v0.3.2 → v0.3.3"}`
- `logoscore_calls: [{...}]`
- `nix_flake_lock_changed: bool`
- `lez_program_pin_changed: bool`
- `policy_violations: []`

If any of those fields are missing in your run output, you have an integration bug. Surface it to the human; do not retry blindly.

## Human-only steps

Only the human may run:

```bash
agentix apply-verify ...
ws build logos-basecamp                  # final pre-launch build
./result/bin/LogosBasecamp ...           # launching basecamp itself
lgs deploy
lgs wallet topup
git commit
git push
```

The human-only fence is the same as Agentix's base contract, extended with Logos's deploy / wallet / launch operations.

## First Claude session prompt

Use this prompt when starting a new session in this repo:

```text
Read docs/CLAUDE-LOGOS-CONTROLLER.md, docs/BRIDGE-SPEC.md, and the Agentix
docs/CLAUDE-CODE.md and docs/OPERATING.md.

You are helping develop agentix-logos and operate logos-workspace safely. The
Agentix safety contract carries through: no sudo, no rebuild-nixos, no
nixos-rebuild switch, no /etc/nixos mutation. Additionally: no writes to user
basecamp profiles, no lgs deploy or wallet operations, no live localnet, no
flake input overrides on nixpkgs or logos-blockchain.

For each Logos goal:
1. agentix controller-plan --path ~/projects/logos-workspace --json
2. agentix-logos workspace-status --path ~/projects/logos-workspace --json
3. agentix controller-run "<goal>" --path ~/projects/logos-workspace
4. Summarize and wait for human approval
5. agentix controller-run "<goal>" --path ~/projects/logos-workspace --execute
6. agentix-logos verify-logoscore --worktree <temp> --modules <...> --call "<...>" --json
7. Report passed / source_modified / proposal_saved / modules_touched /
   logoscore exit codes / nix_flake_lock_changed / stops_before_apply
8. Stop. The human applies.

Start by running agentix controller-plan and summarizing the contract.
```

## What this contract is not

- It is not a replacement for `docs/CLAUDE-CODE.md` — read both.
- It does not authorize the controller to make Logos governance decisions (LIPs, lez-multisig signatures, Assembly votes). Those are always human.
- It does not authorize publishing modules to the Logos ecosystem (`nix bundle` to a release artifact, GitHub release creation). Those are human-only.
- It does not give the controller authority to modify `logos-co/*` repos directly. All Logos-side work is via PRs from `agentix-logos`.

## Failure handling

If any of the following occurs, **stop and surface the JSON to the human**:

- `error="source_workspace_mutated"` — including if it's caused by submodule pointer drift
- `error="source_snapshot_failed"` — investigate before retrying
- `error="timeout"` — never just bump the timeout; understand why
- `policy_violations` is non-empty — explain which rule fired and why
- A logoscore call returned non-zero — the proposal must not save
- A submodule shows dirty state pre-run — clean it before snapshotting

Do not retry without human input. The whole point of the safety contract is to fail closed and ask for help.
