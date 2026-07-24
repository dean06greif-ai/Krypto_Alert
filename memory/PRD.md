# PRD – Antons Daytrading Website (Crypto Scanner / Backtester / Optimizer / KI Trader)

## Original-Problemstellung
Bestehende, funktionierende externe Daytrading-Website (GitHub: dean06greif-ai/Krypto_Alert,
Branch bitunix-fix; React + FastAPI + MongoDB, Bitunix-Anbindung für Live/Paper-Trading).
Die Seite bleibt extern/ausgelagert gehostet (Render.com, render.yaml vorhanden).

## Architektur
- Frontend: React (CRA), recharts, lightweight-charts, Phosphor Icons – /app/frontend
- Backend: FastAPI – /app/backend/server.py + services/ (backtester, optimizer, fast_sim,
  bitunix_trade, strategy_scanner, candle_cache, ai_engine, news_feed) + strategies/
- DB: MongoDB (settings, custom_strategies, strategy_coin_configs, backtest_results,
  trades, ai_chat, ai_decisions)
- Admin-Auth: POST /api/auth/login (Admin/admin), Bearer-Token
- LLM: Emergent Universal Key (EMERGENT_LLM_KEY in backend/.env), emergentintegrations,
  funktioniert auch extern (Render) – Guthaben über Emergent-Profil aufladen.

## Umgesetzt am 24.07.2026 – Lokale Ausführung Phase 1 (100% getestet, Backend 20/20, Frontend 100%)
Backtester, Optimizer & Strategy-Discovery können wahlweise auf dem PC des Users laufen:
1. **Architektur "Same code, remote executor"**: Lokaler Worker (`/app/local_worker/worker.py`)
   importiert exakt dieselben Module (services.backtester/optimizer/candle_cache,
   strategies/) -> identische Ergebnisse per Konstruktion (verifiziert: Cloud vs. Lokal
   36 Trades / -41.66 PnL identisch). Jobs bleiben in bt.JOBS/opt.JOBS -> alle bestehenden
   Status-/Active-/Cancel-/Equity-/Export-/Apply-Endpoints & UI unverändert.
2. **Verbindung**: Worker -> Server Outbound-Polling alle 2s (`POST /api/worker/poll`,
   X-Worker-Token), keine Portfreigaben. Server: services/local_exec.py (Queue, Worker-
   Registry, Watchdog gegen Geister-Jobs), routers/local_worker.py. Job-Payload enthält
   Settings-Snapshot + Custom-Strategie-Definitionen. Ergebnis kommt gzip zurück und wird
   wie beim Cloud-Pfad in db.backtests/backtest_trades/optimizer_runs persistiert.
3. **Lokaler Daten-Cache**: Worker nutzt candle_cache mit CANDLE_CACHE_DIR=Daten-Ordner
   (Default ~/KryptoScannerDaten) -> inkrementelle Updates (nur fehlender Head/Tail) gratis.
   1m-Kerzen decken alle Timeframes ab. Neue candle_cache-Helfer: cached_meta,
   persist_symbol, remove_symbol, list_disk_symbols. Daten-Index (index.json) fürs Inventar.
4. **Daten-Verwaltung** (UI + Worker-Jobs): Download pro Coin/Zeitraum, Alle aktualisieren,
   Löschen, Speicherbelegung/freier Platz, Fortschritt + Abbruch. Auto-Update optional.
5. **UI**: Ausführungs-Toggle Cloud/Lokal in Backtester & Optimizer (mit Online-Punkt,
   localStorage-persistiert, 💻-Tag im Fortschritt), neues LocalWorkerPanel (⚙ Verwalten):
   Worker-Status (CPU/RAM/GPU/Version), Einstellungen (CPU-Kerne, RAM-Limit, max. parallele
   Jobs, Daten-Ordner, Auto-Update; GPU reserviert für Phase 2), Einrichtung (Zip-Download
   `GET /api/localworker/package` – bündelt aktuellen Server-Code, ohne worker_config.json –,
   Token anzeigen/kopieren/erneuern, Startbefehl), Daten-Tabelle.
6. **Endpoints**: POST /api/worker/poll|/job/{id}/progress|/job/{id}/result (Worker-Token);
   GET /api/localworker/status|settings|token|package, POST settings|token/regenerate|
   data/download|data/update|data/delete|data/cancel/{id} (Admin). Backtest/Optimizer-run
   akzeptieren `execution: "cloud"|"local"` (Default cloud, 503 wenn kein Worker online).
7. **Tests**: backend/tests/test_local_worker.py (12 Tests: Queue-Roundtrip, Cancel,
   Daten-Jobs, Auth, Cloud-Regression). Hinweis: tests/test_backtest_optimizer.py hat einen
   VORBESTEHENDEN Fehler (erwartet exakt 9 Strategien, es sind 11) + Default-URL zeigt auf
   alten Pod (REACT_APP_BACKEND_URL setzen).

