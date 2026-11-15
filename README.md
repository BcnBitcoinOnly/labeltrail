# btc-labels

Junta las labels BIP-329 de muchas billeteras y rastrea de dónde viene cada
UTXO, consultando tu propio servidor Electrum (Fulcrum, electrs). Un solo
archivo Python, sin dependencias.

El problema: cuando movés coins de una billetera a otra, la nueva solo ve la
transacción de migración. Las labels de origen (compra P2P, sueldo, minado)
quedan en la billetera vieja y se pierde el rastro.

## Uso

Exportá las labels de cada billetera a una carpeta (Sparrow: File > Export >
Labels; Electrum viejo: Wallet > Labels > Export también sirve). El nombre del
archivo, sin `-labels`, se usa como nombre de la billetera.

    cd labels/
    python3 btclabels.py report -s 10.0.0.5:50001
    python3 btclabels.py find "p2p"
    python3 btclabels.py trace -s 10.0.0.5:50001 bc1q...
    python3 btclabels.py trace -s 10.0.0.5:50001 --dot origen.dot TXID:VOUT
    dot -Tsvg origen.dot -o origen.svg

- `report`: sin más input. Toma los outputs de los exports de Sparrow (que
  incluyen todos, con o sin label), le pregunta al servidor cuáles siguen sin
  gastar y arma `utxos.html`: totales por billetera, índice y una sección por
  UTXO con su grafo (si está Graphviz) y el árbol en texto. Se sube por tus
  propios outputs y se corta en el primero externo: ese es el punto por donde
  entró la plata (amarillo en el grafo). El servidor también sale de
  `$BTCLABELS_SERVER`.
- `find TEXTO`: busca en todas las labels y dice de qué billetera sale cada una.
- `trace REF...`: REF es `txid:vout`, un `txid` (todas sus salidas) o una
  dirección (sus UTXOs actuales). Mismo recorrido que `report`. Cortes:
  `--max-depth` (15), `--max-nodes` (300); `--all` sigue subiendo también por
  lo externo.
- `--dot`: grafo Graphviz. Azul = punto de partida, amarillo = entrada desde
  afuera, verde = propio con label, gris = externo.
- `merge -o master.jsonl`: un BIP-329 único y sin duplicados, para importar en
  Sparrow o guardar como backup. Labels distintas para la misma referencia se
  unen con ` / `.

`-l RUTA` (repetible) elige archivos o carpetas; por defecto, la carpeta
actual. TLS: `--ssl` valida el certificado como un navegador. Para un Fulcrum
con certificado autofirmado, la primera vez `--insecure` imprime su SHA-256;
de ahí en adelante `--fingerprint <sha256>` (o `$BTCLABELS_FINGERPRINT`) lo
fija y corta si cambia.

## Privacidad

- Usá tu propio servidor Electrum: el rastreo le pregunta por cada
  transacción y dirección del árbol.
- Los exports, el master y los `.dot`/`.svg` son el mapa de tus coins.
  Guardalos cifrados.

## Tests

    python3 -m unittest discover -s tests

Offline: transacciones de prueba en `tests/fixtures/`, vectores de BIP-173/350.
