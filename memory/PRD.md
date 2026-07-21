# PRD – Crypto Scanner / Daytrading Website (extern gehostet, Render + eigenes Frontend)

## Original-Problemstellung (Juni 2026)
Bestehende funktionierende Daytrading-Website (GitHub: dean06greif-ai/CryptoParameterOptimizer1) verbessern:
längere Zeiträume (60/90/180/360 Tage) überall, Abbruch-Button + persistenter Fortschritt (%, Kombination, Restzeit),
Backtests schneller & 100% akkurat ohne Selbst-Abbruch, Bayes'sche Optimierung, bestehende Strategien weiterentwickeln,
optimierte Parameter in Backtester übertragen, Zeitfenster pro Strategie (auch im Backtester), Break-Even konfigurierbar
(TP1/CRV/Gewinn-%/Smart/aus), Bugs: Min-Rel-Volumen ohne Wirkung, Trades bei 3/5 Regeln, Gewinnsicherung unsichtbar/wirkungslos.
Infra: Render Free (512MB/0.1 CPU) – prüfen ob Code oder Infra der Flaschenhals ist.

## Architektur
- Frontend: React (CRA/craco), Komponenten unter frontend/src/components (Backtester.js, Optimizer.js, SettingsPanel.js, StrategyAutoTradeModal.js, StrategyBuilder.js ...)
- Backend: FastAPI (backend/server.py ~2000 Zeilen), Services: backtester.py, optimizer.py, fast_sim.py (NEU), strategy_scanner.py, bitunix_trade.py, market_data.py
- DB: MongoDB (settings, custom_strategies, auto_trades, backtests, optimizer_runs, backtest_trades)
- Marktdaten: Binance public API (data-api.binance.vision), Fallback Bitunix/OKX; Live-Trading via Bitunix (Keys via env)
- Admin-Auth: JWT, Login Admin/admin (env ADMIN_USER/ADMIN_PASSWORD)

## Umgesetzt (21.07.2026 – aktueller Durchgang)
1. **Kerzen-Cache** (`services/candle_cache.py`):
   - Hybrid In-Memory (LRU nach used_at) + Disk-Fallback (`/tmp/candle_cache/*.pkl.gz`, gzipped pickle).
   - Nur der fehlende Head/Tail wird nachgeladen (EXTEND-HEAD/EXTEND-TAIL).
   - TAIL_TTL 45s: laufende Minute wird periodisch refresht.
   - Default MAX 500k Kerzen (~250MB), Rest evict → Disk → automatischer Reload beim nächsten Zugriff.
   - Cache-Stats in `GET /api/debug/status` sichtbar.
   - `fetch_history` verwendet ihn transparent (kein Aufrufer-Change nötig).
   - Verifiziert: 2. identischer 30d/2-Coin/3-Strategien-Backtest lief in 2.3s statt 23s (10× schneller).
2. **Fast-Path für Built-in-Strategien** (opt-in via `vectorized_signals()`):
   - Infrastruktur in `services/fast_sim.py:build_builtin_signal_provider()`.
   - Built-in ohne Methode → automatischer Legacy-Fallback (kein Bruch).
   - Umgesetzt für: `rsi_only` (inkl. vektorisiertem Liquidity-Sweep O(N)), `macd_rsi_momentum`, `scalping_4_rules`.
   - Verifiziert: identische Trades/PnL zu Legacy, 38× / 104× / 236× schneller.
   - Backtester + Optimizer nutzen den Fast-Path für Built-ins automatisch.
3. **Render-Deployment-Vorbereitung** (`/app/render.yaml`):
   - `startCommand` respektiert `$PORT`, `--workers 1` (0.1 CPU).
   - OMP/OpenBLAS/MKL-Threads auf 1 gepinnt (verhindert Context-Switch-Overhead).
   - Kerzen-Cache Env-Vars (MAX_CANDLES, DIR, DISK) vorkonfiguriert.
   - Health-Check `/api/health`.
   - Deployment-Agent Check: PASS (keine Blocker).

