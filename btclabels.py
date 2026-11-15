#!/usr/bin/env python3
"""BIP-329 labels from many wallets: merge, search and UTXO origin.

  btclabels.py merge [-l DIR] -o master.jsonl
  btclabels.py find  [-l DIR] TEXT
  btclabels.py trace [-l DIR] [-s host:port] [--dot g.dot] REF...
  btclabels.py report [-l DIR] [-s host:port] [-o utxos.html]

-l (repeatable) is a file or a directory; by default, the current
directory. BIP-329 exports (.jsonl: Sparrow and others) and old Electrum
exports (.json, object {txid|address: label}) are accepted. The file
name, without "-labels", is the wallet name.

REF is txid:vout, a txid (all its outputs) or an address (its current
UTXOs). trace queries your own Electrum server (Fulcrum, electrs) and
walks the inputs backwards through your outputs until it reaches external
ones: where the coins came in. report does that for every live UTXO in
the exports and builds an HTML page with an index and a graph per UTXO.

--lang en|es|ca (or $BTCLABELS_LANG) picks the language of messages and
of the report; English by default.
Stdlib only.
"""
import argparse
import hashlib
import html
import json
import os
import re
import shutil
import socket
import ssl
import subprocess
import sys

KEYS = ("tx", "addr", "output", "input", "pubkey", "xpub")
HEX64 = re.compile(r"^[0-9a-fA-F]{64}$")
OUTREF = re.compile(r"^[0-9a-fA-F]{64}:\d+$")

# ---------------------------------------------------------------- i18n

LANGS = ("en", "es", "ca")
LANG = "en"

DOC_ES = """\
Labels BIP-329 de muchas billeteras: merge, búsqueda y origen de UTXOs.

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

--lang en|es|ca (o $BTCLABELS_LANG) elige el idioma de los mensajes y del
reporte; inglés por defecto.
Solo stdlib.
"""

DOC_CA = """\
Etiquetes BIP-329 de moltes carteres: fusió, cerca i origen d'UTXOs.

  btclabels.py merge [-l DIR] -o master.jsonl
  btclabels.py find  [-l DIR] TEXT
  btclabels.py trace [-l DIR] [-s host:port] [--dot g.dot] REF...
  btclabels.py report [-l DIR] [-s host:port] [-o utxos.html]

-l (repetible) és un fitxer o un directori; per defecte, el directori
actual. S'accepten exports BIP-329 (.jsonl: Sparrow i altres) i exports
antics d'Electrum (.json, objecte {txid|adreça: etiqueta}). El nom del
fitxer, sense "-labels", és el nom de la cartera.

REF és txid:vout, un txid (totes les seves sortides) o una adreça (els seus
UTXOs actuals). trace consulta un servidor Electrum propi (Fulcrum, electrs)
i recorre les entrades cap enrere pels teus outputs fins arribar a l'extern:
per on van entrar els diners. report ho fa per a tots els UTXOs vius dels
exports i genera un HTML amb índex i un graf per UTXO.

--lang en|es|ca (o $BTCLABELS_LANG) tria l'idioma dels missatges i de
l'informe; anglès per defecte.
Només stdlib.
"""

