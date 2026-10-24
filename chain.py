import datetime as dt
import re

from eth_abi import encode as abi_encode
from eth_account import Account
from eth_account.messages import encode_defunct
from eth_utils import keccak
from solcx import compile_files, install_solc
from solcx.exceptions import SolcNotInstalled
from web3 import EthereumTesterProvider, Web3

SOLC = "0.8.20"


def day_number(date):
    return (date - dt.date(1970, 1, 1)).days


class Chain:
    def __init__(self, sol_path, stoves, sensor_keys, baselines):
        try:
            out = compile_files([str(sol_path)], solc_version=SOLC, output_values=["abi", "bin"])
        except SolcNotInstalled:
            install_solc(SOLC)
            out = compile_files([str(sol_path)], solc_version=SOLC, output_values=["abi", "bin"])
        iface = next(iter(out.values()))
        self.w3 = Web3(EthereumTesterProvider())
        self.oracle, self.auditor, self.operator, *accounts = self.w3.eth.accounts
        factory = self.w3.eth.contract(abi=iface["abi"], bytecode=iface["bin"])
        rcpt = self.w3.eth.wait_for_transaction_receipt(
            factory.constructor(self.oracle, self.auditor).transact({"from": self.oracle}))
        self.c = self.w3.eth.contract(address=rcpt.contractAddress, abi=iface["abi"])

        self.sid = {s: keccak(text=s) for s in stoves}
        self.keys = {s: Account.from_key(k if k.startswith("0x") else "0x" + k)
                     for s, k in sensor_keys.items()}
        self.baseline = {s: bytes.fromhex(baselines[s]) for s in stoves}
        self.hh = {s: accounts[i] for i, s in enumerate(stoves)}
        self.log, self.done = [], set()

        self.c.functions.fund().transact({"from": self.operator, "value": self.w3.to_wei(1, "ether")})
        for s in stoves:
            self.c.functions.registerStove(
                self.sid[s], [self.hh[s]], self.baseline[s], self.keys[s].address
            ).transact({"from": self.operator, "value": self.w3.to_wei(0.01, "ether")})

    def head(self, s):
        return bytes(self.c.functions.stoves(self.sid[s]).call()[3])

    def stake(self, s):
        return float(self.w3.from_wei(self.c.functions.stoves(self.sid[s]).call()[4], "ether"))

    def pool(self):
        return float(self.w3.from_wei(self.c.functions.pool().call(), "ether"))

    def balances(self):
        return {s: float(self.w3.from_wei(self.c.functions.balance(h).call(), "ether"))
                for s, h in self.hh.items()}

    def sign(self, s, day, raw, prev, key=None):
        """Sensor signature over (stove, day, raw data hash, previous head)."""
        digest = keccak(abi_encode(["bytes32", "uint32", "bytes32", "bytes32"],
                                   [self.sid[s], day, raw, prev]))
        signer = key if key is not None else self.keys[s]
        return bytes(Account.sign_message(encode_defunct(digest), signer.key).signature)

    def submit(self, s, day, hashes, raw, prev=None, sig=None, key=None):
        prev = self.head(s) if prev is None else prev
        sig = sig if sig is not None else self.sign(s, day, raw, prev, key)
        tx = self.c.functions.submitDay((self.sid[s], day, raw, prev), hashes, sig).transact({"from": self.oracle})
        self.log.append({
            "claim_id": len(self.log), "stove": s,
            "date": dt.date(1970, 1, 1) + dt.timedelta(days=day), "day": day,
            "event_hash": hashes[0].hex(), "raw_hash": raw.hex(),
            "prev_head": prev.hex(), "new_head": keccak(prev + raw).hex(),   # same rule as the contract
            "signature": sig.hex(), "tx": self.w3.to_hex(tx)})
        return len(self.log) - 1

    def attempt(self, *args, **kwargs):
        """Submit and return (True, claim_id) or (False, revert reason)."""
        try:
            return True, self.submit(*args, **kwargs)
        except Exception as e:
            m = re.search(r"execution reverted: ([^\n|]*)", str(e))
            return False, (m.group(1).strip() if m else str(e)[:160])

    def release_all(self):
        """Move the chain clock past the holdback and pay every open claim."""
        tester = self.w3.provider.ethereum_tester
        tester.time_travel(self.w3.eth.get_block("latest")["timestamp"] + 4 * 86400)
        tester.mine_blocks(1)
        for e in self.log:
            if e["claim_id"] not in self.done:
                self.c.functions.release(e["claim_id"]).transact({"from": self.operator})
                self.done.add(e["claim_id"])

    def challenge(self, claim_id):
        stove = next(e["stove"] for e in self.log if e["claim_id"] == claim_id)
        before = self.stake(stove)
        self.c.functions.challenge(claim_id).transact({"from": self.auditor})
        self.done.add(claim_id)
        return stove, before, self.stake(stove)