## Umgesetzt (20.07.2026 – dieser Durchgang)
1. Zeiträume: Backtester & Optimizer bis 365 Tage (UI: 60/90/180/360), Server-Clamps angehoben
2. Abbruch + persistenter Fortschritt: POST /api/backtest|optimizer/cancel/{id}, GET .../active, ETA (eta_seconds) im Status; Frontend resumed laufende Jobs beim Öffnen des Modals, Abbrechen-Buttons
3. Performance/Stabilität:
   - fast_sim.py: Indikator-Serien 1x über gesamte Historie vorberechnet für Custom-Strategien (~100x schneller, identische Ergebnisse verifiziert) – genutzt in Backtester + Optimizer (Discovery/Refine/Params)
   - run_backtest verarbeitet Symbole nacheinander und gibt Speicher frei (vorher: alle 1m-Historien gleichzeitig im RAM → OOM-Abbrüche bei 90d/alle Coins auf 512MB)
   - Status-Endpoint liefert keine export_candles/export_trades mehr (vorher riesige JSON beim Polling)
   - fetch_history mit 4 Retries statt stillem Abbruch bei Netzwerkfehler; Export-Limits (400k Kerzen / 100k Trades)
4. Bugfixes:
   - Min. Rel. Volumen: harter Filter bei rsi_only & bollinger_reversion (war nur weiche Confluence → verifiziert: 1000 → 0 Trades)
   - 3/5-Regeln-Trades: Ursache = min_confluence-Design + Pre-Signale; neue Option require_all_rules ("Nur 100% Regel-Treffer") im Backtester + AutoTrade-Konfiguration
   - Gewinnsicherung: war im Live/Paper-Monitor NICHT implementiert → in _manage_trade ergänzt (Event "GEWINNSICHERUNG", Live-SL-Sync); Backtest zeigt "Gesichert"-Spalte + profit_secured im CSV
5. Break-Even Modi: be_mode = tp1 | crv (be_trigger_crv) | profit_pct (be_trigger_profit_pct) | smart (Swing-Low/High, Live-Fallback=tp1) | off – in Backtester (global + pro Strategie) und StrategyAutoTradeModal
6. Bayes'sche Optimierung: TPE-lite (algorithm=bayes) im Optimizer params-Modus
7. Strategien weiterentwickeln: Discovery/Combo mit base_strategy_id (startet von bestehender Custom-Strategie), Speichern als Update (update_strategy_id) oder neue Strategie; StrategyBuilder konnte bereits editieren
8. Optimierung → Backtester: /api/optimizer/apply type=backtest schreibt beste Params in backtest_strategy_configs; UI-Button "In Backtester übernehmen"
9. Zeitfenster pro Strategie: settings.strategy_sessions (Scanner nutzt sie mit Fallback auf global); SettingsPanel Zeitfenster-Tab mit Scope-Dropdown; Backtester: globales Sessions-Feld + pro-Strategie im ⚙-Panel (Format "09:00-12:00,15:00-22:00", Europe/Berlin, DST-korrekt)

## Testing
- Testing-Agent Iteration 2: Backend 11/11, Frontend alle Checks grün (test_reports/iteration_2.json)
- Hinweis: Zeitfenster-Tab erreichbar über ⚙ neben Strategie-Tabs (mode='strategy'), nicht über Header-Zahnrad (mode='general')

## Infra-Einschätzung (für User)
- Hauptursachen der Abbrüche waren Code-seitig (RAM-Verbrauch, riesige Status-Antworten, kein Retry) → behoben
- 0.1 CPU bleibt für params-Optimierung von Built-in-Strategien auf 1m + viele Tage langsam (läuft jetzt aber durch statt abzubrechen); Render-Upgrade beschleunigt linear

## Backlog / Nächste Schritte
- P1: Vektorisierter Fast-Path für weitere Built-ins (bollinger_reversion, bollinger_squeeze, ema_pullback, ict_liquidity_sweep, stochastic_reversal, vwap_reversion) — aktuell laufen sie noch im Legacy-Pfad (funktionieren, sind aber ~50-100× langsamer als möglich)
- P1: Walk-Forward-Validierung / Out-of-Sample-Split gegen Overfitting der Optimierung
- P2: Zeitfenster-Optimierung (beste Handelszeiten automatisch finden)
- P2: server.py in Module aufteilen; gemeinsame Frontend-Konstanten (DAY_OPTIONS/BE_MODES) in shared Modul
- P2: Kerzen-Cache: numpy-basierte Kompakt-Repräsentation (statt dicts) → ~6× weniger RAM, wichtig für Multi-Coin 360d-Läufe auf Render Free