MSG = {
    "en": {
        "doc": __doc__,
        # errors and progress
        "no_files": "no label files found (.jsonl/.json)",
        "bad_json": "{path}:{n}: invalid JSON: {err}",
        "no_type_ref": "{path}:{n}: record without type/ref, skipped",
        "conflict": "conflict {typ} {ref}: {a!r} + {b!r}",
        "server_closed": "the server closed the connection",
        "bad_b58": "invalid base58 checksum: {s}",
        "bad_bech32": "invalid bech32 checksum: {s}",
        "bad_addr": "unsupported address: {s}",
        "no_utxos": "{ref}: no UTXOs",
        "merged": "{n} records -> {out} ({c} conflicts)",
        "graph_written": "graph -> {path} ({n} nodes)",
        "live_utxos": "{n} live UTXOs in {w} wallets",
        "report_written": "report -> {path}",
        "no_dot": "warning: without Graphviz (dot) there are no graphs,"
                  " text only",
        # trace tree and graph
        "seen": "(already seen)",
        "external": "external",
        "cut_depth": "... stopped at max depth ({n})",
        "cut_nodes": "... stopped at max nodes ({n})",
        "inputs": "inputs ({n}):",
        # HTML report
        "no_label": "(no label)",
        "prev": "← previous",
        "next": "next →",
        "index": "index",
        "index_title": "Index",
        "text_tree": "text tree",
        "lg_entry": "entry (received from outside)",
        "lg_own_labeled": "own, labeled",
        "lg_own": "own",
        "th_wallet": "wallet",
        "th_label": "label",
        "th_entries": "entry points",
        # CLI help
        "mv_path": "PATH",
        "mv_file": "FILE",
        "h_lang": "language of messages and report: en, es, ca"
                  " (default: $BTCLABELS_LANG or en)",
        "h_labels": "file or directory (repeatable; default: .)",
        "h_server": "Electrum server host:port"
                    " (default: $BTCLABELS_SERVER or 127.0.0.1:50001)",
        "h_ssl": "connect with TLS",
        "h_insecure": "do not verify the certificate (self-signed Fulcrum)",
        "h_max_depth": "maximum depth of the walk (default: 15)",
        "h_max_nodes": "per UTXO in report, total in trace (default: 300)",
        "h_all": "keep walking back through external outputs too",
        "h_merge": "merge everything into a single BIP-329 file",
        "h_out": "output file",
        "h_quiet": "do not list every conflict",
        "h_find": "search labels by text",
        "h_text": "text to search for (case-insensitive)",
        "h_trace": "trace the origin of UTXOs",
        "h_dot": "also write the graph as Graphviz (.dot)",
        "h_refs": "txid:vout, txid or address",
        "h_report": "every live UTXO in one HTML file",
        "h_no_graph": "text only, no Graphviz",
    },
    "es": {
        "doc": DOC_ES,
        "no_files": "no encontré archivos de labels (.jsonl/.json)",
        "bad_json": "{path}:{n}: JSON inválido: {err}",
        "no_type_ref": "{path}:{n}: registro sin type/ref, omitido",
        "conflict": "conflicto {typ} {ref}: {a!r} + {b!r}",
        "server_closed": "el servidor cerró la conexión",
        "bad_b58": "checksum base58 inválido: {s}",
        "bad_bech32": "checksum bech32 inválido: {s}",
        "bad_addr": "dirección no soportada: {s}",
        "no_utxos": "{ref}: sin UTXOs",
        "merged": "{n} registros -> {out} ({c} conflictos)",
        "graph_written": "grafo -> {path} ({n} nodos)",
        "live_utxos": "{n} UTXOs vivos en {w} billeteras",
        "report_written": "reporte -> {path}",
        "no_dot": "aviso: sin Graphviz (dot) no hay grafos, solo texto",
        "seen": "(ya visto)",
        "external": "externo",
        "cut_depth": "... corte por profundidad ({n})",
        "cut_nodes": "... corte por cantidad de nodos ({n})",
        "inputs": "entradas ({n}):",
        "no_label": "(sin label)",
        "prev": "← anterior",
        "next": "siguiente →",
        "index": "índice",
        "index_title": "Índice",
        "text_tree": "árbol en texto",
        "lg_entry": "entrada (recibió de afuera)",
        "lg_own_labeled": "propio con label",
        "lg_own": "propio",
        "th_wallet": "billetera",
        "th_label": "label",
        "th_entries": "entradas",
        "mv_path": "RUTA",
        "mv_file": "ARCHIVO",
        "h_lang": "idioma de mensajes y reporte: en, es, ca"
                  " (default: $BTCLABELS_LANG o en)",
        "h_labels": "archivo o directorio (repetible; default: .)",
        "h_server": "servidor Electrum host:puerto"
                    " (default: $BTCLABELS_SERVER o 127.0.0.1:50001)",
        "h_ssl": "conectar con TLS",
        "h_insecure": "no verificar el certificado (Fulcrum autofirmado)",
        "h_max_depth": "profundidad máxima del recorrido (default: 15)",
        "h_max_nodes": "por UTXO en report, total en trace (default: 300)",
        "h_all": "seguir subiendo también por lo externo",
        "h_merge": "junta todo en un BIP-329 único",
        "h_out": "archivo de salida",
        "h_quiet": "no listar cada conflicto",
        "h_find": "busca labels por texto",
        "h_text": "texto a buscar (sin distinguir mayúsculas)",
        "h_trace": "rastrea el origen de UTXOs",
        "h_dot": "además escribe el grafo en Graphviz (.dot)",
        "h_refs": "txid:vout, txid o dirección",
        "h_report": "todos los UTXOs vivos en un HTML",
        "h_no_graph": "solo texto, sin Graphviz",
    },
    "ca": {
        "doc": DOC_CA,
        "no_files": "no he trobat fitxers d'etiquetes (.jsonl/.json)",
        "bad_json": "{path}:{n}: JSON no vàlid: {err}",
        "no_type_ref": "{path}:{n}: registre sense type/ref, omès",
        "conflict": "conflicte {typ} {ref}: {a!r} + {b!r}",
        "server_closed": "el servidor ha tancat la connexió",
        "bad_b58": "checksum base58 no vàlid: {s}",
        "bad_bech32": "checksum bech32 no vàlid: {s}",
        "bad_addr": "adreça no suportada: {s}",
        "no_utxos": "{ref}: sense UTXOs",
        "merged": "{n} registres -> {out} ({c} conflictes)",
        "graph_written": "graf -> {path} ({n} nodes)",
        "live_utxos": "{n} UTXOs vius en {w} carteres",
        "report_written": "informe -> {path}",
        "no_dot": "avís: sense Graphviz (dot) no hi ha grafs, només text",
        "seen": "(ja vist)",
        "external": "extern",
        "cut_depth": "... aturat per profunditat ({n})",
        "cut_nodes": "... aturat per nombre de nodes ({n})",
        "inputs": "entrades ({n}):",
        "no_label": "(sense etiqueta)",
        "prev": "← anterior",
        "next": "següent →",
        "index": "índex",
        "index_title": "Índex",
        "text_tree": "arbre en text",
        "lg_entry": "entrada (rebut de fora)",
        "lg_own_labeled": "propi amb etiqueta",
        "lg_own": "propi",
        "th_wallet": "cartera",
        "th_label": "etiqueta",
        "th_entries": "entrades",
        "mv_path": "RUTA",
        "mv_file": "FITXER",
        "h_lang": "idioma dels missatges i de l'informe: en, es, ca"
                  " (per defecte: $BTCLABELS_LANG o en)",
        "h_labels": "fitxer o directori (repetible; per defecte: .)",
        "h_server": "servidor Electrum host:port"
                    " (per defecte: $BTCLABELS_SERVER o 127.0.0.1:50001)",
        "h_ssl": "connectar amb TLS",
        "h_insecure": "no verificar el certificat (Fulcrum autosignat)",
        "h_max_depth": "profunditat màxima del recorregut (per defecte: 15)",
        "h_max_nodes": "per UTXO a report, total a trace (per defecte: 300)",
        "h_all": "continuar pujant també per l'extern",
        "h_merge": "ajunta-ho tot en un únic BIP-329",
        "h_out": "fitxer de sortida",
        "h_quiet": "no llistar cada conflicte",
        "h_find": "cerca etiquetes per text",
        "h_text": "text a cercar (sense distingir majúscules)",
        "h_trace": "rastreja l'origen d'UTXOs",
        "h_dot": "a més escriu el graf en Graphviz (.dot)",
        "h_refs": "txid:vout, txid o adreça",
        "h_report": "tots els UTXOs vius en un HTML",
        "h_no_graph": "només text, sense Graphviz",
    },
}


