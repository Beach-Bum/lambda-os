# Codex Audit Anchor

How `.agentix/audit.jsonl` becomes Codex-anchored to make the audit trail decentralised, replayable, and tamper-evident across operators.

**Phase:** Phase 3 design (foundation document — no implementation in this issue). Implementation lives in Phase 3 issues filed after this design lands.

**Status:** Approved design, ready for Phase 3 implementation.

---

## Why this matters

Phase 1 ships a local-only audit log: every controller-run / worktree-run appends one canonical JSON line to `<workspace>/.agentix/audit.jsonl`. That gives single-operator traceability — Ned can prove what an agent did on his machine.

Phase 3 generalises that to a *network*: multiple operators, shared governance via `lez-multisig`, public verifiability. The audit trail has to:

1. Survive operator churn — an operator leaving the network shouldn't take the audit log with them.
2. Be tamper-evident — if anyone retroactively modifies a past line, the divergence must be detectable by anyone.
3. Stay private where it needs to — `goal` text and filesystem paths can leak intent; not everything belongs on a public feed.
4. Compose with `lez-multisig` apply gates — the multisig should be able to require an audit anchor exists before approving an apply.
5. Keep working offline — local development, air-gapped operators, and Codex-down events shouldn't break the loop.

Codex is the natural home for the artefact (content-addressed, persistent, replicated). LEZ is the natural home for the anchor (cheap commits, public verifiability, smart-contract gateable). Together they give us a tamper-evident, decentralised audit log without a central server.

## What stays unchanged

The local `audit.jsonl` format is the source of truth. Its bytes are the canonical artefact. Phase 3 does not change the schema in `docs/AUDIT-SCHEMA.md`, the writer in `agentix_logos/audit.py`, or any consumer (`audit tail`, `audit summary`, `agentix-logos audit show`). Phase 3 adds a *sidecar* and a *publisher worker*; the existing pipeline is untouched.

This is a deliberate constraint. Operators who never connect to Codex / LEZ get the same Phase 1 experience. Operators who do connect get the network properties on top.

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│  controller-run / worktree-run                                       │
│    │                                                                 │
│    ▼                                                                 │
│  agentix_logos.audit.audit_logos_run()                               │
│    │                                                                 │
│    ▼                                                                 │
│  .agentix/audit.jsonl  ←───────── canonical, sort_keys=True          │
│    │                                                                 │
│    ▼                                                                 │
│  ┌──────────────────────────────────────────────────────────┐       │
│  │  PHASE 3 ANCHOR WORKER (background)                       │       │
│  │                                                            │       │
│  │   tail audit.jsonl ──► canonical bytes ──► sha256 = CID  │       │
│  │                              │                            │       │
│  │   1. Codex put(canonical_bytes) → CID confirmed           │       │
│  │   2. LEZ tx anchor(CID, prev_cid, sequence_number)        │       │
│  │   3. write sidecar entry to .agentix/audit-codex.jsonl   │       │
│  │      {sequence_number, cid, prev_cid, lez_tx_id,          │       │
│  │       codex_published_at, lez_anchored_at}                │       │
│  └──────────────────────────────────────────────────────────┘       │
│                                                                      │
│   Anyone with .agentix/audit-codex.jsonl can:                        │
│   - Walk the chain via prev_cid                                      │
│   - Fetch each Codex object by CID                                   │
│   - Recompute sha256(line) and verify                                │
│   - Verify LEZ anchor txs at checkpoint frequency                    │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

The local `audit.jsonl` is append-only. The publisher worker runs out-of-band: it tails the file, processes new lines into Codex objects + LEZ anchors + sidecar entries. The sidecar is also append-only and never edits past entries.

## Format invariance

The Codex object **is** a single line of `audit.jsonl`. No re-serialisation, no field reordering, no transformation. The bytes that get hashed for the CID are exactly the bytes appended to the local file.

Three properties make this work:

