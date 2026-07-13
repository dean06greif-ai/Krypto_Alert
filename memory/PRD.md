# PRD – Krypto_Alert Daytrading Signals

## Original Problem (user, DE)
Bestehende Trading-Signals-Website (GitHub: dean06greif-ai/Krypto_Alert) "perfekt fürs Trading" machen:
tägl. Signal-Reset (Analyse bleibt), Regel-Kreise grün/rot bei aktiver Regel, Strategien als Dashboard-Reiter
mit Signal on/off pro Reiter (Signale nur vom aktiven Reiter), Strategie-Einstellungen + neue Custom-Strategien
erstellen/löschen, Auto-Trade pro Coin über Bitunix (Live/Paper, default AUS) mit Bitunix-ähnlichem Overlay +
max. Kapital, dynamisches SL/TP (Struktur-basiert, TP1 bei CRV1 mit %-Teilverkauf, TP-full bei CRV2, Break-Even
+ Gebühren), erweiterte Analyse, pro-Coin Strategie-Parameter, DB sparsam (500MB). Bugs: Black-Screen (Handy/PC),
Gold-Kurs Absturz, Cronjob "zu große Daten".

## User Choices
- Repo klonen & darauf aufbauen. Auto-Trade Live+Paper umschaltbar, default AUS. Reset Mitternacht Europe/Berlin.
- Design beibehalten & erweitern (Bitunix-ähnlich, dark).

## Stack
React + FastAPI + MongoDB. Market data: Bitunix→Binance→OKX Fallback + Yahoo (Gold/Silver/Oil). WS /api/ws.

## Implemented (2026-06 / this session)
- BUGFIX Black-Screen: lightweight-charts crashte bei ungültigem System-Locale → `localization:{locale:'en-US'}`.
- BUGFIX leerer Chart: historische Kerzen via GET /api/klines/{symbol} + geschützte Live-Updates (kein Out-of-order-Crash) + ErrorBoundary. Gold-Wechsel crasht nicht mehr.
- BUGFIX WebSocket: /ws → /api/ws (Ingress routet nur /api zum Backend). Auto-Reconnect.
- BUGFIX Cronjob: /api/health liefert minimal {status:alive} (Keepalive; Cron dorthin zeigen lassen).
- Täglicher Reset (Europe/Berlin Mitternacht): rohe Signale/geschlossene Trades gelöscht, kompakte Aggregate
  (performance, analytics_daily, trade_stats) bleiben dauerhaft → DB-sparsam.
- Live Regel-Kreise: analyze() liefert pro Regel long/short → Kreis grün(Long)/rot(Short); Banner bei allen gleich.
- Multi-Strategie: enabled_strategies als Dashboard-Reiter, Signal on/off pro Strategie, Signale nur vom aktiven Reiter.
- Pro-Coin UND pro-Strategie Parameter (coin_params) via SettingsPanel "Gilt für" Dropdown.
- Custom Strategy Builder: regelbasiert (Indikatoren rsi/ema_fast/ema_slow/price/ha_color/ema_gap_pct; ops <,>,<=,>=,cross_above,cross_below), CRUD, auto als Reiter.
- Auto-Trade pro Coin (Bitunix): Paper/Live umschaltbar, default AUS, Bitunix-ähnliches Overlay, max Kapital,
  Hebel, dynamisches SL (Struktur/fest), TP1 bei CRV mit %-Teilverkauf, TP-full bei CRV, Break-Even+Gebühren,
  Monitor-Loop verwaltet offene Trades gegen Live-Preis.
- Erweiterte Analyse: Win-Rate (heute + gesamt), per-Strategie, Top-Coins, Trades-Ansicht (PnL/offen/geschlossen), Zeit-Analyse, auto Signal-Outcome (win/loss).

## Verified
Backend 54/54 pytest. Frontend: alle kritischen Flows (Chart BTC+GOLD, Reiter, Regel-Kreise, AutoTrade-Modal,
Builder, Settings pro-Coin, WS live) per Playwright bestätigt.

## Known limits
- Bitunix ist von der Preview-IP geblockt (403) → LIVE-Orders nur auf Render testbar. Paper-Modus voll funktionsfähig.
- Telegram Token nur auf Render-Server (nicht im Repo) → /api/telegram/test 400 in Preview.

## Backlog / Next (P1/P2)
- P1: Server-seitige Validierung (leverage 1-125, tp1_close 1-99, max_capital>0) für Auto-Trade.
- P1: Live-Verifikation der Bitunix-Order-Signatur auf Render (Error 10007 prüfen).
- P2: private WS von Bitunix für Positions/PnL live; AbortController gegen Konsolen-Rauschen; "Save & Close" im Builder.
