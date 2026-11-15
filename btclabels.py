#!/usr/bin/env python3
"""Labels BIP-329 de muchas billeteras: merge, búsqueda y origen de UTXOs.

  btclabels.py merge [-l DIR] -o master.jsonl
  btclabels.py find  [-l DIR] TEXTO
  btclabels.py trace [-l DIR] [-s host:puerto] [--dot g.dot] REF...
  btclabels.py report [-l DIR] [-s host:puerto] [-o utxos.html]

-l (repetible) es un archivo o un directorio; por defecto, el directorio
actual. Se aceptan exports BIP-329 (.jsonl: Sparrow y otros) y exports
viejos de Electrum (.json, objeto {txid|dirección: label}). El nombre del
archivo, sin "-labels", es el nombre de la billetera.

REF es txid:vout, un txid (todas sus salidas) o una dirección (sus UTXOs
actuales). trace consulta un servidor Electrum propio (Fulcrum, electrs) y
recorre las entradas hacia atrás por tus outputs hasta llegar a lo externo:
por dónde entró la plata. report hace eso para todos los UTXOs vivos de los
exports y arma un HTML con índice y un grafo por UTXO.
Solo stdlib.
"""
import argparse
import hashlib
import html
import json
import os
import re
import socket
import shutil
import ssl
import subprocess
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

    def call_many(self, method, params_list, chunk=100):
        """Pipelining: manda de a `chunk` pedidos y lee las respuestas."""
        results = []
        for k in range(0, len(params_list), chunk):
            ids = {}
            for params in params_list[k:k + chunk]:
                self.i += 1
                ids[self.i] = len(ids)
                msg = {"jsonrpc": "2.0", "id": self.i,
                       "method": method, "params": params}
                self.f.write(json.dumps(msg).encode() + b"\n")
            self.f.flush()
            got = [None] * len(ids)
            pending = len(ids)
            while pending:
                line = self.f.readline()
                if not line:
                    raise RuntimeError("el servidor cerró la conexión")
                resp = json.loads(line)
                if resp.get("id") not in ids:
                    continue
                if resp.get("error"):
                    raise RuntimeError(f"{method}: {resp['error']}")
                got[ids[resp["id"]]] = resp["result"]
                pending -= 1
            results += got
        return results

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
    """Recorre hacia atrás desde UTXOs.

    "Propio" = output o dirección que aparece en algún export (Sparrow
    exporta todos los outputs y direcciones, con o sin label). Se sube por
    lo propio y se para en lo externo: ahí entró la plata a tus billeteras.
    """

    def __init__(self, el, recs, max_depth, max_nodes, follow_labeled):
        self.el = el
        self.ix = index(recs)
        self.own_out, self.own_addr = ownership(recs)
        self.max_depth, self.max_nodes = max_depth, max_nodes
        self.follow = follow_labeled
        self.cache = {}
        self.reset()

    def reset(self):
        self.seen, self.nodes, self.edges = set(), {}, []
        self.starts, self.lines = [], []

    def tx(self, txid):
        if txid not in self.cache:
            self.cache[txid] = decode_tx(
                self.el.call("blockchain.transaction.get", [txid]))
        return self.cache[txid]

    def prefetch(self, txids):
        need = [t for t in dict.fromkeys(txids) if t not in self.cache]
        for t, raw in zip(need, self.el.call_many(
                "blockchain.transaction.get", [[t] for t in need])):
            self.cache[t] = decode_tx(raw)

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

    def info(self, ref):
        txid, vout = ref.split(":")
        ins, outs, coinbase = self.tx(txid)
        val, spk = outs[int(vout)]
        addr = script_addr(spk)
        found = []
        for typ, key in (("output", ref), ("addr", addr), ("tx", txid)):
            r = self.ix[typ].get(key) if key else None
            if r:
                found.append((typ, r["label"], r.get("_wallets", [])))
        wallets = self.own_out.get(ref) or self.own_addr.get(addr)
        return {"val": val, "addr": addr, "found": found, "ins": ins,
                "coinbase": coinbase, "own": wallets or [],
                "entry": False}

    def emit(self, depth, text):
        self.lines.append("  " * depth + text)

    def walk(self, ref, depth):
        if ref in self.seen:
            self.emit(depth, f"{ref} (ya visto)")
            return
        self.seen.add(ref)
        n = self.info(ref)
        self.nodes[ref] = n
        who = f"  <{', '.join(n['own'])}>" if n["own"] else "  <externo>"
        self.emit(depth, f"{ref}  {n['val'] / 1e8:.8f} BTC"
                         f"  {n['addr'] or '?'}{who}")
        for typ, label, wallets in n["found"]:
            self.emit(depth + 1, f"[{typ}] {label}  ({', '.join(wallets)})")
        if n["coinbase"]:
            self.emit(depth + 1, "coinbase")
            return
        if depth > 0 and not n["own"] and not self.follow:
            return
        if depth >= self.max_depth:
            self.emit(depth + 1, f"... corte por profundidad"
                                 f" ({self.max_depth})")
            return
        self.prefetch(t for t, _ in n["ins"])
        self.emit(depth + 1, f"entradas ({len(n['ins'])}):")
        for ptx, pvout in n["ins"]:
            if len(self.nodes) >= self.max_nodes:
                self.emit(depth + 1, f"... corte por cantidad de nodos"
                                     f" ({self.max_nodes})")
                return
            pref = f"{ptx}:{pvout}"
            self.edges.append((pref, ref))
            self.walk(pref, depth + 1)
            if not self.nodes[pref]["own"]:
                n["entry"] = True

    def run(self, refs):
        for r in refs:
            for start in self.resolve(r):
                if len(self.nodes) >= self.max_nodes:
                    self.emit(0, f"... corte por cantidad de nodos"
                                 f" ({self.max_nodes})")
                    return
                self.starts.append(start)
                self.walk(start, 0)
                self.lines.append("")

    def entries(self):
        """Nodos propios que recibieron desde afuera (o coinbase)."""
        return [(ref, n) for ref, n in self.nodes.items()
                if n["own"] and (n["entry"] or n["coinbase"])]

    def dot(self):
        def q(s):
            return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'

        def ql(s):
            return q(s)[:-1].replace("\n", "\\l") + '\\l"'

        lines = ["digraph utxo {", "  rankdir=LR;",
                 '  node [shape=box, style="rounded,filled",'
                 ' fontname="monospace", fontsize=10];']
        for ref, n in self.nodes.items():
            txid, vout = ref.split(":")
            text = [f"{txid[:8]}…{txid[-4:]}:{vout}",
                    f"{n['val'] / 1e8:.8f} BTC"]
            if n["addr"]:
                text.append(n["addr"][:12] + "…" + n["addr"][-6:])
            text.append(f"<{', '.join(n['own'])}>" if n["own"]
                        else "<externo>")
            for typ, label, _ in n["found"]:
                text.append(f"[{typ}] {label}")
            if n["coinbase"]:
                text.append("coinbase")
            color = ("lightblue" if ref in self.starts else
                     "gold" if n["entry"] else
                     "palegreen" if n["found"] and n["own"] else
                     "white" if n["own"] else "lightgrey")
            lines.append(f"  {q(ref)} [label={ql(chr(10).join(text))},"
                         f" fillcolor={color}];")
        for a, b in dict.fromkeys(self.edges):
            lines.append(f"  {q(a)} -> {q(b)};")
        lines.append("}")
        return "\n".join(lines) + "\n"