1. **`sort_keys=True`.** Already enforced in `agentix_logos/audit.py`:
   ```python
   f.write(json.dumps(full_event, sort_keys=True) + "\n")
   ```
   Two operators producing structurally-identical events get byte-identical lines.

2. **Newline terminator.** Each line ends in exactly one `"\n"`. The CID is computed over the line *without* the trailing newline (so consumers can verify whether they read with or without it consistently).

3. **No floats.** The schema deliberately uses ints for counts, ISO-8601 strings for timestamps, and never `float`-typed numbers. Floats are non-canonical across language runtimes and would break byte-identical reproducibility.

The CID computation:

```python
def compute_cid(audit_line: str) -> str:
    """audit_line is the JSONL line *without* trailing newline."""
    line = audit_line.rstrip("\n").encode("utf-8")
    return "sha256:" + hashlib.sha256(line).hexdigest()
```

Returning a typed CID (`sha256:<hex>`) lets us migrate hash algorithms in a future schema version without breaking existing CIDs.

## Anchor flow — sequence numbers and the hash chain

Each sidecar entry contains:

| Field | Type | Meaning |
|---|---|---|
| `sequence_number` | `int` | Monotonically increasing per workspace, starting at 0 |
| `cid` | `string` | `sha256:<hex>` of this line's audit.jsonl bytes |
| `prev_cid` | `string \| null` | CID of `sequence_number - 1`, or `null` for genesis |
| `lez_tx_id` | `string \| null` | LEZ transaction id pinning this entry, or `null` if pending |
| `codex_published_at` | ISO-8601 string \| null | When Codex confirmed the put |
| `lez_anchored_at` | ISO-8601 string \| null | When LEZ confirmed the anchor tx |
| `chain_root` | `string` | `sha256(seq || cid || prev_cid)` — what gets anchored on LEZ |

The `chain_root` is what actually goes into the LEZ transaction. Anchoring just the CID would let an attacker forge a sequence (compute valid CIDs, swap chain order). Anchoring `chain_root` binds the CID to its position in the chain.

Genesis entry:

```json
{
  "sequence_number": 0,
  "cid": "sha256:abc123...",
  "prev_cid": null,
  "lez_tx_id": "0xdeadbeef...",
  "codex_published_at": "2026-05-06T08:14:23.451Z",
  "lez_anchored_at": "2026-05-06T08:14:31.117Z",
  "chain_root": "sha256:def456..."
}
```

Successor:

```json
{
  "sequence_number": 1,
  "cid": "sha256:b2c2d2...",
  "prev_cid": "sha256:abc123...",
  "lez_tx_id": "0xfeedface...",
  "codex_published_at": "2026-05-06T08:15:01.812Z",
  "lez_anchored_at": "2026-05-06T08:15:09.244Z",
  "chain_root": "sha256:e3f3g3..."
}
```

A consumer walks the sidecar and verifies the chain by recomputing `chain_root` for each entry and checking every `prev_cid` against the previous entry's `cid`. Any divergence is a tampering signal.

## Replay and verify

A third party fetches the sidecar (via Codex by CID, or directly from the operator), then:

```
for entry in sidecar:
    1. Fetch the audit object by entry.cid from Codex
    2. line = bytes_received_from_codex
    3. assert compute_cid(line) == entry.cid
    4. if entry.sequence_number > 0:
           assert entry.prev_cid == sidecar[seq-1].cid
    5. assert recompute(chain_root) == entry.chain_root
    6. at checkpoint frequency:
           query LEZ for entry.lez_tx_id
           assert lez_tx pins entry.chain_root
```

Verification is `O(n)` over the chain; checkpointing every N entries (configurable, default 100) keeps LEZ query cost bounded. A consumer who only cares about a recent state walks back from the latest checkpoint.

The `agentix-logos audit verify` command (already specced for Phase 2 in `docs/AUDIT-SCHEMA.md`) extends naturally: in Phase 3 it gains an optional `--against-codex` mode that performs the chain verification described above.

