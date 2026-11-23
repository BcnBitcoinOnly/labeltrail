# labeltrail

**Follow every coin back to where it came from.**

You hold a UTXO today. Did it come from a KYC exchange, a P2P trade, a
payment from a friend? You knew once and wrote it down as a label, but in
a wallet you stopped using two migrations ago.

labeltrail reads the label exports of all your wallets, old and new. It
asks your own Electrum server which coins you still hold, then follows each
one back through your own transactions to the point where it entered your
wallets. You get one HTML page that lists every UTXO with its amount, the
labels found along the way, the wallet each label came from and a graph of
its history.

It is one Python file and uses only the standard library. It talks only to
the server you point it at.

## The problem

Labels stay with the wallet where you wrote them. Coins don't stay put:

    Old Trezor                Sparrow hot wallet        Coldcard
    ──────────                ──────────────────        ────────
    "Bisq buy, no KYC"  ──▶   (no label)  ──consolidate──▶  UTXO ?
    "Kraken withdrawal" ──▶   (no label)  ──────┘

Each hop creates a transaction that the next wallet can see, but the
original label stays in the old wallet. A year later the Coldcard shows a
UTXO, and nothing tells you that it mixes KYC and non-KYC coins.

You need that history for:

- **Privacy**: don't spend KYC and non-KYC coins together without knowing
  it.
- **Taxes**: when and how you acquired each coin.
- **Inheritance and audits**: someone else should be able to understand
  your stash without asking you.

The labels still exist in your old wallets and the transactions are on
the chain. labeltrail joins the two.

## How it works

1. **Read every export.** Each file in the folder is one wallet; its name,
   minus `-labels`, becomes the wallet name. Sparrow's BIP-329 export lists
   every address and output of the wallet, labeled or not. Electrum's label
   files (`{txid or address: label}`) work too.
2. **Find the coins you hold now.** Every known address is checked with
   `listunspent` on your server. That includes coins received after you
   made the export, as long as they landed on an address the export lists.
3. **Walk back.** From each UTXO it follows the inputs of the transaction
   that created it. While an input belongs to any of your wallets (any
   export, not only the one holding the UTXO) it keeps going back. It
   stops at the first output that isn't yours: the **entry point**, where
   those coins came in from outside. A coinbase is an entry point too.
4. **Collect labels.** Every node on the way gets its output, address and
   transaction labels, each with the wallet it came from.

The report has one section per UTXO with a graph:

| color | meaning |
| --- | --- |
| blue | the UTXO you hold |
| gold | entry point: yours, received from outside |
| green | yours, labeled |
| white | yours, no label |
| grey | external |

The labels on the gold nodes usually answer the question. The index at the
top of the report lists them for every UTXO.

## Requirements

- Python 3, standard library only. Tested on 3.13.
- An Electrum server you control, such as Fulcrum or electrs. Umbrel,
  StartOS, RaspiBlitz and similar node distributions ship one.
- Optional: Graphviz (`dot`) for the graphs. Without it the report has a
  text tree for each UTXO.

## Quick start

1. **Export the labels of every wallet into one folder.** Include old,
   empty wallets: they hold the labels you want to recover.
   - Sparrow: open the wallet, wait until it has synced, then *File →
     Export Wallet → Labels*. A file exported before the sync finishes is
     missing outputs.
   - Electrum: *Wallet → Labels → Export*.
2. **Get `labeltrail.py`** from this repository (download the file or clone
   the repo).
3. **Run the report against your server:**

       cd my-labels/
       python3 /path/to/labeltrail.py report -s mynode.local:50002 --insecure
       xdg-open utxos.html

   With `--insecure` it connects without verifying the certificate and
   prints its SHA-256; node servers usually have a self-signed one. Pin it,
   so later runs refuse any other certificate:

       export LABELTRAIL_SERVER=mynode.local:50002
       export LABELTRAIL_FINGERPRINT=<sha256>
       python3 /path/to/labeltrail.py report