def set_lang(lang):
    global LANG
    if lang not in LANGS:
        raise ValueError(f"unsupported language: {lang}")
    LANG = lang


def t(key, **kw):
    """Message `key` in the current language, formatted with `kw`."""
    s = MSG[LANG].get(key, MSG["en"][key])
    return s.format(**kw) if kw else s

# ------------------------------------------------------------- reading


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
        sys.exit(t("no_files"))
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
                sys.exit(t("bad_json", path=path, n=n, err=e))
            if r.get("type") not in KEYS or "ref" not in r:
                print(t("no_type_ref", path=path, n=n), file=sys.stderr)
                continue
            recs.append(r)
    w = wallet_name(path)
    for r in recs:
        r["_wallet"] = w
    return recs


def merge(paths, quiet=False, skip=()):
    """Merge by (type, ref). Returns (records, conflicts)."""
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
                        print(t("conflict", typ=k[0], ref=k[1], a=a, b=b),
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
                raise RuntimeError(t("server_closed"))
            resp = json.loads(line)
            if resp.get("id") == self.i:
                break
        if resp.get("error"):
            raise RuntimeError(f"{method}: {resp['error']}")
        return resp["result"]

    def call_many(self, method, params_list, chunk=100):
        """Pipelining: send `chunk` requests at a time, then read replies."""
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
                    raise RuntimeError(t("server_closed"))
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

# ------------------------------------------------- script <-> address

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
        raise ValueError(t("bad_b58", s=s))
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
        raise ValueError(t("bad_bech32", s=addr))
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
    raise ValueError(t("bad_addr", s=addr))


def scripthash(script):
    return hashlib.sha256(script).digest()[::-1].hex()

# --------------------------------------------------------------- trace


class Tracer:
    """Walks backwards from UTXOs.

    "Own" = an output or address that shows up in some export (Sparrow
    exports every output and address, labeled or not). The walk goes up
    through own outputs and stops at external ones: that is where the
    coins entered your wallets.
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
        """txid:vout | txid | address -> list of txid:vout."""
        if OUTREF.match(ref):
            return [ref.lower()]
        if HEX64.match(ref):
            _, outs, _ = self.tx(ref.lower())
            return [f"{ref.lower()}:{i}" for i in range(len(outs))]
        sh = scripthash(addr_script(ref))
        utxos = self.el.call("blockchain.scripthash.listunspent", [sh])
        if not utxos:
            print(t("no_utxos", ref=ref), file=sys.stderr)
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
            self.emit(depth, f"{ref} {t('seen')}")
            return
        self.seen.add(ref)
        n = self.info(ref)
        self.nodes[ref] = n
        who = (f"  <{', '.join(n['own'])}>" if n["own"]
               else f"  <{t('external')}>")
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
            self.emit(depth + 1, t("cut_depth", n=self.max_depth))
            return
        self.prefetch(tx for tx, _ in n["ins"])
        self.emit(depth + 1, t("inputs", n=len(n["ins"])))
        for ptx, pvout in n["ins"]:
            if len(self.nodes) >= self.max_nodes:
                self.emit(depth + 1, t("cut_nodes", n=self.max_nodes))
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
                    self.emit(0, t("cut_nodes", n=self.max_nodes))
                    return
                self.starts.append(start)
                self.walk(start, 0)
                self.lines.append("")

    def entries(self):
        """Own nodes that received from outside (or coinbase)."""
        return [(ref, n) for ref, n in self.nodes.items()
                if n["own"] and (n["entry"] or n["coinbase"])]

    def dot(self):
        def q(s):
            return '"' + str(s).replace("\\", "\\\\").replace('"', '\\"') + '"'

        def ql(s):
            return q(s)[:-1].replace("\n", "\\l") + '\\l"'

        lines = ["digraph utxo {", "  rankdir=LR;",
                 ('  node [shape=box, style="rounded,filled",'
                  ' fontname="monospace", fontsize=10];')]
        for ref, n in self.nodes.items():
            txid, vout = ref.split(":")
            text = [f"{txid[:8]}…{txid[-4:]}:{vout}",
                    f"{n['val'] / 1e8:.8f} BTC"]
            if n["addr"]:
                text.append(n["addr"][:12] + "…" + n["addr"][-6:])
            text.append(f"<{', '.join(n['own'])}>" if n["own"]
                        else f"<{t('external')}>")
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
    """Outputs and addresses that appear in the exports -> wallets."""
    own_out, own_addr = {}, {}
    for r in recs:
        if r["type"] == "output":
            own_out[r["ref"]] = r.get("_wallets", [])
        elif r["type"] == "addr":
            own_addr[r["ref"]] = r.get("_wallets", [])
    return own_out, own_addr


def find_utxos(el, recs):
    """Live UTXOs among the exported outputs.

    The address comes from the addr record with the same origin+keypath;
    if there is none, from the transaction. Then listunspent per address.
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
                                    or t("no_label")))
            for e in ents) or "—"
        frozen = " ❄" if r.get("spendable") is False else ""
        rows.append(
            f"<tr><td><a href='#u{i}'>{i}</a></td><td>{esc(w)}</td>"
            f"<td><code>{esc(ref[:12])}…:{esc(ref.split(':')[1])}</code></td>"
            f"<td class=n>{n['val'] / 1e8:.8f}{frozen}</td>"
            f"<td>{esc(label)}</td><td>{ent_txt}</td></tr>")
        graph = svg_of(tr.dot()) if have_dot else None
        prev = f"<a href='#u{i - 1}'>{t('prev')}</a>" if i > 1 else ""
        nxt = (f"<a href='#u{i + 1}'>{t('next')}</a>"
               if i < len(utxos) else "")
        body = (f"<div class=graph>{graph}</div>" if graph else "")
        sections.append(
            f"<section id='u{i}'><nav><a href='#top'>{t('index')}</a>"
            f"{prev}{nxt}</nav>"
            f"<h2>{i}. {esc(w)} — {n['val'] / 1e8:.8f} BTC{frozen}</h2>"
            f"<p><code>{esc(ref)}</code><br>{esc(n['addr'] or '')}"
            f"<br>{esc(label)}</p>{body}"
            f"<details{'' if graph else ' open'}><summary>{t('text_tree')}"
            f"</summary><pre>{esc(chr(10).join(tr.lines))}</pre></details>"
            f"</section>")
    wallets = "".join(
        f"<tr><td>{esc(w)}</td><td class=n>{v / 1e8:.8f}</td></tr>"
        for w, v in sorted(per_wallet.items(), key=lambda x: -x[1]))
    legend = (f"<p class=legend><span style='background:lightblue'>UTXO"
              f"</span><span style='background:gold'>{t('lg_entry')}</span>"
              f"<span style='background:palegreen'>{t('lg_own_labeled')}"
              f"</span><span>{t('lg_own')}</span><span style='background:"
              f"lightgrey'>{t('external')}</span></p>")
    doc = (f"<!doctype html><html lang={LANG}><head><meta charset=utf-8>"
           f"<title>UTXOs</title><style>{CSS}</style></head><body id=top>"
           f"<h1>{len(utxos)} UTXOs — {total / 1e8:.8f} BTC</h1>"
           f"<table>{wallets}</table>{legend}"
           f"<h2>{t('index_title')}</h2><table><tr><th>#</th>"
           f"<th>{t('th_wallet')}</th><th>UTXO</th><th>BTC</th>"
           f"<th>{t('th_label')}</th><th>{t('th_entries')}</th></tr>"
           f"{''.join(rows)}</table>{''.join(sections)}</body></html>")
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    if not have_dot and not nodot:
        print(t("no_dot"), file=sys.stderr)

