#!/usr/bin/env python3
"""Labels BIP-329 de muchas billeteras: merge, búsqueda y origen de UTXOs.

  btclabels.py merge [-l DIR] -o master.jsonl
  btclabels.py find  [-l DIR] TEXTO
  btclabels.py trace [-l DIR] [-s host:puerto] [--dot g.dot] REF...

-l (repetible) es un archivo o un directorio; por defecto, el directorio
actual. Se aceptan exports BIP-329 (.jsonl: Sparrow y otros) y exports
viejos de Electrum (.json, objeto {txid|dirección: label}). El nombre del
archivo, sin "-labels", es el nombre de la billetera.

REF es txid:vout, un txid (todas sus salidas) o una dirección (sus UTXOs
actuales). trace consulta un servidor Electrum propio (Fulcrum, electrs) y
recorre las entradas hacia atrás hasta dar con algo etiquetado.
Solo stdlib.
"""
import argparse
import hashlib
import json
import os
import re
import socket
import ssl
import sys

KEYS = ("tx", "addr", "output", "input", "pubkey", "xpub")
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
OUTREF = re.compile(r"^[0-9a-fA-F]{64}:\d+$")

# ------------------------------------------------------------- lectura


def wallet_name(path):
    base = re.sub(r"\.(jsonl|json)$", "", os.path.basename(path))
    return re.sub(r"-labels$", "", base)


def expand(paths, skip=()):
    skip = {os.path.abspath(s) for s in skip}
    files = []
    for p in paths:
        if os.path.isdir(p):
            for n in sorted(os.listdir(p)):
                full = os.path.join(p, n)
                if n.endswith((".jsonl", ".json")) and \
                        os.path.abspath(full) not in skip:
                    files.append(full)
        else:
            files.append(p)
    if not files:
        sys.exit("no encontré archivos de labels (.jsonl/.json)")
    return files


def from_electrum(data):
    recs = []
    for ref, label in data.items():
        if not isinstance(label, str) or not label:
            continue
        if HEX64.match(ref):
            recs.append({"type": "tx", "ref": ref.lower(), "label": label})
        elif OUTREF.match(ref):
            recs.append({"type": "output", "ref": ref.lower(),
                         "label": label})
        else:
            recs.append({"type": "addr", "ref": ref, "label": label})
    return recs