## Failure modes

The publisher worker is fail-soft. The local `audit.jsonl` is the source of truth; Codex and LEZ are downstream. Specific failure scenarios:

### Codex unreachable

- Worker queues the unpublished line locally (in-memory + a small `audit-pending.jsonl` for crash safety)
- Retries with exponential backoff (1s → 2s → … → 5min cap)
- After N hours of failures, surface to operator via `agentix audit doctor` exit code; do not block local writes
- Local `audit.jsonl` keeps appending normally — eventual consistency

### LEZ anchor fails

- CID is already published to Codex; sidecar entry exists with `lez_tx_id: null`, `lez_anchored_at: null`
- Worker retries the LEZ tx independently from Codex puts
- Same backoff strategy
- Sidecar updates the entry in place (the only mutation allowed; clearly marked as "anchor late-binding") to fill `lez_tx_id` once the tx confirms

> **Note:** the sidecar is *almost* append-only. The one allowed mutation is filling `lez_tx_id` and `lez_anchored_at` on a previously-pending entry. The CID, prev_cid, and chain_root never change. This is documented and detectable by consumers (a sidecar diff that touches anything other than the LEZ fields is a tampering signal).

### Chain fork

If two sidecar entries claim the same `sequence_number`:

- Fail closed. The publisher worker stops, surfaces an error.
- Human reconciles. Never auto-merge — chain forks should not happen if the worker is working correctly, and silently merging would mask a bug or an attack.
- Recovery: pick the canonical chain (typically the one with more LEZ-anchored entries), replay the divergent entries with new sequence numbers from the divergence point.

### Codex put succeeds but ack lost

- Worker re-puts on retry. Codex is content-addressed, so the second put is a no-op (same CID).
- No double-anchor on LEZ: the worker checks the sidecar before committing a new LEZ tx for the same CID.

### Local crash mid-write

- `audit.jsonl` writes are O_APPEND to a single file; partial line writes are detectable (no trailing newline). Worker skips partial lines on next start.
- `audit-pending.jsonl` queue uses the same pattern.
- `audit-codex.jsonl` is fsync'd after each entry. If the process crashes between Codex put and sidecar write, the next start replays from the local `audit.jsonl` and re-puts (idempotent, see above).

## Migration: Phase 1 → Phase 3

Existing operators have `audit.jsonl` from their Phase 1 work. The migration plan:

1. **Backfill on first connected run.** When the publisher worker starts and finds a non-empty `audit.jsonl` with no `audit-codex.jsonl`, it backfills:
   - For each existing line in file order, compute CID
   - Publish to Codex (idempotent — content-addressed)
   - Anchor to LEZ in chronological batches (configurable batch size; default = full backfill in one tx if < 100 entries)
   - Write sidecar entries with `migrated: true` flag

2. **Genesis chaining.** Backfilled entries chain from `sequence_number: 0` (the first existing line) just like a fresh start.