# -------------------------------------------------------------- report


def ownership(recs):
    """outputs y direcciones que aparecen en los exports -> billeteras."""
    own_out, own_addr = {}, {}
    for r in recs:
        if r["type"] == "output":
            own_out[r["ref"]] = r.get("_wallets", [])
        elif r["type"] == "addr":
            own_addr[r["ref"]] = r.get("_wallets", [])
    return own_out, own_addr


def find_utxos(el, recs):
    """UTXOs vivos entre los outputs exportados.

    La dirección sale del registro addr con el mismo origin+keypath; si no
    está, de la transacción. Después, listunspent por dirección.
    """
    by_path = {(r.get("origin"), r.get("keypath")): r["ref"]
               for r in recs if r["type"] == "addr" and r.get("keypath")}
    outs = [r for r in recs if r["type"] == "output"]
    addr_of, missing = {}, []
    for r in outs:
        a = by_path.get((r.get("origin"), r.get("keypath")))
        if a:
            addr_of[r["ref"]] = a
        else:
            missing.append(r["ref"])
    if missing:
        txids = list(dict.fromkeys(m.split(":")[0] for m in missing))
        raws = dict(zip(txids, el.call_many(
            "blockchain.transaction.get", [[t] for t in txids])))
        for m in missing:
            txid, vout = m.split(":")
            _, o, _ = decode_tx(raws[txid])
            a = script_addr(o[int(vout)][1])
            if a:
                addr_of[m] = a
    addrs = list(dict.fromkeys(addr_of.values()))
    res = el.call_many("blockchain.scripthash.listunspent",
                       [[scripthash(addr_script(a))] for a in addrs])
    live = {f"{u['tx_hash']}:{u['tx_pos']}" for us in res for u in us}
    utxos = [r for r in outs if r["ref"] in live]
    utxos.sort(key=lambda r: (r["_wallets"][0], -(r.get("value") or 0)))
    return utxos