def load(path):
    with open(path, encoding="utf-8-sig") as f:
        text = f.read().strip()
    if not text:
        return []
    recs = None
    try:
        data = json.loads(text)
        if isinstance(data, dict) and "type" not in data:
            recs = from_electrum(data)
    except json.JSONDecodeError:
        pass
    if recs is None:
        recs = []
        for n, line in enumerate(text.splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError as e:
                sys.exit(f"{path}:{n}: JSON inválido: {e}")
            if r.get("type") not in KEYS or "ref" not in r:
                print(f"{path}:{n}: registro sin type/ref, salteado",
                      file=sys.stderr)
                continue
            recs.append(r)
    w = wallet_name(path)
    for r in recs:
        r["_wallet"] = w
    return recs


def merge(paths, quiet=False, skip=()):
    """Une por (type, ref). Devuelve (registros, conflictos)."""
    out, conflicts = {}, 0
    for p in expand(paths, skip):
        for r in load(p):
            k = (r["type"], r["ref"])
            w = r.pop("_wallet")
            if k not in out:
                out[k] = dict(r, _wallets=[w])
                continue
            cur = out[k]
            if w not in cur["_wallets"]:
                cur["_wallets"].append(w)
            a, b = cur.get("label", ""), r.get("label", "")
            if b and b not in a.split(" / "):
                if a:
                    conflicts += 1
                    if not quiet:
                        print(f"conflicto {k[0]} {k[1]}: {a!r} + {b!r}",
                              file=sys.stderr)
                cur["label"] = f"{a} / {b}" if a else b
            for f, v in r.items():
                if f != "label" and f not in cur:
                    cur[f] = v
    return list(out.values()), conflicts


def index(recs):
    ix = {t: {} for t in KEYS}
    for r in recs:
        if r.get("label"):
            ix[r["type"]][r["ref"]] = r
    return ix

# ------------------------------------------------------------ electrum


class Electrum:
    def __init__(self, server, use_ssl, insecure):
        host, port = server.rsplit(":", 1)
        s = socket.create_connection((host, int(port)), timeout=60)
        if use_ssl:
            ctx = ssl.create_default_context()
            if insecure:
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
            s = ctx.wrap_socket(s, server_hostname=host)
        self.f = s.makefile("rwb")
        self.i = 0
        self.call("server.version", ["btclabels", "1.4"])

    def call(self, method, params):
        self.i += 1
        msg = {"jsonrpc": "2.0", "id": self.i,
               "method": method, "params": params}
        self.f.write(json.dumps(msg).encode() + b"\n")
        self.f.flush()
        while True:
            line = self.f.readline()
            if not line:
                raise RuntimeError("el servidor cerró la conexión")
            resp = json.loads(line)
            if resp.get("id") == self.i:
                break
        if resp.get("error"):
            raise RuntimeError(f"{method}: {resp['error']}")
        return resp["result"]

# ------------------------------------------------------------ tx decode


def varint(b, i):
    n = b[i]
    if n < 0xfd:
        return n, i + 1
    w = {0xfd: 2, 0xfe: 4, 0xff: 8}[n]
    return int.from_bytes(b[i + 1:i + 1 + w], "little"), i + 1 + w


def decode_tx(hexstr):
    b = bytes.fromhex(hexstr)
    i = 4
    if b[i] == 0 and b[i + 1] != 0:
        i += 2
    n, i = varint(b, i)
    ins = []
    for _ in range(n):
        txid = b[i:i + 32][::-1].hex()
        vout = int.from_bytes(b[i + 32:i + 36], "little")
        i += 36
        sl, i = varint(b, i)
        i += sl + 4
        ins.append((txid, vout))
    n, i = varint(b, i)
    outs = []
    for _ in range(n):
        val = int.from_bytes(b[i:i + 8], "little")
        i += 8
        sl, i = varint(b, i)
        outs.append((val, b[i:i + sl]))
        i += sl
    coinbase = len(ins) == 1 and ins[0][0] == "0" * 64
    return ins, outs, coinbase

# ---------------------------------------------- script <-> dirección

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
B32 = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


def dsha(b):
    return hashlib.sha256(hashlib.sha256(b).digest()).digest()


def b58check(payload):
    data = payload + dsha(payload)[:4]
    n, s = int.from_bytes(data, "big"), ""
    while n:
        n, r = divmod(n, 58)
        s = B58[r] + s
    return "1" * (len(data) - len(data.lstrip(b"\0"))) + s


def b58decode(s):
    n = 0
    for c in s:
        n = n * 58 + B58.index(c)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big")
    raw = b"\0" * (len(s) - len(s.lstrip("1"))) + raw
    payload, chk = raw[:-4], raw[-4:]
    if dsha(payload)[:4] != chk:
        raise ValueError(f"checksum base58 inválido: {s}")
    return payload


def _polymod(values):
    gen = [0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3]
    chk = 1
    for v in values:
        top = chk >> 25
        chk = (chk & 0x1ffffff) << 5 ^ v
        for k in range(5):
            chk ^= gen[k] if (top >> k) & 1 else 0
    return chk


def _hrp_expand(hrp):
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def segwit_addr(ver, prog, hrp="bc"):
    acc, bits, data = 0, 0, [ver]
    for x in prog:
        acc = (acc << 8) | x
        bits += 8
        while bits >= 5:
            bits -= 5
            data.append((acc >> bits) & 31)
    if bits:
        data.append((acc << (5 - bits)) & 31)
    const = 1 if ver == 0 else 0x2bc830a3
    pm = _polymod(_hrp_expand(hrp) + data + [0] * 6) ^ const
    data += [(pm >> 5 * (5 - k)) & 31 for k in range(6)]
    return hrp + "1" + "".join(B32[d] for d in data)


def segwit_decode(addr):
    addr = addr.lower()
    hrp, _, rest = addr.rpartition("1")
    data = [B32.index(c) for c in rest]
    ver = data[0]
    const = 1 if ver == 0 else 0x2bc830a3
    if _polymod(_hrp_expand(hrp) + data) != const:
        raise ValueError(f"checksum bech32 inválido: {addr}")
    acc, bits, prog = 0, 0, []
    for v in data[1:-6]:
        acc = (acc << 5) | v
        bits += 5
        if bits >= 8:
            bits -= 8
            prog.append((acc >> bits) & 0xff)
    return ver, bytes(prog)


def script_addr(s):
    if len(s) == 25 and s[:3] == b"\x76\xa9\x14" and s[23:] == b"\x88\xac":
        return b58check(b"\x00" + s[3:23])
    if len(s) == 23 and s[:2] == b"\xa9\x14" and s[22] == 0x87:
        return b58check(b"\x05" + s[2:22])
    if len(s) in (22, 34) and s[0] == 0 and s[1] == len(s) - 2:
        return segwit_addr(0, s[2:])
    if len(s) == 34 and s[0] == 0x51 and s[1] == 32:
        return segwit_addr(1, s[2:])
    return None


def addr_script(addr):
    if addr.lower().startswith("bc1"):
        ver, prog = segwit_decode(addr)
        op = 0 if ver == 0 else 0x50 + ver
        return bytes([op, len(prog)]) + prog
    p = b58decode(addr)
    if p[0] == 0:
        return b"\x76\xa9\x14" + p[1:] + b"\x88\xac"
    if p[0] == 5:
        return b"\xa9\x14" + p[1:] + b"\x87"
    raise ValueError(f"dirección no soportada: {addr}")


def scripthash(script):
    return hashlib.sha256(script).digest()[::-1].hex()

# --------------------------------------------------------------- trace


class Tracer:
    def __init__(self, el, ix, max_depth, max_nodes, follow_labeled):
        self.el, self.ix = el, ix
        self.max_depth, self.max_nodes = max_depth, max_nodes
        self.follow = follow_labeled
        self.cache, self.seen = {}, set()
        self.nodes, self.edges, self.starts = {}, [], []

    def tx(self, txid):
        if txid not in self.cache:
            self.cache[txid] = decode_tx(
                self.el.call("blockchain.transaction.get", [txid]))
        return self.cache[txid]

    def resolve(self, ref):
        """txid:vout | txid | dirección -> lista de txid:vout."""
        if OUTREF.match(ref):
            return [ref.lower()]
        if HEX64.match(ref):
            _, outs, _ = self.tx(ref.lower())
            return [f"{ref.lower()}:{i}" for i in range(len(outs))]
        sh = scripthash(addr_script(ref))
        utxos = self.el.call("blockchain.scripthash.listunspent", [sh])
        if not utxos:
            print(f"{ref}: sin UTXOs", file=sys.stderr)
        return [f"{u['tx_hash']}:{u['tx_pos']}" for u in utxos]

    def labels_of(self, txid, vout):
        _, outs, _ = self.tx(txid)
        val, spk = outs[vout]
        addr = script_addr(spk)
        found = []
        for typ, key in (("output", f"{txid}:{vout}"), ("addr", addr),
                         ("tx", txid)):
            r = self.ix[typ].get(key) if key else None
            if r:
                found.append((typ, r["label"], r.get("_wallets", [])))
        return val, addr, found

    def walk(self, ref, depth):
        pad = "  " * depth
        if ref in self.seen:
            print(f"{pad}{ref} (ya visto)")
            return
        self.seen.add(ref)
        txid, vout = ref.split(":")
        val, addr, found = self.labels_of(txid, int(vout))
        print(f"{pad}{ref}  {val / 1e8:.8f} BTC  {addr or '?'}")
        for typ, label, wallets in found:
            print(f"{pad}  [{typ}] {label}  ({', '.join(wallets)})")
        if not found:
            print(f"{pad}  (sin label)")
        ins, _, coinbase = self.tx(txid)
        self.nodes[ref] = (val, addr, found, coinbase)
        if coinbase:
            print(f"{pad}  coinbase")
            return
        if found and not self.follow and depth > 0:
            return
        if depth >= self.max_depth:
            print(f"{pad}  ... corte por profundidad ({self.max_depth})")
            return
        print(f"{pad}  entradas ({len(ins)}):")
        for ptx, pvout in ins:
            if len(self.nodes) >= self.max_nodes:
                print(f"{pad}  ... corte por cantidad de nodos"
                      f" ({self.max_nodes})")
                return
            pref = f"{ptx}:{pvout}"
            self.edges.append((pref, ref))
            self.walk(pref, depth + 1)

    def run(self, refs):
        for r in refs:
            for start in self.resolve(r):
                if len(self.nodes) >= self.max_nodes:
                    print(f"... corte por cantidad de nodos ({self.max_nodes})")
                    return
                self.starts.append(start)
                self.walk(start, 0)
                print()

    def dot(self):
        def q(s):
            return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'

        def ql(s):
            return q(s)[:-1].replace("\n", "\\l") + '\\l"'

        lines = ["digraph utxo {", "  rankdir=LR;",
                 '  node [shape=box, style="rounded,filled",'
                 ' fontname="monospace", fontsize=10];']
        for ref, (val, addr, found, coinbase) in self.nodes.items():
            txid, vout = ref.split(":")
            text = [f"{txid[:8]}…{txid[-4:]}:{vout}", f"{val / 1e8:.8f} BTC"]
            if addr:
                text.append(addr[:12] + "…" + addr[-6:])
            for typ, label, wallets in found:
                text.append(f"[{typ}] {label}")
                if wallets:
                    text.append(f"  ({', '.join(wallets)})")
            if coinbase:
                text.append("coinbase")
            color = ("lightblue" if ref in self.starts else
                     "palegreen" if found else "white")
            lines.append(f"  {q(ref)} [label={ql(chr(10).join(text))},"
                         f" fillcolor={color}];")
        for a, b in dict.fromkeys(self.edges):
            lines.append(f"  {q(a)} -> {q(b)};")
        lines.append("}")
        return "\n".join(lines) + "\n"

# ----------------------------------------------------------------- cli


def write_bip329(recs, path):
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            r = {k: v for k, v in r.items() if not k.startswith("_")}
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def labels_arg(p):
        p.add_argument("-l", "--labels", action="append", metavar="RUTA",
                       help="archivo o directorio (repetible; default: .)")

    m = sub.add_parser("merge", help="junta todo en un BIP-329 único")
    labels_arg(m)
    m.add_argument("-o", "--out", required=True)
    m.add_argument("-q", "--quiet", action="store_true",
                   help="no listar cada conflicto")

    f = sub.add_parser("find", help="busca labels por texto")
    labels_arg(f)
    f.add_argument("text")

    t = sub.add_parser("trace", help="rastrea el origen de UTXOs")
    labels_arg(t)
    t.add_argument("-s", "--server", default="127.0.0.1:50001",
                   help="servidor Electrum host:puerto")
    t.add_argument("--ssl", action="store_true")
    t.add_argument("--insecure", action="store_true",
                   help="no verificar el cert (Fulcrum autofirmado)")
    t.add_argument("--max-depth", type=int, default=15)
    t.add_argument("--max-nodes", type=int, default=300)
    t.add_argument("--all", action="store_true",
                   help="seguir subiendo aunque haya label")
    t.add_argument("--dot", metavar="ARCHIVO",
                   help="además escribe el grafo en Graphviz (.dot)")
    t.add_argument("refs", nargs="+", metavar="REF",
                   help="txid:vout, txid o dirección")
    a = ap.parse_args(argv)
    paths = a.labels or ["."]

    if a.cmd == "merge":
        recs, conflicts = merge(paths, a.quiet, skip=[a.out])
        write_bip329(recs, a.out)
        print(f"{len(recs)} registros -> {a.out} ({conflicts} conflictos)",
              file=sys.stderr)
        return

    recs, _ = merge(paths, quiet=True)
    if a.cmd == "find":
        needle = a.text.lower()
        for r in recs:
            if needle in r.get("label", "").lower():
                print(f"{r['type']:6} {r['ref']}  {r['label']}"
                      f"  ({', '.join(r['_wallets'])})")
        return

    tr = Tracer(Electrum(a.server, a.ssl, a.insecure), index(recs),
                a.max_depth, a.max_nodes, a.all)
    tr.run(a.refs)
    if a.dot:
        with open(a.dot, "w", encoding="utf-8") as fh:
            fh.write(tr.dot())
        print(f"grafo -> {a.dot} ({len(tr.nodes)} nodos)", file=sys.stderr)


if __name__ == "__main__":
    main()
