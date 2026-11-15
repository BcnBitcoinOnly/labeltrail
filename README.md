# labeltrail

**Follow every coin back to where it came from.**

labeltrail reads the BIP-329 label exports of all your wallets, past and
present. It asks *your own* Electrum server (Fulcrum, electrs) which coins
are still unspent, then walks each UTXO backwards through your own
transactions until it reaches the point where the money entered your
wallets. The result is one offline HTML page with every UTXO, its amount, its
labels and a graph of its history.

One Python file, standard library only. Nothing leaves your network.

## Why

Labels die when coins move. You label a P2P purchase in one wallet, migrate
to a new hardware wallet, consolidate, maybe pass through a multisig. Every
hop creates a transaction the new wallet can see, but the *original* label
(KYC or not, who paid you, what it was for) stays behind in a wallet you no
longer open. A year later you have a UTXO and no idea what it is.

That matters for:

- **Privacy**: knowing which coins are KYC and which aren't before you spend
  them together.
- **Taxes**: acquisition date and origin of each coin.
- **Inheritance and audits**: someone else should be able to read your stash
  without your memory.

labeltrail rebuilds that chain from data you already have.

## How it works

1. **Collect.** Every export in the folder is read. Sparrow exports every
   output and address of a wallet, labeled or not, with its keypath. Old
   Electrum label files (`{txid|address: label}`) work too. The file name,
   minus `-labels`, becomes the wallet name.
2. **Find live coins.** Every address in the exports is checked against the
   server with `listunspent`, so only the coins you hold now show up, even
   those received after the export was made.
3. **Walk back.** From each UTXO it follows the inputs. While an output
   belongs to any of your wallets it keeps climbing, across wallets. It stops
   at the first output that is not yours: the **entry point**, where the coins
   came in from outside (or a coinbase).
4. **Report.** Labels found along the way (output, address and transaction
   labels, and which wallet each came from) are attached to every node.

Graph colors:

| color | meaning |
| --- | --- |
| blue | the UTXO you hold |
| gold | entry point: yours, received from outside |
| green | yours, labeled |
| white | yours, no label |
| grey | external |

## Quick start

Export the labels of every wallet into one folder. In Sparrow, open each
wallet and use *File → Export Wallet → Labels*. Do it for old, empty wallets
too: their labels are the ones you want to recover.

    cd my-labels/
    python3 labeltrail.py report -s mynode.local:50002 --insecure
    xdg-open utxos.html

The first run with `--insecure` prints the server certificate's SHA-256. Pin
it so later runs refuse anything else, and set the server once:

    export LABELTRAIL_SERVER=mynode.local:50002
    export LABELTRAIL_FINGERPRINT=<sha256>
    python3 labeltrail.py report

Install Graphviz (`dot`) for graphs. Without it the report still has a text
tree per UTXO.

## Commands

- `report`: all live UTXOs in one HTML page with totals per wallet, an
  index, and one section per UTXO with its graph and text tree.
- `trace REF...`: the same walk for specific coins, printed as a tree. `REF`
  is `txid:vout`, a `txid` (all its outputs) or an address (its current
  UTXOs). `--dot file.dot` writes the graph.
- `find TEXT`: search every label in every wallet and show which wallet it
  lives in.
- `merge -o ../all.jsonl`: one deduplicated BIP-329 file from all exports,
  for backup or to import into Sparrow. Different labels for the same item
  are joined with ` / `. Write it outside the exports folder: inside, later
  runs read it back as one more wallet (`merge` warns about it).
- `check`: per wallet, the balance according to the export against the live
  UTXOs found. A difference means the export is stale: re-export it from
  Sparrow once it has synced.

Common options:

- `-l PATH`: export file or folder, repeatable. Default: current folder.
- `-s HOST:PORT`, `--ssl`, `--insecure`, `--fingerprint SHA256`: server and
  TLS. `--fingerprint` and `--insecure` imply TLS.
- `--max-depth` (15), `--max-nodes` (300): limits per walk, so a coinjoin
  doesn't blow up the graph. `--all` keeps climbing through external outputs.

## Language

Messages, `--help` and the HTML report are in English by default. Spanish
and Catalan are available with `--lang es` / `--lang ca` (before or after
the subcommand) or `LABELTRAIL_LANG=es|ca`. The BIP-329 output of `merge`
is the same in every language.

    python3 labeltrail.py --lang ca report
    LABELTRAIL_LANG=es python3 labeltrail.py --help

## Privacy

- **Use your own server.** The walk asks the server about every transaction
  and address in your history; a public server would learn your whole stash.
- Keep that server on your LAN, so the queries never leave your network.
- The exports, the `merge` output, `.dot`/`.svg` files and the HTML report
  are a map of your coins. Store them encrypted and delete what you don't
  keep.

## Limits

- A wallet you never exported looks external, so the walk stops there.
  Export it and the trail continues.
- Electrum label files have no output list: only the addresses you labeled
  count as yours. Unlabeled Electrum addresses look external.
- Coinjoins: the other participants' inputs are external, so the walk stops
  at the mix. Label your premix outputs.
- P2PK outputs (very old coins) have no address and can't be looked up.

## Tests

    python3 -m unittest discover -s tests

Offline. Real transactions in `tests/fixtures/`, address vectors from BIP-173
and BIP-350.

## License

GPL-3.0, see [LICENSE](LICENSE).
