# MeshFlow Audit Chain Specification

**Current format: 2. Legacy format: 1 (verification supported).**

A run is an ordered sequence of records. The first record has `prev_hash=""`;
each following record references the preceding `entry_hash`. SHA-256 hashes
are lowercase hexadecimal strings. Records from different runs have independent
chains, including when their writes share a batch.

## Version 2

New records contain `metadata.hash_version=2`. The canonical JSON object includes:

- `run_id`, `step_id`, `node_id`, `node_kind`
- Full stored `input_task` and `output_content`
- `verdict`, boolean `blocked`, `block_reason`
- `uncertainty`, `cost_usd`, `tokens_used`, `carbon_gco2`, `duration_ms`
- `timestamp`, `prev_hash`, complete `metadata`

Normalize `uncertainty`, `cost_usd`, `carbon_gco2` and `duration_ms` to floats.
Serialize with Python `json.dumps(payload, sort_keys=True, allow_nan=False,
separators=(",", ":"))`, encode as UTF-8, then compute SHA-256. The Python default
`ensure_ascii=True` is part of the canonical format. The metadata includes the
ledger tenant ID and, for batched records, the Merkle proof.

Storage-generated row IDs, compression flags and `entry_hash` itself are not
canonical inputs. Verify after decompressing output and decoding metadata.
Check that the stored tenant column agrees with the hashed metadata tenant.
Policy redaction and truncation happen before hashing: omitted source text is
not part of the stored record and is not protected.

For a Merkle leaf, use the same canonical fields with `prev_hash=""` and remove
only `metadata.merkle_proof`. A parent hash is SHA-256 of the concatenated ASCII
hex hashes of its children. Duplicate the final child for odd-length levels.
The entry hash includes the completed proof metadata after leaf construction.

## Legacy version 1

Records without `metadata.hash_version=2` use the original canonical fields:
`run_id`, `step_id`, `node_id`, the first 200 characters of `input_task` and
`output_content`, `verdict`, boolean `blocked`, `timestamp`, and `prev_hash`.
Their JSON uses `json.dumps(payload, sort_keys=True)` with default separators.
Existing hashes are never silently upgraded: v1 does not protect accounting,
metadata, or input/output suffixes.

## Verification

`await ledger.verify_chain(run_id)` recomputes canonical hashes, checks chain
links and validates available Merkle proofs. `steps_verified` reports the number
of retrieved records; inspect it when verifying an expected run. An empty run has
no chain links to check and is not evidence that expected records exist.

A chain alone cannot detect wholesale replacement, deletion of an entire run,
or suffix truncation without a trusted external final hash or expected record
count. Use write-once archives or externally anchored roots for that threat model.
SHA-256 provides tamper evidence, not access control or regulatory certification.
