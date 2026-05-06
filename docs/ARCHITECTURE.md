# Architecture: Why Agentix + Logos

This document is the strategic / architectural rationale for `agentix-logos`. The implementation spec is [`BRIDGE-SPEC.md`](./BRIDGE-SPEC.md). The runbook is [`../RUNBOOK.md`](../RUNBOOK.md). This document is for explaining to anyone — collaborators, IFT leadership, grant reviewers, future contributors — *why* this bridge exists.

## The thesis in one sentence

> Agentix and Logos converge on Nix flakes as substrate by independent reasoning. Wired together, they form an autonomous, sovereign, network-native operating system: agents that can install, configure, and evolve the system; a substrate no central party can revoke; a governance layer where the human-in-the-loop generalises from one operator to a network state.

## What each project is, briefly

### Logos (`logos-co`, `logos-blockchain`)

A modular technology stack consolidating three previously separate IFT projects:

- **Nomos** → **Logos Blockchain** (consensus, LEZ execution zone with public + private state via RISC-V + ZK)
- **Codex** → **Logos Storage** (decentralised, content-addressed file storage)
- **Waku** → **Logos Messaging** (Logos Delivery, Logos Chat, libp2p mix protocol)

Logos's own framing is **a Linux distribution pattern**:

> *"A Linux distribution isn't solely a single binary — it's a runtime foundation, a networking stack, a set of system services, and the applications that together create a complete operating system. Logos follows the same pattern."*

Layered from the bottom up:

| Layer | What's there |
|---|---|
| Networking | Mix protocol, RLN spam protection, capability discovery |
| Modules | `.lgx` packages built via `nix bundle` — wallet, chat, storage, blockchain, custom |
| Runtime | `logoscore` headless module runtime, `liblogos` library |
| Apps | `logos-basecamp` launcher, custom dapps |
| Build system | **Nix flakes throughout** — workspace propagates local overrides via `follows` |
| Governance | LIPs (Logos Improvement Proposals), Assembly, lez-multisig |

Logos's own [`logos-workspace`](https://github.com/logos-co/logos-workspace) repo description includes the phrase *"AI tooling for agents"* — they've identified the gap.

### Agentix (`Beach-Bum/Agentix`)

A safety-first agent control layer for NixOS:

> *"The long-term goal is an OS where AI agents can help configure, repair, maintain, and evolve the machine — without receiving unrestricted live-system control."*

Core loop:

```
plan → sandbox → propose → verify → human apply/rebuild
```

Hard invariants:

- Source workspace untouched (HEAD + diff + SHA-256 untracked files snapshotted before/after)
- No `sudo`, no `nixos-rebuild switch`, no `/etc/nixos` mutation from agents
- Git-worktree sandbox; only allowed mutation is one new patch under `.agentix/proposals/`
- Audit log per run (JSONL)
- Conservative subprocess timeout
- Sanitized export workflow for public release

## Why the connection is structural, not metaphorical

Both projects target the same substrate primitives:

| Logos primitive | Agentix primitive | Match |
|---|---|---|
| `nix build '.#lgx'` | `agentix package` / `agentix propose` | Both speak `flake.nix` natively |
| Module = `metadata.json` + Nix output, content-addressed | Proposal = signed git diff + audit JSONL | Both produce reviewable, reproducible, auditable artifacts |
| `logos-workspace` `--auto-local` overrides via `follows` | Agentix git-worktree sandbox with HEAD snapshots | Both isolate change while keeping the dep graph intact |
| `logoscore` headless runtime (load module, call method, observe) | Agentix `worktree-run` (execute goal, save diff, never touch source) | Worktree-run *driving* a logoscore call is the natural composition |
| LEZ programs via `lgs deploy` with project-local sequencer | Agentix proposes deployment patches, human applies | Same deploy/verify discipline, at LEZ layer |
| LIPs (Markdown spec → reviewable PR → status) | Agentix proposals (`.agentix/proposals/*.patch`) | Both are PR-shaped review artifacts |
| Logos Assembly + lez-multisig (governance) | Agentix's "human-final-approval" rung | Today human = operator; on Logos, human = multisig / governance |

