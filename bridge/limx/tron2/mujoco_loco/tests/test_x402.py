import pytest
from flow.x402 import X402Verifier, X402Challenge, X402Error

GOOD = {"txHash": "0x" + "ab" * 32, "amount": "0.10",
        "network": "base-sepolia", "asset": "0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        "payer": "0xF2749b5fAdA8a83d3DE1a2621B1d212e73907D4a"}


def test_rejects_missing_txhash():
    with pytest.raises(X402Error):
        X402Verifier().verify({})


def test_rejects_bad_format():
    with pytest.raises(X402Error):
        X402Verifier().verify({**GOOD, "txHash": "0xdeadbeef"})


def test_rejects_amount_mismatch():
    with pytest.raises(X402Error):
        X402Verifier().verify({**GOOD, "amount": "0.20"})


def test_rejects_network_mismatch():
    with pytest.raises(X402Error):
        X402Verifier().verify({**GOOD, "network": "eip155:1"})


def test_rejects_asset_mismatch():
    with pytest.raises(X402Error):
        X402Verifier().verify({**GOOD, "asset": "0x0000000000000000000000000000000000000000"})


def test_rejects_replay():
    v = X402Verifier()
    v.verify(GOOD)
    with pytest.raises(X402Error):
        v.verify(GOOD)


def test_accepts_well_formed():
    r = X402Verifier().verify(GOOD)
    assert r["verified"] is True
