"""Functional tests against the deployed PrimarySource on Studionet.

Security: the temp key lives only in this process; never printed, never saved.
Deployed contract is NOT modified beyond its normal write methods.
"""
import hashlib
import json
import sys

from genlayer_py import create_client, create_account
from genlayer_py.chains import studionet
from genlayer_py.types import TransactionStatus, ExecutionResult

ADDR = "0x533792A0Ecff229F89178E34A14FAE2B53c115c5"
CONTRACT = "contracts/primary_source.py"

results = []

def record(name, ok, detail):
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'} | {name} | {detail}")

def main():
    acct = create_account()            # fresh throwaway, in-memory
    client = create_client(chain=studionet, account=acct)
    rpc = client.provider.make_request
    print("temp wallet:", acct.address)

    fund = rpc(method="sim_fundAccount", params=[acct.address, 10**20])
    if "error" in fund:
        print("BLOCKER: faucet failed:", fund["error"]); return 1
    wei = int(rpc(method="eth_getBalance", params=[acct.address, "latest"])["result"], 16)
    print(f"balance: {wei/10**18} test GEN")
    assert wei >= 10**18, "insufficient funding"

    def write(fn, args):
        import time
        tx = None
        for attempt in range(3):
            try:
                tx = client.write_contract(address=ADDR, function_name=fn, args=args)
                break
            except Exception as e:
                msg = str(e)
                if ("502" in msg or "Bad gateway" in msg or "invalid JSON" in msg) and attempt < 2:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
        receipt = client.wait_for_transaction_receipt(
            transaction_hash=tx, status=TransactionStatus.FINALIZED, retries=90,
            full_transaction=True,
        )
        # consensus outcome (result_name) and GenVM execution outcome are distinct;
        # docs require checking execution results, not just consensus status.
        # NOTE: a leader cancelled after validator quorum ("VALIDATOR_QUORUM_REACHED")
        # is recorded as execution ERROR although the tx is correct+finalized.
        exec_res = None
        cancel_reason = None
        cd = receipt.get("consensus_data") or {}
        lr = cd.get("leader_receipt") if isinstance(cd, dict) else None
        if isinstance(lr, list):
            lr = lr[-1] if lr else None
        if isinstance(lr, dict):
            exec_res = lr.get("execution_result")
            gr = lr.get("genvm_result") or {}
            if isinstance(gr, dict) and gr.get("error_code") == "CONSENSUS_VALIDATOR_QUORUM_REACHED":
                cancel_reason = "VALIDATOR_QUORUM_REACHED"
        status_name = receipt.get("status_name") or str(receipt.get("status"))
        exec_ok = exec_res == "SUCCESS" or (cancel_reason == "VALIDATOR_QUORUM_REACHED" and status_name == "FINALIZED")
        ok = status_name == "FINALIZED" and exec_ok
        tag = f"execution_result={exec_res}"
        if cancel_reason:
            tag += f" (leader cancelled after quorum: {cancel_reason})"
        return tx, receipt, ok, f"status={status_name} {tag}"

    def read(fn, args):
        import time
        last = None
        for attempt in range(5):
            try:
                return client.read_contract(address=ADDR, function_name=fn, args=args)
            except Exception as e:
                last = e
                msg = str(e)
                if "502" in msg or "Bad gateway" in msg or "invalid JSON" in msg:
                    time.sleep(5 * (attempt + 1))
                    continue
                raise
        raise last

    def write_retry(fn, args):
        return write(fn, args)

    def sha(b): return hashlib.sha256(b if isinstance(b, bytes) else str(b).encode()).hexdigest()

    URL = "https://example.com"

    # baseline may already exist from earlier runs — test is state-aware
    base_count = read("edition_count", [URL])
    print(f"baseline edition_count({URL}) = {base_count}")

    # ---- T1: capture public URL ----
    tx, receipt, ok, detail = write("capture_or_recapture", [URL])
    record("T1 capture(example.com)", ok, f"tx={tx[:18]}... {detail}")
    t1_tx = tx

    # ---- T2: evidence record + digest verified against local sha256 ----
    import urllib.request
    body = urllib.request.urlopen(urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0"}), timeout=30).read()
    local_digest = sha(body)
    e0 = read("get_edition", [URL, 0])
    got_digest = (e0.get("digest") if isinstance(e0, dict) else e0)
    ok = (got_digest == local_digest) and (e0["status"] == "OK")
    record("T2 digest matches live fetch", ok, f"onchain={str(got_digest)[:12]}... local={local_digest[:12]}... status={e0['status']}")

    # ---- T3: views ----
    n = read("edition_count", [URL])
    rec = read("get_edition", [URL, 0])
    lat = read("get_latest_edition", [URL])
    ok = (n == base_count + 1) and (rec["digest"] == got_digest) and (rec["url"] == URL)
    record("T3 views after first capture", ok, f"count={n} (base={base_count}+1), captured_at={rec['captured_at']}")

    # ---- T4: recapture same URL; edition 0 must stay unchanged ----
    before = read("get_edition", [URL, 0])
    tx, receipt, ok, detail = write("capture_or_recapture", [URL])
    t2_tx = tx
    after = read("get_edition", [URL, 0])
    n = read("edition_count", [URL])
    latest = read("get_latest_edition", [URL])
    ok = n == base_count + 2 and before == after and latest["edition"] == n - 1
    record("T4 immutable edition 0 after recapture", ok,
           f"count={n}, edition0_unchanged={before==after}, latest_edition_idx={latest['edition']}, tx={tx[:18]}... {detail}")

    # ---- T5: error URL (404) ----
    BAD = "https://example.com/nonexistent-page-xyz-404"
    bad_base = read("edition_count", [BAD])
    tx, receipt, ok, detail = write("capture_or_recapture", [BAD])
    t3_tx = tx
    n_bad = read("edition_count", [BAD])
    e_bad = read("get_edition", [BAD, n_bad - 1])
    ok = n_bad == bad_base + 1 and e_bad["status"] in ("NOT_FOUND", "GONE", "UNREACHABLE", "OTHER")
    record("T5 error-URL capture recorded as evidence", ok,
           f"count={n_bad} (base={bad_base}), latest_status={e_bad['status']}, tx={tx[:18]}... {detail}")

    # ---- T6: divergence handling (no false positive on stable content) ----
    div = read("has_diverged", [URL])
    ok = div is False
    record("T6 no false divergence on stable URL", ok, f"has_diverged={div} after 2 identical captures")

    print()
    print("== write tx hashes ==")
    for label, t in [("T1 first capture", t1_tx), ("T4 recapture", t2_tx), ("T5 error-url capture", t3_tx)]:
        print(f"{label}: {t}")
    print()
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"== {passed}/{len(results)} passed ==")
    json.dump({
        "tests": [{"name": n, "ok": ok, "detail": d} for n, ok, d in results],
        "write_txs": {"T1": t1_tx, "T4": t2_tx, "T5": t3_tx},
        "evidence_ids": {URL: n, BAD: n_bad},
        "temp_wallet_address": acct.address,
    }, open("test_report.json", "w"), indent=2)
    return 0 if passed == len(results) else 2

if __name__ == "__main__":
    sys.exit(main())
