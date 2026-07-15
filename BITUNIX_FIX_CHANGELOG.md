# Bitunix Live-Trading Fix (Codes 30016 & 30027)

Alle Änderungen in **`backend/services/bitunix_trade.py`** – nichts anderes berührt.

## Was war kaputt?

Aus deinen Telegram-Screenshots:

1. **`code 30016: The amount should be larger than 0.0001 BTC`**
   → Ordermenge kam als **`0`** bei Bitunix an.
2. **`code 30027: TP price must be greater than mark price: 77.29`**
   → Take-Profit lag auf oder unter dem Mark-Preis.

## Root Cause

`load_trading_pairs()` hat die Felder `basePrecision` / `quotePrecision`
von Bitunix als **Step-Size** interpretiert. Bitunix liefert dort aber
**Dezimalstellen** (Integer: `3`, `4`, `5` …).

Folge: `_round_step(0.0154, 3.0)` → `(0.0154 / 3.0).floor() * 3.0 = 0`.
Die Ordermenge wurde also auf `0` abgerundet → **30016**.
Für Preise passierte dasselbe → TP landete auf `0` oder gerundet unter dem
Mark-Price → **30027**.

Zusätzlich: Bei sehr kleinem Risk (z. B. `0.07 %`, siehe SOLUSDT / XRPUSDT im
Screenshot) rutscht der Mark-Price zwischen Signal und Order-Ausführung
schon über den TP → ebenfalls **30027**.

## Fixes

| # | Was | Wo |
|---|-----|----|
| 1 | Neue Hilfsfunktion **`_precision_to_step()`** – erkennt automatisch, ob Bitunix Dezimalstellen (3 → 0.001) oder eine echte Step-Size (0.001) liefert. | `bitunix_trade.py` |
| 2 | `load_trading_pairs()` benutzt jetzt `_precision_to_step()` für `qty_step` und `price_tick`. | `load_trading_pairs()` |
| 3 | `_round_step()` respektiert die Step-Präzision explizit (kein `.normalize()` → kein "1.11" statt "1.1100"), rounding-mode ist parametrisierbar. | `_round_step()` |
| 4 | `_fmt_qty()` **erzwingt `min_qty`** – wenn die berechnete Menge kleiner ist, wird auf das Bitunix-Minimum hochgesetzt (kein 30016 mehr durch Rounding-to-Zero). | `_fmt_qty()` |
| 5 | `_fmt_price()` bekommt **direction-aware rounding**: LONG-TP rundet UP, LONG-SL rundet DOWN, SHORT umgekehrt. So kann Tick-Rounding den TP nie unter den Mark-Price schieben. | `_fmt_price()` |
| 6 | `place_order()` ruft `_fmt_price()` mit passender Richtung auf. | `place_order()` |
| 7 | Neue Methode **`get_mark_price()`** – holt live den Mark-Price über `GET /api/v1/futures/market/tickers`. | `BitunixTradeClient` |
| 8 | `AutoTradeManager._levels()` erzwingt einen **Mindest-Risk** (`min_risk_percent`, default **0.25 %**). So kann Risk nie unter dem sein, was Bitunix Slippage sicher erlaubt. | `_levels()` |
| 9 | `AutoTradeManager.on_signal()` re-aligned TP/SL kurz vor dem Order-Placement gegen den **aktuellen Mark-Price** (`min_tp_distance_percent`, default **0.15 %**). Wenn der Markt schon durch TP gelaufen ist, wird TP nach vorne geschoben statt Order abzulehnen. | `on_signal()` |
|10 | Kapital-Guard: wenn `qty < min_qty` und Kapital nicht reicht, wird der Trade **sauber übersprungen** mit Telegram-Meldung (statt Bitunix mit 30016 abzulehnen). | `on_signal()` |

## Neue Config-Keys (mit Defaults, alte Configs bleiben kompatibel)

```python
DEFAULT_COIN_CFG = {
    ...
    "min_tp_distance_percent": 0.15,  # min Abstand TP/SL vom Mark-Price (%)
    "min_risk_percent":       0.25,   # Floor fuer Risk (% vom Entry)
}
```

Kannst du pro Coin in deiner Trading-Config überschreiben, falls du für
bestimmte Symbole engere / weitere Werte willst.

## Verifikation

```
python -c "from services.bitunix_trade import _round_step, _precision_to_step
from decimal import ROUND_DOWN
assert _precision_to_step(3) == 0.001
assert _round_step(0.0154, 0.001, ROUND_DOWN) == '0.015'  # war vorher '0'
print('OK')"
```

## Deployment

Kein Datenbank-Migrationsschritt nötig. Einfach:

```
git pull        # oder Dateien aus dem ZIP übernehmen
sudo supervisorctl restart backend
```

Die nächsten Live-Orders sollten die Fehler 30016 / 30027 nicht mehr sehen.
Sollte doch mal 30027 auftauchen, in der Coin-Config `min_tp_distance_percent`
z. B. auf `0.25` hochziehen.