This is not a coincidence. **NixOS is the only mainstream substrate where reproducible-agentic-systems are tractable today.** Both projects arrived independently; the bridge formalises the convergence.

## What the combined system looks like

```
┌──────────────────────────────────────────────────────────────────┐
│  USER GOALS  ("install storage module + chat, harden privacy")   │
├──────────────────────────────────────────────────────────────────┤
│  AGENTIX       plan → sandbox → propose → verify → apply         │
│                (LLM controller speaks the safety contract)       │
├──────────────────────────────────────────────────────────────────┤
│  AGENTIX-LOGOS  workspace adapter, logoscore verify, policy,     │
│                 audit extensions                                 │
├──────────────────────────────────────────────────────────────────┤
│  LOGOS APPS   basecamp, standalone-app, custom dapps             │
├──────────────────────────────────────────────────────────────────┤
│  LOGOS MODULES   wallet, chat, storage, blockchain, custom       │
│                  (.lgx = nix bundle, metadata.json, deps)        │
├──────────────────────────────────────────────────────────────────┤
│  LOGOS RUNTIME   logoscore / liblogos                            │
├──────────────────────────────────────────────────────────────────┤
│  LOGOS NETWORK   Mix, RLN, Discovery, LEZ, Delivery, Codex       │
├──────────────────────────────────────────────────────────────────┤
│  NIX / NIXOS    declarative, reproducible, rollback-friendly     │
└──────────────────────────────────────────────────────────────────┘
```

## Six properties that fall out

**(a) Module operations become agent-driven, audited, and reversible.** Adding/removing/upgrading Logos modules becomes a reviewable PR-shaped event with full audit trail.

**(b) `logoscore` is the agent's tool surface for live introspection.** Every "did this module actually start cleanly?" check becomes part of `verify`, not part of `apply`.

**(c) Agentix's safety contract maps onto Logos governance.** Today `apply` is `human at the keyboard`. Replace with `lez-multisig` signature threshold or Assembly approval — the proposal is a signed patch, the multisig signature is the apply trigger. Single-operator agent → network-state agent without changing safety invariants.

**(d) The `.lgx` ecosystem becomes an autonomously-installable software market.** Agentix can propose package installs/upgrades and refuse to apply if `policy.json` rules forbid it. App-store-style review, written in Nix, executed by an agent, signed off by governance.

**(e) The "Agentic OS" goal and the Logos vision become the same goal.** Agentix on stock NixOS = a hardened agent IDE. Logos without an agent layer = a decentralised app stack with manual operator burden. Combined = first realistic version of an autonomous, sovereign, network-native OS.

**(f) Decentralisation carries through every layer.** Each Agentix primitive has a Logos-native distribution path (audit on Codex, proposals on Codex, governance on lez-multisig, policy enforcement on LEZ, agent definitions as LEZ programs).

## Phasing

Phase 1 (now): local demo. All on one machine. Human applies on keyboard.

Phase 2 (months 2-3): `agentix-logos-module` (Agentix as a Logos module), policy hardening, three more demo goals.

Phase 3 (months 4-6): decentralised audit + proposals on Codex, apply via lez-multisig, discovery via Mix.

Phase 4 (months 6-12): policy enforcement as a LEZ program, agents-as-LEZ-programs, self-hosting.

## Strategic position

This bridge is the most natural way to land Agentix inside IFT:

- Agentix stays independent — authorship and direction preserved.
- Substrate alignment means almost no integration friction (both already speak Nix).
- Logos's roadmap and `logos-co/ideas` are explicitly soliciting contributions of this shape.
- The `logos-workspace` description literally calls out *"AI tooling for agents"* as a workstream.
- The IFT mission (sovereign technology, exit from platform capture) is the right political grounding for "Agentic OS" — without which it collapses into another devops AI tool.

## One-line pitch

> *Logos is building a decentralised Linux distro. Agentix is building the agent control layer for a Nix-based OS. Both target NixOS as the substrate by independent reasoning. Wired together, Logos + Agentix is the first realistic version of an autonomous, sovereign, network-native operating system.*
