# PrimarySource — Evidence Provenance Conservatory

A GenLayer Intelligent Contract (MVP) that captures web page evidence as immutable, verifiable editions on-chain. Part of the **PrimarySource — Evidence Provenance Conservatory** project (idea #6 from `genlayer-ideas.md`).

## What it does

`PrimarySource` records a snapshot (edition) of any public URL:

- **Digest** — SHA-256 of the page body at capture time.
- **Status class** — OK (2xx), NOT_FOUND (404), GONE (410), or OTHER.
- **Body length**, capture timestamp, and the address that captured it.
- **Divergence flag** — set when a recapture returns OK but with a different digest than the previous OK edition. Stable content never produces a false divergence.

Each URL maps to one evidence record with a `DynArray` of editions. Editions are **immutable**: recapturing appends a new edition; earlier editions are never mutated. Divergence history persists even if the page later reverts to its original content — it is evidence, not noise.

## Network

Deployed and tested on **GenLayer Studionet** (RPC `https://studio.genlayer.com/api`, chain id `61999`). Only Studionet is used — never mainnet, never real funds.

## Contract source

`contracts/primary_source.py` — the tested MVP source, extending `gl.Contract` with `@gl.public.view` / `@gl.public.write` methods:

- `capture_or_recapture(url) -> str` — capture or recapture a URL; returns the canonical evidence id.
- `get_edition(evidence_id, index) -> dict` — view a specific edition.
- `get_latest_edition(evidence_id) -> dict` — view the most recent edition.
- `edition_count(evidence_id) -> int` — number of editions recorded.
- `has_diverged(evidence_id) -> bool` — True if any edition was flagged divergent.

## Status

| Suite | Result |
|---|---|
| Direct-mode tests (`tests/direct/test_primary_source.py`) | **11 / 11 passed** |
| Live tests against Studionet deployment (`test_deployed.py`) | **6 / 6 passed** |

### Deployed contract (Studionet)

- **Address:** `0x533792A0Ecff229F89178E34A14FAE2B53c115c5`
- **Deploy tx:** `0x3e7f397272f9309b1c1dc2dc3347d496dbbad6555b124ce7c1ee9e3064e2f321` (FINALIZED)
- **Chain:** Studionet, id 61999
- **On-chain code:** byte-identical to `contracts/primary_source.py` (sha256 `5754022c07d33d382f2e3686b6319f9eb2a47d0b8709c20a7eed1851c21aede5`)

### Live test write transactions

- T1 first capture (`https://example.com`): `0xa8cb433db5af8cb7b2e1499b982d2a47ca3714ab9b8b94b728d6e6d1999eee83`
- T4 recapture (`https://example.com`): `0xe9f58d5ecbec266e87462f6fd4cfa1defadaf41b9ef2c0cd0c5e57812b5981f2`
- T5 error-URL capture (`https://example.com/nonexistent-page-xyz-404`): `0xb35f07d5ecb8a510020da872022fefce947416875c33119af6835038002d4665`

See `test_report.json` for the full public-only report (contract address, network, deploy tx, verification, final run 6/6, write tx hashes, evidence IDs, notes, key-disposal statement). It contains **no secrets**.

## Setup (local development)

Python **3.12+** is required (`genlayer-test` uses `collections.abc.Buffer`, unavailable on 3.10/3.11).

```bash
cd /root/PrimarySource

# 1. Create a Python 3.12 venv
python3.12 -m venv .venv
source .venv/bin/activate

# 2. Install dependencies
pip install -r requirements.txt
```

`requirements.txt`:
```
genlayer-test==0.29.2
pytest
```

## Testing

### Direct-mode tests (no network needed)

Run against a local GenVM:

```bash
source .venv/bin/activate
python -m pytest tests/direct/ -v
```

Result: **11 / 11 passed**.

### Live tests against Studionet deployment

```bash
source .venv/bin/activate
python test_deployed.py
```

This generates a **fresh throwaway wallet in memory**, funds it via Studionet's official faucet RPC (`sim_fundAccount`), runs six functional tests (capture, digest vs local sha256, views, edition-0 immutability, error-URL recording, no false divergence), waits for FINALIZATION after every write, and checks the GenVM execution result. It writes `test_report.json` (public only, no secrets).

Result: **6 / 6 passed**.

## Deployment (`deploy_studionet.py`)

One-shot Studionet deployment:

1. Generates a temporary wallet **locally in memory** (OS entropy via `eth_account`), never written to disk, never printed.
2. Funds it via Studionet's official `sim_fundAccount` faucet RPC.
3. Deploys `contracts/primary_source.py`.
4. Waits for FINALIZED consensus.
5. Verifies deployed on-chain code matches the tested source.
6. Writes `deployment_info.json` (public only).
7. Process exits — the temporary key existed only in this process's memory.

**Never** mainnet, never real funds, never a CLI wallet, never a persisted private key.

## Known limitations

1. **QUORUM_REACHED quirk:** A leader execution cancelled after validator quorum is recorded as GenVM `execution_result = ERROR` (`CONSENSUS_VALIDATOR_QUORUM_REACHED`) even though the transaction is correct and FINALIZED. The live test suite treats `FINALIZED` + this error code as a pass; on-chain state assertions remain the independent truth.
2. **502 transience:** The hosted Studionet endpoint occasionally returns 502 / Bad gateway / invalid JSON. Both read and write helpers in `test_deployed.py` retry with backoff.
3. **No frontend:** Scope is limited to the smart contract and tests. A UI is planned for the full PrimarySource project, developed separately using this contract as foundation.
4. **Edition-0 immutability:** Recapturing identical content does not mutate edition 0; edited content appends a new divergent edition. Divergence is permanent historical evidence — it is never cleared if content reverts.

## Repository contents

- `contracts/primary_source.py` — the deployed GenLayer Intelligent Contract MVP
- `tests/conftest.py` — direct-mode test helpers (sha256 utility)
- `tests/direct/test_primary_source.py` — 11 direct-mode tests (11/11 passing)
- `test_deployed.py` — live Studionet test battery (6/6 passing)
- `test_report.json` — public-only live test report (no secrets)
- `deploy_studionet.py` — one-shot Studionet deployment script (no secrets)
- `README.md` — this file
- `.gitignore` — excludes venv, caches, secrets
- `requirements.txt` — `genlayer-test`, `pytest`

**Excluded:** `genlayer-ideas.md` (private research file, not part of this contract repo), `.venv/`, `__pycache__/`, `.pytest_cache/`, `.claude/`, `artifacts/`, any secret or env files.

## License

Not specified yet. The full PrimarySource project will be developed separately using this contract as its foundation.