## Backlog Lokale Ausführung (Phase 2)
- P1: Multi-Core innerhalb eines Jobs (ProcessPool über (Strategie,Coin)-Paare bzw.
  Optimizer-Iterationen; simulate_pair-Inputs picklbar machen)
- P1: Walk-Forward-Analyse & Konstanz-/Robustheitstests für Optimizer/Strat-Finder
  (laufen dank Architektur automatisch auch lokal)
- P2: GPU-Beschleunigung für Parameter-Sweeps (use_gpu-Setting bereits vorhanden)
- P2: Backtest-Queue für mehrere Strategien-Läufe hintereinander, Benchmark-Modus
- P2: Kerzen-CSV-Export für lokale Läufe (export_candles wird aktuell nicht hochgeladen)

## Umgesetzt am 24.07.2026 – KI Trader (100% getestet, Backend 15/15, Frontend 100%)
Neue parameterlose Strategie **"KI Trader"** (strategy_id: `ai_trader`):
1. **AI Engine** (services/ai_engine.py): Periodische Analyse (default alle 10 min,
   konfigurierbar 5-60) aller Coins mit Multi-Timeframe-Snapshots (1m/15m/1h: RSI,
   EMA-Trend, Range, ATR, Volumen) + Krypto-News (kostenlose RSS: Cointelegraph,
   CoinDesk, Decrypt via services/news_feed.py, 10-min-Cache). LLM (default
   openai/gpt-5.4, wählbar: gpt-5.4-mini, claude-sonnet-4-6, gemini-3-flash-preview)
   liefert JSON-Entscheidungen: action LONG/SHORT/HOLD, confidence, sl/tp1/tpf %,
   news_impact, reasoning (deutsch).
2. **Vollautomatisches Trading**: Aktionable Entscheidungen (confidence >= min_confidence,
   Cooldown, Session-Fenster) werden als Signale durch die BESTEHENDE Pipeline emittiert
   (process_signal → Telegram → autotrader.on_signal). Per-Coin Paper/Live über das
   bestehende ⚡-Modal (strategy_coin_configs, default off). Signal wird zusätzlich per
   WebSocket type "signal" gebroadcastet.
3. **KI-Chat-Panel** (AITradingPanel.js): Zahnrad am KI-Trader-Tab öffnet Chat statt
   Settings. SSE-Streaming (POST /api/ai/chat, fetch+ReadableStream, da EventSource keine
   Auth-Header kann). User-Nachrichten fließen als Direktiven in die nächste Analyse
   (letzte 15 Nachrichten). Analyse-Ergebnisse erscheinen als Karten im Chat-Feed
   (role="analysis"). Decision-Chips pro Coin, An/Aus-Toggle, "Jetzt analysieren",
   Setup (Modell/Intervall/Konfidenz/Cooldown/News).
4. **SignalPanel-Regeln** (strategies/ai_trader_strategy.py): KI-Analyse, KI-Richtung
   (mit Reasoning), Konfidenz, News-Lage – Live-Kreise wie andere Strategien.
5. **Endpoints**: GET /api/ai/status, POST /api/ai/config (admin), POST /api/ai/analyze
   (admin), GET/POST/DELETE /api/ai/chat(+/history), GET /api/ai/news.
6. **Extern-Deploy**: render.yaml buildCommand um --extra-index-url für
   emergentintegrations erweitert; EMERGENT_LLM_KEY als Render-EnvVar; requirements.txt
   um emergentintegrations ergänzt. Download-Zip: frontend/public/krypto_alert_ki_update.zip
7. Default-Config: enabled=false, interval 10 min, min_confidence 65, cooldown 45 min,
   news an, Modell gpt-5.4.

## Frühere Iterationen (22.-24.07.2026)
Strategie-Export/Import, Auto-Leverage, Optimizer-Gruppen, PnL%/Drawdown%,
Equity-Kurven-Chart, Zeitraum-Presets bis 1440 Tage, RAM-Anzeige (Details siehe Git-Historie).

## Backlog / Nächste Schritte
- P1: KI-Trade-Levels (SL/TP der KI) optional direkt für die Order nutzen
  (aktuell: Signal zeigt KI-Levels, Trade nutzt Coin-Trade-Settings wie andere Strategien)
- P2: Hydration-Warning (<span> in <option>) in Selects app-weit fixen (dev-only)
- P2: Telegram-Nachricht um KI-Reasoning erweitern
- P2: KI-Tagesbericht (Zusammenfassung aller Trades am Abend im Chat)