def esc(s):
    return html.escape(str(s), quote=True)


def svg_of(dot_src):
    if not shutil.which("dot"):
        return None
    p = subprocess.run(["dot", "-Tsvg"], input=dot_src.encode(),
                       capture_output=True, check=False)
    if p.returncode:
        return None
    svg = p.stdout.decode()
    return svg[svg.find("<svg"):]


CSS = """
body{font:14px system-ui,sans-serif;margin:2em;color:#222}
table{border-collapse:collapse;width:100%}
td,th{border-bottom:1px solid #ddd;padding:4px 8px;text-align:left;
vertical-align:top}
td.n{text-align:right;font-family:monospace}
code,pre{font-family:monospace;font-size:12px}
section{border-top:3px solid #333;margin-top:3em;padding-top:1em}
nav a{margin-right:1em}
.graph{overflow:auto;border:1px solid #ddd;padding:4px}
.legend span{padding:2px 6px;margin-right:6px;border:1px solid #999}
"""


def report(tr, utxos, out, nodot=False):
    rows, sections = [], []
    total = 0
    per_wallet = {}
    have_dot = shutil.which("dot") and not nodot
    for i, r in enumerate(utxos, 1):
        ref = r["ref"]
        tr.reset()
        tr.run([ref])
        n = tr.nodes[ref]
        total += n["val"]
        w = ", ".join(r["_wallets"])
        per_wallet[w] = per_wallet.get(w, 0) + n["val"]
        label = r.get("label") or ""
        ents = tr.entries()
        ent_txt = "<br>".join(
            esc(f"{e[0][:8]}… " + ("; ".join(lb for _, lb, _ in e[1]["found"])
                                    or "(sin label)"))
            for e in ents) or "—"
        frozen = " ❄" if r.get("spendable") is False else ""
        rows.append(
            f"<tr><td><a href='#u{i}'>{i}</a></td><td>{esc(w)}</td>"
            f"<td><code>{esc(ref[:12])}…:{esc(ref.split(':')[1])}</code></td>"
            f"<td class=n>{n['val'] / 1e8:.8f}{frozen}</td>"
            f"<td>{esc(label)}</td><td>{ent_txt}</td></tr>")
        graph = svg_of(tr.dot()) if have_dot else None
        prev = f"<a href='#u{i - 1}'>← anterior</a>" if i > 1 else ""
        nxt = (f"<a href='#u{i + 1}'>siguiente →</a>"
               if i < len(utxos) else "")
        body = (f"<div class=graph>{graph}</div>" if graph else "")
        sections.append(
            f"<section id='u{i}'><nav><a href='#top'>índice</a>{prev}{nxt}"
            f"</nav><h2>{i}. {esc(w)} — {n['val'] / 1e8:.8f} BTC{frozen}</h2>"
            f"<p><code>{esc(ref)}</code><br>{esc(n['addr'] or '')}"
            f"<br>{esc(label)}</p>{body}"
            f"<details{'' if graph else ' open'}><summary>árbol en texto"
            f"</summary><pre>{esc(chr(10).join(tr.lines))}</pre></details>"
            f"</section>")
    wallets = "".join(
        f"<tr><td>{esc(w)}</td><td class=n>{v / 1e8:.8f}</td></tr>"
        for w, v in sorted(per_wallet.items(), key=lambda x: -x[1]))
    legend = ("<p class=legend><span style='background:lightblue'>UTXO"
              "</span><span style='background:gold'>entrada (recibió de"
              " afuera)</span><span style='background:palegreen'>propio con"
              " label</span><span>propio</span><span style='background:"
              "lightgrey'>externo</span></p>")
    doc = (f"<!doctype html><html><head><meta charset=utf-8>"
           f"<title>UTXOs</title><style>{CSS}</style></head><body id=top>"
           f"<h1>{len(utxos)} UTXOs — {total / 1e8:.8f} BTC</h1>"
           f"<table>{wallets}</table>{legend}"
           f"<h2>Índice</h2><table><tr><th>#</th><th>billetera</th>"
           f"<th>UTXO</th><th>BTC</th><th>label</th><th>entradas</th></tr>"
           f"{''.join(rows)}</table>{''.join(sections)}</body></html>")
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    if not have_dot and not nodot:
        print("aviso: sin Graphviz (dot) no hay grafos, solo texto",
              file=sys.stderr)

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

    def server_args(p):
        p.add_argument("-s", "--server",
                       default=os.environ.get("BTCLABELS_SERVER",
                                              "bitcoin.lan:50001"),
                       help="servidor Electrum host:puerto"
                            " (default: $BTCLABELS_SERVER o bitcoin.lan:50001)")
        p.add_argument("--ssl", action="store_true")
        p.add_argument("--insecure", action="store_true",
                       help="no verificar el cert (Fulcrum autofirmado)")
        p.add_argument("--max-depth", type=int, default=15)
        p.add_argument("--max-nodes", type=int, default=300,
                       help="por UTXO en report, total en trace")
        p.add_argument("--all", action="store_true",
                       help="seguir subiendo también por lo externo")

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
    server_args(t)
    t.add_argument("--dot", metavar="ARCHIVO",
                   help="además escribe el grafo en Graphviz (.dot)")
    t.add_argument("refs", nargs="+", metavar="REF",
                   help="txid:vout, txid o dirección")

    r = sub.add_parser("report", help="todos los UTXOs vivos en un HTML")
    labels_arg(r)
    server_args(r)
    r.add_argument("-o", "--out", default="utxos.html")
    r.add_argument("--no-graph", action="store_true",
                   help="solo texto, sin Graphviz")

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
        for rec in recs:
            if needle in rec.get("label", "").lower():
                print(f"{rec['type']:6} {rec['ref']}  {rec['label']}"
                      f"  ({', '.join(rec['_wallets'])})")
        return

    el = Electrum(a.server, a.ssl, a.insecure)
    tr = Tracer(el, recs, a.max_depth, a.max_nodes, a.all)
    if a.cmd == "trace":
        tr.run(a.refs)
        print("\n".join(tr.lines))
        if a.dot:
            with open(a.dot, "w", encoding="utf-8") as fh:
                fh.write(tr.dot())
            print(f"grafo -> {a.dot} ({len(tr.nodes)} nodos)",
                  file=sys.stderr)
        return

    utxos = find_utxos(el, recs)
    print(f"{len(utxos)} UTXOs vivos en"
          f" {len({w for u in utxos for w in u['_wallets']})} billeteras",
          file=sys.stderr)
    report(tr, utxos, a.out, a.no_graph)
    print(f"reporte -> {os.path.abspath(a.out)}", file=sys.stderr)


if __name__ == "__main__":
    main()