3. **Migration is idempotent.** Re-running migration on an already-migrated workspace is a no-op (Codex puts don't double-up; sidecar stops at the highest existing sequence_number).

4. **Pre-migration audit lines remain valid.** The schema is forward-compatible. A consumer that only knows Phase 1 schema can still read backfilled lines.

## Privacy

Some audit fields are sensitive:

- `goal` — free-text user goal, can leak intent (e.g. "deploy a financial product")
- `path` — filesystem paths, can leak organisational structure
- `logos_workspace_commit` — git SHA, can leak which fork an operator is on
- `sandbox_user_dir` — temp paths, low risk but include hostname-like data on some platforms

**Phase 3 strategy: redact-on-anchor.**

The local `audit.jsonl` retains plaintext. The Codex-published version replaces sensitive fields with `<field>_sha256` hashes. The sidecar records both the original CID (over the redacted line as published) and a `local_cid` (over the unredacted line as locally stored).

```
audit.jsonl line          ──sha256──►  local_cid
   │
   ├── redact goal, path  ──sha256──►  cid (the published one)
   └── publish to Codex
```

A consumer can verify the chain entirely via `cid` without ever needing the unredacted form. Selective unredaction (e.g. for governance review) is a Phase 4 concern: the operator opens up specific entries by sharing the unredacted line directly with the verifier, who recomputes `local_cid` and checks against the sidecar.

**Phase 4 evolution: field-level encryption.** Per-workspace encryption key. Sensitive fields get encrypted (not redacted) before publish. Verifiers with the key can selectively decrypt. Requires key management — defer to Phase 4 when key rotation, sharing, and revocation are also being designed.

## Implementation phases

| Phase | Scope |
|---|---|
| **Phase 3a** | Publisher worker (Python). Codex put + LEZ anchor + sidecar write. No redaction yet. Single-operator deployments. |
| **Phase 3b** | Redaction. `goal`, `path` and other sensitive fields swap for `<field>_sha256` on the published line. |
| **Phase 3c** | `agentix-logos audit verify --against-codex` extends the existing verify command to walk the chain. |
| **Phase 3d** | `lez-multisig` apply gate: an `lez-multisig` deploy that requires an audit anchor exists for the proposed change before approving. |
| **Phase 4** | Field-level encryption, key management, key rotation, group access. |

Phase 3a is the smallest unit that gives us real value: an operator can publish their audit log to Codex and anchor to LEZ. Phase 3b adds privacy. Phase 3c adds verification ergonomics. Phase 3d closes the governance loop. Phase 4 is the long tail.

## Open questions

These are tracked here so they don't get lost during implementation:

- **Anchor frequency vs cost.** Every line vs batched (every N lines or every M seconds)? Per-line maximises tamper-evidence resolution; batched amortises LEZ tx cost. Default proposed: batched at 1-min idle or 100-entry buffer, whichever first.
- **LEZ verification program.** Phase 3 trusts `lez-multisig` to anchor honestly. Phase 4 could deploy a LEZ program that *programmatically* enforces sequence + chain_root validity, refusing anchor txs that don't match. Provable refusal vs trust-the-multisig.
- **Sidecar retention.** Indefinite local? Rolling window after Codex confirms? (After Codex put, the local `audit.jsonl` is recoverable from Codex by CID list — local file becomes a cache.)
- **Multi-operator merge.** When two operators' audit chains need to interleave (e.g. shared `lez-multisig` gates), how do we agree on a global sequence_number? Proposed: per-operator chains; multisig governance log is a separate "global sequence" that anchors operator chain heads. Out of Phase 3 scope; revisit when first multi-op deployment is in flight.
- **Phase-2 `audit verify` overlap.** The Phase 2 `agentix-logos audit verify` (T5 / PER-6 already shipped) replays logoscore calls and confirms stdout SHA. The Phase 3 `--against-codex` mode adds chain verification. They're complementary — combine them in Phase 3c into one `audit verify --full` command.
- **Migration lossiness.** If the publisher worker is wired in *after* a workspace has accumulated thousands of audit lines, the LEZ batch anchor for the backfill could be expensive. Should there be an explicit `audit migrate` command that runs the backfill and caps the LEZ tx count at a configurable budget?

## References

- `docs/AUDIT-SCHEMA.md` § "Phase 3 migration: audit → Codex" (existing Phase 1 stub)
- `docs/AUDIT-SCHEMA.md` § "Schema versioning" (forward-compatibility constraints)
- `docs/BRIDGE-SPEC.md` § "Phase 3 (months 4-6)" (overall Phase 3 scope)
- `docs/POLICY-SCHEMA.md` § `lez_programs_pinned` (sister anchor pattern for LEZ programs)
- `agentix_logos/audit.py` (current writer; Phase 3 publisher worker reads its output)

---

*Implementation note: this design doc is the canonical reference. The Phase 3 implementation issues (to be filed) reference this document by section, not by paraphrase. If anything in this design needs to change during implementation, update this document first.*
