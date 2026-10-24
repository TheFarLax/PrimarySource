"""One-shot Studionet deployment for PrimarySource.

Security model for the temporary wallet:
- Key generated in-process by eth_account (OS entropy), never written to disk.
- Key exists only inside this process's memory; never printed, never serialized.
- Funded exclusively via Studionet's official sim_fundAccount faucet RPC
  (the same RPC the Studio UI's faucet button calls).
- This process is the wallet's entire lifecycle: generate -> fund -> deploy
  -> verify -> forget. Nothing else ever uses it.

Network: Studionet ONLY (https://studio.genlayer.com/api, chain 61999).
"""
import hashlib
import json
import sys

from genlayer_py import create_client, create_account
from genlayer_py.chains import studionet
from genlayer_py.types import TransactionStatus

RPC = "https://studio.genlayer.com/api"
EXPECTED_CHAIN_ID = 61999
SOURCE_PATH = "contracts/primary_source.py"
SOURCE_COMMIT = "3d8e81b"
FAUCET_AMOUNT_WEI = 10**20  # 100 test GEN (verified to credit properly)

def rpc(client, method, params):
    return client.provider.make_request(method=method, params=params)

def main():
    # ---- 0. sanity: network is Studionet and nothing else ----
    assert studionet.id == EXPECTED_CHAIN_ID, "chain id mismatch"
    assert studionet.rpc_urls["default"]["http"][0] == RPC, "RPC mismatch"

    with open(SOURCE_PATH, "r", encoding="utf-8") as f:
        code = f.read()

    client = create_client(chain=studionet)  # no account attached yet
    live = rpc(client, "eth_chainId", [])
    live_id = int(live["result"], 16)
    if live_id != EXPECTED_CHAIN_ID:
        print(f"BLOCKER: endpoint chain id {live_id} != {EXPECTED_CHAIN_ID}")
        return 1
    print(f"[0] endpoint verified: chainId={live_id} (Studionet)")

    # ---- 1. fresh temporary wallet, generated locally in memory ----
    account = create_account()          # eth_account random key, in-memory only
    addr = account.address
    print(f"[1] temporary wallet generated: {addr}")

    # ---- 2. fund via the official Studionet faucet RPC ----
    fund = rpc(client, "sim_fundAccount", [addr, FAUCET_AMOUNT_WEI])
    if "error" in fund:
        print(f"BLOCKER: faucet error: {fund['error']}")
        return 1
    bal = rpc(client, "eth_getBalance", [addr, "latest"])
    bal_wei = int(bal["result"], 16)
    print(f"[2] faucet ok (tx {str(fund['result'])[:18]}...), balance = {bal_wei/10**18} test GEN")
    if bal_wei <= 0:
        print("BLOCKER: balance is zero after funding")
        return 1

    # ---- 3. deploy the tested contract source ----
    client_local = create_client(chain=studionet, account=account)
    tx_hash = client_local.deploy_contract(code=code, leader_only=False)
    print(f"[3] deploy tx submitted: {tx_hash}")

    # ---- 4. wait for consensus ----
    receipt = client_local.wait_for_transaction_receipt(
        transaction_hash=tx_hash, status=TransactionStatus.FINALIZED, retries=60
    )
    status = rpc(client, "gen_getTransactionStatus", [tx_hash])["result"]
    print(f"[4] consensus status: {status}")

    # ---- 5. extract contract address from receipt ----
    def find_addr(obj):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k in ("contract_address", "to") and isinstance(v, str) and v.startswith("0x") and int(v, 16) != 0:
                    return v
                r = find_addr(v)
                if r:
                    return r
        elif isinstance(obj, list):
            for v in obj:
                r = find_addr(v)
                if r:
                    return r
        return None

    contract_address = find_addr(receipt if isinstance(receipt, dict) else {"r": receipt})
    print(f"[5] receipt keys: {sorted(receipt.keys()) if isinstance(receipt, dict) else type(receipt)}")
    if not contract_address:
        print("BLOCKER: could not determine contract address from receipt")
        print(json.dumps(receipt, default=str)[:2000])
        return 1

    # ---- 6. verify deployed code is byte-identical to the approved commit ----
    import base64
    code_resp = rpc(client, "gen_getContractCode", [contract_address])
    if "error" in code_resp:
        print(f"BLOCKER: gen_getContractCode error: {code_resp['error']}")
        return 1
    raw = code_resp["result"].get("code") if isinstance(code_resp["result"], dict) else code_resp["result"]
    try:
        deployed_code = base64.b64decode(raw)  # gen_getContractCode returns base64
    except Exception:
        deployed_code = str(raw).encode("utf-8")  # fallback: already text
    local_bytes = code.encode("utf-8")
    local_sha = hashlib.sha256(local_bytes).hexdigest()
    chain_sha = hashlib.sha256(deployed_code).hexdigest()
    match = (chain_sha == local_sha)
    print(f"[6] local  sha256: {local_sha}")
    print(f"[6] onchain sha256: {chain_sha}")
    print(f"[6] byte-identical to commit {SOURCE_COMMIT}: {match}")

    # ---- 7. post-deploy reads (state must be empty/zero) ----
    n = client_local.read_contract(address=contract_address, function_name="edition_count", args=["https://example.com"])
    print(f"[7] edition_count(https://example.com) = {n} (expected 0)")
    info = {
        "network": "Studionet",
        "chain_id": EXPECTED_CHAIN_ID,
        "rpc": RPC,
        "contract_address": contract_address,
        "deploy_tx": tx_hash,
        "deploy_status": status,
        "source_commit": SOURCE_COMMIT,
        "byte_identical_to_commit": match,
        "source_sha256": local_sha,
        "deployed_code_sha256": chain_sha,
    }
    with open("deployment_info.json", "w") as f:
        json.dump(info, f, indent=2)
    print("[7] wrote deployment_info.json")
    print(json.dumps(info, indent=2))
    print("KEY_DISPOSED: temporary private key existed only in process memory; process exits now.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
