# Goal — Upgrade logos-storage-module

A reference goal exercising **module upgrade** through the controller. Easier than install (the module is already captured) and a good Day 5 first-real-change candidate.

## Goal text

```
Upgrade logos-storage-module from its current pin to the latest stable github
tag in logos-workspace, and verify the upgrade loads cleanly via logoscore.
```

## What the controller should do

1. Standard ladder: `controller-plan` → `workspace-status` → `modules list` → `policy-check`.
2. Read current pin from `scaffold.toml` `[modules.logos_storage_module]` and `flake.lock`.
3. Resolve latest stable tag via `gh api repos/logos-co/logos-storage-module/tags`.
4. Run `agentix controller-run "..." --execute` — in the worktree:
   - Update `[modules.logos_storage_module].flake` to new tag
   - `nix flake lock --update-input logos-storage-module` (or equivalent)
   - `ws build logos-storage-module --auto-local` (sanity build)
5. `agentix-logos verify-logoscore`:
   - `--modules storage_module`
   - `--call "storage_module.healthcheck()"`
   - `--call "storage_module.get_version()"` — assert version string matches new tag
   - `--call "storage_module.test_roundtrip()"` if it exists — store and retrieve a small file
6. Save proposal. Report. Stop.

## Expected proposal contents

- `scaffold.toml` diff: `[modules.logos_storage_module].flake` updated
- `flake.lock` diff: new locked rev for `logos-storage-module`
- Nothing else

## Human apply

```bash
agentix apply-verify --proposal .agentix/proposals/<latest>.patch
ws build logos-basecamp
./repos/logos-basecamp/result/bin/LogosBasecamp --user-dir /tmp/basecamp-storage-upgrade
# Verify storage app still works (publish + retrieve a file)
```

## Why this is the recommended Day 5 goal

- Smallest surface area: one module, no install/swap.
- Exercises every primitive: lock update, build, logoscore verify, patch save.
- Easy to validate post-apply: storage either works or it doesn't.
- Easy to roll back: revert the patch, re-apply, rebuild basecamp.

## Failure scenarios

- **No newer tag available** → controller reports nothing to do; no patch saved. Audit shows `result="noop"`.
- **`nix flake lock --update-input` fails** → likely because the upstream tag's flake.nix has incompatible inputs. Audit shows `error="flake_update_failed"`.
- **Storage roundtrip fails** → patch not saved. The new version regressed; bug for upstream.
