# PrimarySource — Evidence Provenance Conservatory

A GenLayer Intelligent Contract (MVP) that captures web page evidence as immutable, verifiable editions on-chain. Part of the **PrimarySource — Evidence Provenance Conservatory** project (idea #6 from `genlayer-ideas.md`).

## What it does

`PrimarySource` records a snapshot (edition) of any public URL:

- **Digest** — SHA-256 of the page body at capture time.
- **Status class** — OK (2xx), NOT_FOUND (404), GONE (410), or OTHER.
- **Body length**, capture timestamp, and the address that captured it.
- **Divergence flag** — set when a recapture returns OK but with a different digest than the previous OK edition. Stable content never produces a false divergence.

Each URL maps to one evidence record with a `DynArray` of editions. Editions are **immutable**: recapturing appends a new edition; earlier editions are never mutated. Divergence history persists even if the page later reverts to its original content — it is evidence, not noise.

Every edition records the **exact URL fetched**, so an edition can never be attributed to a different resource than the one retrieved.

### URL identity (RFC 3986)

| Component | Treatment | Example |
|---|---|---|
| Scheme | lowercased | `HTTPS://` → `https://` |
| Host | lowercased | `Example.COM` → `example.com` |
| Default port | removed | `:443` (https) / `:80` (http) |
| **Path** | **preserved (case-sensitive)** | `/Path` ≠ `/path` |
| **Query** | **preserved (case-sensitive)** | `?item=ABC` ≠ `?item=abc` |
| Fragment | dropped (never sent to server) | `/page#s` → `/page` |

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
| Direct-mode tests (`tests/direct/test_primary_source.py`) | **24 / 24 passed** |
| GenVM lint (`genvm-lint lint contracts/primary_source.py`) | **passed (3 checks)** |
| Live verification against Studionet deployment `0xFa3C...66Ab8` | **passed** (query-no-slash case distinctness, path-case distinctness, per-edition URL recording, recapture isolation, scheme/host normalization, code byte-identical) |

The current live deployment is `0xFa3C79Ff45b4049ce0a16b180bD65Fa68CE66Ab8` (commit `c537832`, deploy tx FINALIZED).


### Deployed contracts (Studionet)

| Deployment | Address | Source commit | Deploy tx | Status |
|---|---|---|---|---|
| Original (pre-lint fix) | `0x533792A0Ecff229F89178E34A14FAE2B53c115c5` | — | `0x3e7f397272f9309b1c1dc2dc3347d496dbbad6555b124ce7c1ee9e3064e2f321` | FINALIZED |
| Lint-clean | `0xC5f3A08f89f9bEEc58322161259A38BD318953B8` | `3d8e81b` | `0x7399888206a7a79fe153c208886e451467d566ebf69d0066acbd5069405e2072` | FINALIZED |
| URL-identity fix | `0xD033c74019FD4f92e6536577C424d5736f6043bf` | `7d6ee9d` | `0xc04125634a22d5e5edaacafdf066902b1669400f7011e083b2ea524b9661c5fb` | FINALIZED |
| **URL-identity fix 2 (current)** | `0xFa3C79Ff45b4049ce0a16b180bD65Fa68CE66Ab8` | `c537832` | `0xa18959f6dd607b21abb9ac46f6115c72c05163cd92d6a336a352cfa1f17a97d5` | FINALIZED |

- **Network:** Studionet, chain id 61999
- **Current on-chain code:** byte-identical to `contracts/primary_source.py` at commit `c537832` (sha256 `8cf38393ca90ffdcb6122e35c4cbf8ae6ecc1cc1df1ece3a5ea67adb41a907f0`)

All earlier deployments remain untouched on-chain. Everything is on Studionet only.

### Live test write transactions (against commit `c537832` deployment)

- T1 query-no-slash (`https://example.com?q=CASE_XYZ`): FINALIZED exec=SUCCESS
- T2 no-slash query case distinct (`?q=case_xyz`): FINALIZED exec=SUCCESS, distinct evidence id
- T3 path case (`/FreshPathABC` vs `/freshpathabc`): FINALIZED exec=SUCCESS, distinct evidence ids
- T4 per-edition URL (`/PerEditionAAA` / `HTTPS://EXAMPLE.COM/PerEditionAAA`): each edition returns its own fetched URL
- T5 recapture isolation (`/MergeTestUpper` vs `/mergetestupper`): never merged

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

Result: **19 / 19 passed**.

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
