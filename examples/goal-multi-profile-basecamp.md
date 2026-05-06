# Goal — Multi-profile basecamp configuration

A reference goal exercising **parallel basecamp instances** via Agentix proposal. Each profile gets isolated port ranges and module directories to prevent interference between instances.

## Goal text

```
Configure 3 parallel basecamp profiles in logos-workspace. Each profile
gets its own --user-dir, non-overlapping port range from sandbox_port_range,
and distinct module directory. Validate isolation before saving the proposal.
```

## What the controller should do

1. Standard ladder: `controller-plan` → `workspace-status` → `modules list` → `policy-check`.
2. Read existing `[profiles.*]` blocks from `scaffold.toml` (via `agentix_logos.profiles.list_profiles`).
3. Allocate port bases: `agentix_logos.profiles.allocate_profile_ports(workspace, 3)`. Uses `sandbox_port_range` from policy (default [13000, 14000]).
4. Run `agentix controller-run "..." --execute` — in the worktree:
   - Add/update `[profiles.<name>]` blocks in `scaffold.toml` with `user_dir`, `port_base`, `module_dir`.
5. **Validate isolation.** `agentix_logos.profiles.validate_profile_isolation(workspace)`:
   - No port overlap between profiles
   - No shared module directories
   - No collision with Logos default port (3040)
6. Save proposal. Report. Stop.

## Expected proposal contents

- `scaffold.toml` diff: `[profiles.*]` blocks added/updated with isolation-valid assignments.
- Nothing else.

## scaffold.toml shape

```toml
[profiles.dev]
user_dir = "sandbox/profiles/dev"
module_dir = "sandbox/profiles/dev/modules"
port_base = 13000

[profiles.test]
user_dir = "sandbox/profiles/test"
module_dir = "sandbox/profiles/test/modules"
port_base = 13010

[profiles.staging]
user_dir = "sandbox/profiles/staging"
module_dir = "sandbox/profiles/staging/modules"
port_base = 13020
```

## Human apply

```bash
agentix apply-verify --proposal .agentix/proposals/<latest>.patch
# Launch each profile in a separate terminal:
cd ~/projects/logos-workspace
./repos/logos-basecamp/result/bin/LogosBasecamp --user-dir sandbox/profiles/dev --port 13000
./repos/logos-basecamp/result/bin/LogosBasecamp --user-dir sandbox/profiles/test --port 13010
./repos/logos-basecamp/result/bin/LogosBasecamp --user-dir sandbox/profiles/staging --port 13020
```

## Validation rules

- Each profile must have a unique `user_dir`.
- Each profile must have a unique `module_dir` (if declared).
- Port bases must not overlap (each profile occupies 10 consecutive ports).
- No profile's port range may include the Logos default port (3040).
- Port bases must fall within `policy.sandbox_port_range`.

## Failure scenarios

- **Port range too small** → `allocate_profile_ports` raises ValueError. Widen `sandbox_port_range` in policy.json or reduce profile count.
- **Port collision** → `validate_profile_isolation` reports overlapping ranges. Adjust port_base values to maintain spacing of 10.
- **Module dir overlap** → Two profiles point at the same module directory. Each needs its own to prevent state corruption.
- **Default port collision** → A profile's port_base is within 10 of port 3040. Move it into the sandbox range.

## Why parallel profiles matter

Testing module upgrades in isolation requires launching basecamp with different module sets. Profiles let the controller propose and validate a multi-instance setup without risking the user's primary basecamp state. The port isolation ensures no two instances bind to the same socket — a silent corruption vector if unchecked.