4. **Check that nothing is missing:**

       python3 /path/to/labeltrail.py check

   For each wallet, `check` compares the balance in the export with the
   coins found on the server. They differ when the wallet received or spent
   after the export: re-export it from Sparrow once it has synced. If they
   still differ, please open an issue.

## Commands

- `report`: writes every live UTXO to one HTML page (`-o`, default
  `utxos.html`): totals per wallet, an index with the entry points, and one
  section per UTXO with its graph and text tree.
- `trace REF...`: runs the same walk for specific coins and prints it as a
  tree. `REF` is `txid:vout`, a `txid` (all its outputs) or an address (its
  current UTXOs). `--dot file.dot` writes the graph.
- `find TEXT`: searches every label in every wallet and shows which wallet
  each match comes from.
- `merge -o ../all.jsonl`: writes one deduplicated BIP-329 file from all
  exports, for backup or to import back into Sparrow. Conflicting labels
  are joined with ` / `.
- `check`: per wallet, the balance in the export against the live coins
  found.

Write the `merge` output **outside** the exports folder. Inside, later runs
read it as one more wallet; `merge` warns about it.

Common options:

- `-l PATH`: export file or folder, repeatable. Default: current folder.
- `-s HOST:PORT`, `--ssl`, `--insecure`, `--fingerprint SHA256`: server and
  TLS. `--insecure` and `--fingerprint` imply TLS. `LABELTRAIL_SERVER` and
  `LABELTRAIL_FINGERPRINT` set the defaults.
- `--max-depth` (15) and `--max-nodes` (300) limit each walk, so a coinjoin
  doesn't blow up the graph.
- `--all`: keep walking through external outputs too. Expect large graphs.
- `--no-graph`: text trees only.

## Privacy

labeltrail talks only to the server you give it, and that server sees every
address and transaction in your history.

- **Use your own server.** A public one learns your whole stash, linked to
  your IP.
- Keep it on your LAN, or reach it over a VPN or Tor. Don't expose it to
  the internet.
- The exports, the `merge` output, the `.dot`/`.svg` files and the HTML
  report are a complete map of your coins. Keep them on encrypted storage
  and delete what you don't need.

## Limits

- A wallet you never exported looks external, so the walk stops there.
  Export it and the trail continues.
- Electrum label files list only what you labeled. Unlabeled Electrum
  addresses look external, and the coins on them are not found.
- A coin on an address that is in no export is not found. That happens
  when it went beyond the addresses Sparrow had derived at export time:
  re-export after receiving.
- Coinjoins: the other participants' inputs are external, so the walk
  stops at the mix, which is usually what you want. Label your premix
  outputs before mixing.
- P2PK outputs (very old coins) have no address and can't be looked up.

## How it compares

- **Sparrow** keeps labels per wallet and imports BIP-329. It doesn't follow
  a coin across wallets. The `merge` output can be imported into Sparrow to
  bring old labels into your current wallet.
- **[am-i.exposed](https://am-i.exposed)** scores a transaction or address
  from the outside: change detection, common-input ownership, wallet
  fingerprints, a grade from A+ to F. It answers *what can a chain analyst
  infer?* labeltrail answers *what do I know about this coin?* from your
  own labels. They complement each other: before spending, labeltrail tells
  you whether you are mixing KYC and non-KYC coins, and am-i.exposed how
  the transaction will look to an analyst.

## Language

Messages, `--help` and the report are in English by default. Spanish and
Catalan are available with `--lang es` or `--lang ca` (before or after the
subcommand), or with `LABELTRAIL_LANG`. The `merge` output is the same in
every language.

    python3 labeltrail.py --lang es report

## Tests

    python3 -m unittest discover -s tests

Offline, against real mainnet transactions in `tests/fixtures/`. Address
encoding is checked with the BIP-173 and BIP-350 test vectors.

## License

GPL-3.0, see [LICENSE](LICENSE).