# ----------------------------------------------------------------- cli


def write_bip329(recs, path):
    with open(path, "w", encoding="utf-8") as f:
        for r in recs:
            r = {k: v for k, v in r.items() if not k.startswith("_")}
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def pick_lang(argv):
    """--lang from argv (anywhere) or $BTCLABELS_LANG; English by default.

    Resolved before building the parser so that --help is translated too.
    """
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument("--lang", choices=LANGS,
                     default=os.environ.get("BTCLABELS_LANG") or "en")
    a, _ = pre.parse_known_args(argv)
    return a.lang


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    set_lang(pick_lang(argv))

    # --lang is accepted before or after the subcommand; SUPPRESS keeps the
    # subparser from overwriting the value with its default.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--lang", choices=LANGS, default=argparse.SUPPRESS,
                        help=t("h_lang"))

    ap = argparse.ArgumentParser(
        description=t("doc"), formatter_class=argparse.RawTextHelpFormatter,
        parents=[common])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def labels_arg(p):
        p.add_argument("-l", "--labels", action="append",
                       metavar=t("mv_path"), help=t("h_labels"))

    def server_args(p):
        p.add_argument("-s", "--server",
                       default=os.environ.get("BTCLABELS_SERVER",
                                              "127.0.0.1:50001"),
                       help=t("h_server"))
        p.add_argument("--ssl", action="store_true", help=t("h_ssl"))
        p.add_argument("--insecure", action="store_true",
                       help=t("h_insecure"))
        p.add_argument("--max-depth", type=int, default=15,
                       help=t("h_max_depth"))
        p.add_argument("--max-nodes", type=int, default=300,
                       help=t("h_max_nodes"))
        p.add_argument("--all", action="store_true", help=t("h_all"))

    m = sub.add_parser("merge", help=t("h_merge"), parents=[common])
    labels_arg(m)
    m.add_argument("-o", "--out", required=True, help=t("h_out"))
    m.add_argument("-q", "--quiet", action="store_true", help=t("h_quiet"))

    f = sub.add_parser("find", help=t("h_find"), parents=[common])
    labels_arg(f)
    f.add_argument("text", help=t("h_text"))

    tp = sub.add_parser("trace", help=t("h_trace"), parents=[common])
    labels_arg(tp)
    server_args(tp)
    tp.add_argument("--dot", metavar=t("mv_file"), help=t("h_dot"))
    tp.add_argument("refs", nargs="+", metavar="REF", help=t("h_refs"))

    r = sub.add_parser("report", help=t("h_report"), parents=[common])
    labels_arg(r)
    server_args(r)
    r.add_argument("-o", "--out", default="utxos.html", help=t("h_out"))
    r.add_argument("--no-graph", action="store_true", help=t("h_no_graph"))

    a = ap.parse_args(argv)
    paths = a.labels or ["."]

    if a.cmd == "merge":
        recs, conflicts = merge(paths, a.quiet, skip=[a.out])
        write_bip329(recs, a.out)
        print(t("merged", n=len(recs), out=a.out, c=conflicts),
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
            print(t("graph_written", path=a.dot, n=len(tr.nodes)),
                  file=sys.stderr)
        return

    utxos = find_utxos(el, recs)
    print(t("live_utxos", n=len(utxos),
            w=len({w for u in utxos for w in u["_wallets"]})),
          file=sys.stderr)
    report(tr, utxos, a.out, a.no_graph)
    print(t("report_written", path=os.path.abspath(a.out)), file=sys.stderr)


if __name__ == "__main__":
    main()
