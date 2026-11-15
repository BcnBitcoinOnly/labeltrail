# btc-labels

Merges the BIP-329 labels of many wallets and traces where each UTXO comes
from, querying your own Electrum server (Fulcrum, electrs). A single Python
file, no dependencies.

The problem: when you move coins from one wallet to another, the new wallet
only sees the migration transaction. The origin labels (P2P purchase,
salary, mining) stay in the old wallet and the trail is lost.

## Usage

Export the labels of each wallet into one folder (Sparrow: File > Export >
Labels; old Electrum: Wallet > Labels > Export works too). The file name,
without `-labels`, is used as the wallet name.

    cd labels/
    python3 btclabels.py report -s 10.0.0.5:50001
    python3 btclabels.py find "p2p"
    python3 btclabels.py trace -s 10.0.0.5:50001 bc1q...
    python3 btclabels.py trace -s 10.0.0.5:50001 --dot origin.dot TXID:VOUT
    dot -Tsvg origin.dot -o origin.svg

- `report`: no further input. Takes the outputs from the Sparrow exports
  (which include all of them, labeled or not), asks the server which ones
  are still unspent and builds `utxos.html`: totals per wallet, an index and
  one section per UTXO with its graph (if Graphviz is installed) and the
  text tree. The walk goes up through your own outputs and stops at the
  first external one: that is where the coins entered (yellow in the
  graph). The server can also come from `$BTCLABELS_SERVER`.
- `find TEXT`: searches every label and says which wallet each one comes
  from.
- `trace REF...`: REF is `txid:vout`, a `txid` (all its outputs) or an
  address (its current UTXOs). Same walk as `report`. Limits:
  `--max-depth` (15), `--max-nodes` (300); `--all` keeps walking back
  through external outputs too.
- `--dot`: Graphviz graph. Blue = starting point, yellow = entry from
  outside, green = own with a label, grey = external.
- `merge -o master.jsonl`: a single BIP-329 file without duplicates, to
  import into Sparrow or keep as a backup. Different labels for the same
  reference are joined with ` / `.

`-l PATH` (repeatable) picks files or folders; by default, the current
folder. SSL: `--ssl`, plus `--insecure` if the certificate is self-signed.

## Language

Messages, `--help` and the HTML report are in English by default. Spanish
and Catalan are available with `--lang es` / `--lang ca` (before or after
the subcommand) or `BTCLABELS_LANG=es|ca`. The BIP-329 output of `merge`
is the same in every language.

    python3 btclabels.py --lang ca report
    BTCLABELS_LANG=es python3 btclabels.py --help

## Privacy

- Use your own Electrum server: tracing asks it about every transaction
  and address in the tree.
- The exports, the master file and the `.dot`/`.svg`/`.html` files are a
  map of your coins. Keep them encrypted.

## Tests

    python3 -m unittest discover -s tests

Offline: test transactions in `tests/fixtures/`, BIP-173/350 vectors.
