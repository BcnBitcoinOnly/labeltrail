import contextlib
import io
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
import btclabels as b  # noqa: E402

HAL = "f4184fc596403b9d638783cf57adfe4c75c605f6356fbc91338530e9831e9e16"
CB9 = "0437cd7f8525ceed2324359c2d0ba26006d92d856a9c20fa0241106ee5a597c9"

VECTORS = {
    "bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t4":
        "0014751e76e8199196d454941c45d1b3a323f1433bd6",
    "bc1p0xlxvlhemja6c4dqv22uapctqupfhlxm9h8z3k2e72q4k9hcz7vqzk5jj0":
        "512079be667ef9dcbbac55a06295ce870b07029bfcdb2dce28d959f2815b16f81798",
    "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2":
        "76a91477bff20c60e522dfaa3350c39b030a5d004e839a88ac",
    "3J98t1WpEZ73CNmQviecrnyiWrnqRhWNLy":
        "a914b472a266d0bd89c13706a4132ccfb16f7c3b9fcb87",
}


class FakeElectrum:
    """Sirve txs desde tests/fixtures/<txid>.hex."""

    def call(self, method, params):
        assert method == "blockchain.transaction.get", method
        with open(os.path.join(HERE, "fixtures", params[0] + ".hex")) as f:
            return f.read().strip()


def write(d, name, content):
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)
    return p


class TestAddresses(unittest.TestCase):
    def test_roundtrip(self):
        for addr, spk in VECTORS.items():
            self.assertEqual(b.addr_script(addr).hex(), spk)
            self.assertEqual(b.script_addr(bytes.fromhex(spk)), addr)

    def test_bad_checksum(self):
        with self.assertRaises(ValueError):
            b.addr_script("bc1qw508d6qejxtdg4y5r3zarvary0c5xw7kv8f3t5")


class TestMerge(unittest.TestCase):
    def test_bip329_and_electrum(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "Fria-labels.jsonl",
                  json.dumps({"type": "tx", "ref": HAL, "label": "a hal"})
                  + "\n" + json.dumps({"type": "output",
                                       "ref": f"{CB9}:0",
                                       "label": "minado"}) + "\n")
            write(d, "electrum_viejo.json",
                  json.dumps({HAL: "p2p", "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2":
                              "dir vieja"}))
            write(d, "Otra-labels.jsonl",
                  json.dumps({"type": "tx", "ref": HAL, "label": "a hal"})
                  + "\n")
            with contextlib.redirect_stderr(io.StringIO()):
                recs, conflicts = b.merge([d])
        by = {(r["type"], r["ref"]): r for r in recs}
        self.assertEqual(len(recs), 3)
        self.assertEqual(conflicts, 1)
        tx = by[("tx", HAL)]
        self.assertEqual(tx["label"], "a hal / p2p")
        self.assertEqual(sorted(tx["_wallets"]),
                         ["Fria", "Otra", "electrum_viejo"])
        self.assertIn(("addr", "1BvBMSEYstWetqTFn5Au4m4GFg7xJaNVN2"), by)

    def test_merge_output_is_plain_bip329(self):
        with tempfile.TemporaryDirectory() as d:
            write(d, "A-labels.jsonl",
                  json.dumps({"type": "tx", "ref": HAL, "label": "x"}) + "\n")
            out = os.path.join(d, "master.jsonl")
            with contextlib.redirect_stderr(io.StringIO()):
                b.main(["merge", "-l", d, "-o", out])
                b.main(["merge", "-l", d, "-o", out])
            with open(out) as f:
                lines = [json.loads(x) for x in f]
        self.assertEqual(lines, [{"type": "tx", "ref": HAL, "label": "x"}])


class TestTrace(unittest.TestCase):
    def test_trace_to_coinbase(self):
        recs = [{"type": "output", "ref": f"{CB9}:0", "label": "minado",
                 "_wallets": ["Fria"]}]
        tr = b.Tracer(FakeElectrum(), b.index(recs), 15, 300, False)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            tr.run([f"{HAL}:0", HAL])
        text = out.getvalue()
        self.assertIn("[output] minado  (Fria)", text)
        self.assertIn("coinbase", text)
        self.assertEqual(tr.starts, [f"{HAL}:0", f"{HAL}:0", f"{HAL}:1"])
        self.assertEqual(len(tr.nodes), 3)
        dot = tr.dot()
        self.assertIn(f'"{CB9}:0" -> "{HAL}:0";', dot)
        self.assertIn("palegreen", dot)

    def test_decode_values(self):
        ins, outs, coinbase = b.decode_tx(FakeElectrum().call(
            "blockchain.transaction.get", [HAL]))
        self.assertEqual(ins, [(CB9, 0)])
        self.assertEqual([v for v, _ in outs], [10 * 10**8, 40 * 10**8])
        self.assertFalse(coinbase)


if __name__ == "__main__":
    unittest.main()