## Umgesetzt (Juni 2026 – Iteration "Kapital / Analyse / Optimizer-Scope")
Original-Anforderung: (1) Kapital-Zuweisungs-Popup Live+Paper, (2) Analyse: %-PnL + doppelte Live/Paper-Tabs entfernen, (3) Optimizer-Übernahme wahlweise nur für optimierte Coins oder global.
User-Entscheidungen: Limit-Änderung betrifft nur neue Trades; %-PnL bezogen auf Positionsgröße (entry×qty).

1. **Kapital-Zuweisung** (live/paper getrennt, persistiert in settings `_id=capital_allocation`):
   - Backend: `GET/POST /api/autotrade/capital` (POST admin), Modi full|fixed|percent, paper mit `base_balance` (Default 1000). Validierung (fixed>0, 1–100%, fixed ≤ Live-Gesamtguthaben).
   - `AutoTradeManager.capital_allocation/allocated_capital/used_margin` (bitunix_trade.py); `on_signal` begrenzt Gesamt-Exposure: freies Zuweisungs-Kapital = allocated − Summe max_capital offener Trades des Modus; Trade wird geclampt oder abgelehnt (<5 USDT frei), Telegram-Reject + Event-Notiz.
   - `GET /api/autotrade/balance` liefert jetzt `allocation` (live+paper: allocated/used_margin/free).
   - Frontend: Balance-Widget klickbar → `CapitalModal.js` (Tabs LIVE/PAPER, 3 Modi, Live-Vorschau, Validierung); Widget zeigt "Bot X · frei Y" (`bw-alloc`).
2. **Analyse**: `_enrich_trade` → `computed.pnl_pct` (PnL/(entry×qty)×100). Trade-Zeile zeigt (±x.xx%) farbcodiert + Meta-Feld "PnL %". Unterer trade-filter entfernt; oberer pnl-filter (Alle/Live/Paper) filtert global: Trades, PnL-Karten, Performance je Strategie (Titel zeigt · LIVE/· PAPER).
3. **Optimizer-Scope**: `POST /api/optimizer/apply` type=params akzeptiert `scope=global|coins`+`symbols`. coins → Indikator-Params in `coin_params[sid][symbol]` (scanner nutzt sie bereits per get_params) + trade_params in `strategy_coin_configs` (mit `optimizer_applied`-Zeitstempel, mode/enabled unangetastet). `GET /api/optimizer/overrides/{sid}` (liest DB). Frontend: Auswahl-Dialog beim Übernehmen (nur optimierte Coins vs. alle Coins), lila Punkt-Marker auf Coin-Chips + Legende.

Getestet: testing_agent iteration_1.json – Backend 12/12, Frontend alle Flows PASS. Regression-Suite: backend/tests/test_capital_and_optimizer.py.

## Backlog / Nächste Schritte
- P1: Live-Verifikation der Kapital-Limits mit echten Bitunix-Keys (hier nicht konfiguriert)
- P2: Warnhinweis im Kapital-Modal, wenn offenes Exposure > neues Limit
- P2: Coin-Override-Marker auch in Strategie-Tabs/AutoTrade-Modal anzeigen; Button zum Entfernen einzelner Coin-Overrides
- P2: server.py (2200+ Zeilen) in Router-Module aufteilen; Hydration-Warning `<span>` in `<option>` (Optimizer-Dropdown, vorbestehend)

## Bugfixes (Juni 2026 – Runde 2)
- Tages-Winrate ("HEUTE aktive Strategie") gefixt: process_signal + Startup-Rehydration lasen signal['tp1']/['sl'], Scanner liefert aber take_profit_1/stop_loss -> open_signal_evals blieb leer, nie win/loss gesetzt. Fix: Fallback auf beide Key-Varianten (server.py). E2E verifiziert (iteration_2.json, 100% PASS).
- Optimizer Timeframe-Default von 5m auf 1m geändert (Optimizer.js).
- Strategie-Review durchgeführt: Empfehlung an User – "Stochastic Reversal" redundant zu "Smart Money RSI Reversal" (gleiche Signalklasse Oszillator-Extrem-Reversal, Stochastik auf 1m nur schnellere/rauschigere Variante); Rest sinnvoll differenziert. Hinweis: Stochastic/VWAP-MR haben Beschreibung "empfohlen 5m-15m" aber Default-TF 1m.
