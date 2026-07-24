# PRD – Antons Daytrading Website (Crypto Scanner / Backtester / Optimizer)

## Original-Problemstellung
Bestehende, funktionierende externe Daytrading-Website (von GitHub: AntonsBacktesterUpgrade22071051,
React + FastAPI + MongoDB, Bitunix-Anbindung für Live/Paper-Trading) soll verbessert werden.
Die Seite bleibt extern/ausgelagert gehostet.

## Architektur
- Frontend: React (CRA), recharts, lightweight-charts, Phosphor Icons – /app/frontend
- Backend: FastAPI – /app/backend/server.py + services/ (backtester, optimizer, fast_sim,
  bitunix_trade, strategy_scanner, candle_cache) + strategies/ (registry, custom_strategy)
- DB: MongoDB (settings, custom_strategies, strategy_coin_configs, backtest_results,
  backtest_trades, optimizer_results, trades)
- Admin-Auth: POST /api/auth/login (Admin/admin), Bearer-Token

## Umgesetzt am 22.07.2026 (alle Punkte getestet, 11/11 Backend-Tests, Frontend 100%)
1. **Strategie-Komplett-Backup pro Strategie**
   - GET /api/strategies/{id}/export → JSON (Name, Typ, Long/Short-Regeln, Parameter,
     TP/SL, Break-Even, Gewinnsicherung, Auto-Leverage, Timeframe, Zeitfenster,
     Live/Paper-Overrides, Per-Coin-Trade-Configs, Backtest-Config)
   - POST /api/strategies/import → 1:1-Wiederherstellung auch nach Löschung
   - UI: Download-Button pro Strategie + "Strategie importieren" im Strategie-Manager
     (StrategyBuilder), Export/Import zusätzlich im ⚙-Einstellungspanel (SettingsPanel)
     und Trade-Settings-Export weiterhin im Blitz-Modal (StrategyAutoTradeModal)
2. **Discovery/Custom-Strategien im Backtester bearbeitbar**: ⚙-Panel zeigt jetzt
   Regeln (Schwellenwerte editierbar) + Indikator-Perioden als Definition-Override
   (strategy_configs[sid].definition, nur für den Backtest)
3. **Auto-Leverage** (effective_leverage in backtester.py, genutzt von Backtester + Live/Paper):
   Modi: Liquidation X% hinter Stop / X Ticks (1 Tick = 0.01%) hinter Stop, Max-Hebel-Cap.
   Verfügbar: global im Backtester, pro Strategie (⚙), Live/Paper-Modal, pro Coin.
4. **Optimizer aufgeteilt in Gruppen** (Checkboxen): TP/SL, Break-Even, Gewinnsicherung,
   Hebel, Auto-Leverage, Zeitfenster (body.optimize{...}); Discovery/Combo bekommen eine
   zusätzliche Trade-Settings-Suchphase; beim "Als Strategie speichern" werden die besten
   trade_params in die Backtest-Config der neuen Strategie geschrieben.
5. **PnL % + Drawdown %**: Backtest-Ranking, Matrix (inkl. Ø Hebel), Optimizer-Metriken,
   Strategie-Vergleich (avg-margin-basiert).
6. **Equity-Kurven-Chart pro Backtest** (EquityChart.js, recharts): Equity + Peak,
   Drawdown-Flächen, Liquidations-Marker, Long/Short getrennt, Coin-Beiträge,
   Strategie-Filter, Export equity.csv (GET /api/backtest/export/{job}?kind=equity,
   GET /api/backtest/equity/{job}).
7. **QoL**: Backtester- & Optimizer-Auswahl bleibt via localStorage erhalten
   (bt_ui_state_v1 / opt_ui_state_v1) – kein Reset beim Raus-/Reintabben.
8. **Zeiträume**: Presets 540/720/900/1080/1440 Tage + benutzerdefinierter Von/Bis-Bereich
   (Backend filtert Kerzen nach Datum, days-Limit 365 → 1500, auch im Optimizer).
9. **RAM-Anzeige** (Backend-RAM, Kerzen-Cache, Cache-leeren) jetzt auch im Optimizer.

## Umgesetzt am 24.07.2026 – Equity-Chart Feintuning (Iteration 5, 100% getestet)
1. **Coin-Chips einzeln zu-/abschaltbar** (statt „alle an/aus")
   - Top-3 Coins nach |PnL| automatisch vorausgewählt, „alle an" / „alle aus" als
     Sammelbuttons. Coin-Chip zeigt PnL-Wert als Badge.
   - Recompute bei Strategie-Wechsel (verhindert Coins aus alter Strategie).
2. **Multi-Strategie-Default**: bei ≥2 Strategien wird automatisch die 1. gewählt
   (statt „Alle Strategien") → verhindert Lag durch Über-Rendering.
3. **Liquidations-Marker Toggle** – Standard AUS (Hauptursache für Lag bei vielen
   Liquidationen). Chip zeigt Anzahl vorhandener Liquidationen im Label.
4. **Performance**: `isAnimationActive={false}` auf allen Lines/Areas, Downsampling
   auf max. 800 sichtbare Punkte (Liquidationen werden auf nächstliegenden Index
   remapt). Info-Chip zeigt „(Downsampling aktiv)" wenn aktiv.
5. **Equity-Kurve im Optimizer** (neu):
   - Toggle-Button `opt-equity-toggle` im Ergebnisbereich, **Standard AUS** (Performance).
   - Erst-Klick lädt scope=`optimized` (nur die im Lauf verwendeten Coins).
   - Zweiter Button „Auch andere Coins prüfen" (scope=`all`) → simuliert die
     Best-Kombination auf allen Top-10-Coins → Robustheits-Check außerhalb des
     Trainings-Sets.
   - Neuer Endpoint: `GET /api/optimizer/equity/{job_id}?scope=optimized|all`
     (async parallelisiert via `asyncio.gather`: 5.7s für 10 Coins statt >30s).
   - Werden auch nach Reload aus `optimizer_runs` in Mongo wiederhergestellt.
6. **EquityChart wiederverwendbar**: akzeptiert entweder `jobId` (Backtester lädt selbst)
   ODER `points`-Prop (Optimizer füttert vorbereitete Simulation direkt).

## Backlog / Nächste Schritte
- P1: server.py (2700+ Zeilen) in Router-Module aufteilen
- P1: Pydantic-Validierung für /api/strategies/import Payload
- P1: Robustheits-Check als eigener Feature-Screen (mehrere Marktphasen automatisch
  parallel testen und Ergebnisse gegenüberstellen)
- P2: React-Hydration-Warnung (<span> in <option>) beseitigen (nur Dev-Warnung)
- P2: Auto-Lev-Suchraum im Optimizer nach Modus getrennt sampeln (Ticks vs. %)
- P2: PnL % auch in PerformanceAnalytics-Charts
- P2: optimizer_equity scope=all optional per SSE streamen (für sehr lange days-Läufe)
