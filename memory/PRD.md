# Crypto Scalping Scanner - Product Requirements Document

## Original Problem Statement
Der User möchte eine Trading-App entwickeln, die:
- 4 Regeln einer Scalping-Strategie erkennt und den User notifyed
- Mit KI den Coin/Markt analysiert
- Aktuelle Ereignisse (Kriege etc.) beachtet
- Außerhalb von Emergent verfügbar sein soll

## Complete Strategy (User Provided)
**Handelszeiten:** London 9:00-12:00 CET, US 15:30-18:30 CET

**4 Regeln:**
1. EMA 50: Preis über = Long, Preis unter = Short
2. RSI: RSI < 32 = Long, RSI > 64 = Short
3. EMA 9 Trigger: Grüne HA Kerze schließt über EMA 9 = Long, Rote unter = Short
4. Zeit: 2 Kerzen Zeit (1min) - arbeitet mit Heikin Ashi Candles

**Risk Management:**
- SL: 3-5 ticks unter letztem Low
- TP1: 40% bei CRV 1 (dann SL auf Break Even)
- Target CRV: 2

## User Choices
- Krypto-Märkte, Top 10 Coins
- Dark Trading Theme
- In-App + Telegram Notifications
- Deployment auf Render
- Bitunix als Broker

## Architecture

### Backend (FastAPI)
- **File:** `/app/backend/server.py`
- **WebSocket Client:** `/app/backend/services/bitunix_client.py`
- **Indicators:** `/app/backend/services/technical_indicators.py`
- **Scanner:** `/app/backend/services/strategy_scanner.py`
- **Telegram:** `/app/backend/services/telegram_bot.py`

### Frontend (React)
- **App:** `/app/frontend/src/App.js`
- **Components:** Header, CoinSidebar, MainChart, SignalPanel, PerformanceAnalytics, AlertModal, SettingsPanel
- **Charts:** lightweight-charts (TradingView)
- **Icons:** @phosphor-icons/react
- **Fonts:** Chivo (Headings), Manrope (Body), JetBrains Mono (Numbers)

## Implementation Status (Feb 2026)

### ✅ Implemented (Phase 1 - MVP)
- Bitunix WebSocket Integration (Real-time 1min Candles für 10 Coins)
- Heikin Ashi Candle Calculation
- EMA 50 + EMA 9 Berechnung
- RSI (14) Berechnung
- 4-Regel Scanner Logik
- Trading Session Filter (London/US)
- Entry, SL, TP, CRV Berechnung
- Telegram Bot Integration
- MongoDB Storage für Signals + Performance
- WebSocket Real-time Frontend Updates
- Dark Trading Theme UI
- 4-Regel Visual Indicators
- Full-Screen Alert Modal (z-9999)
- Sound Alerts + Browser Notifications
- Performance Analytics Dashboard
- Coin Selector Sidebar
- Settings Panel mit Telegram Setup Guide

### ⚠️ Pending / Deferred (Phase 2)
- KI-Marktanalyse (LLM Integration)
- News/Event Tracking (Kriege, wichtige News)
- Signal Win/Loss Tracking (manuelles Feedback)
- Historical Backtest
- Push Notifications (PWA)
- Auto-Trading Sperre visualisiert (aktuell im Code implementiert)
- Historical Data Loading beim Chart Start
- Coin On/Off Toggle
- SL zu Break Even Automatik-Alert

## Environment Variables Required
```
MONGO_URL="mongodb://localhost:27017"
DB_NAME="crypto_scanner"
TELEGRAM_BOT_TOKEN=""  # User muss eintragen
TELEGRAM_CHAT_ID=""    # User muss eintragen
BITUNIX_API_KEY="9acf71135150d8046fc14e1493e16f8f"
BITUNIX_SECRET_KEY="9ee6fd2bb3c31af3a5ea092df4307a8a"
```

## Deployment
- Optimiert für **Render** (WebSocket-Support)
- Alternative: Railway
- Node/Python Backend Web Service
- MongoDB Atlas empfohlen für Production

## API Endpoints
- `GET /` - App Status
- `GET /api/coins` - Tracked Coins
- `GET /api/signals` - Recent Signals
- `GET /api/signals/{symbol}` - Symbol-specific Signals
- `GET /api/performance` - Performance Stats
- `GET /api/session/status` - Trading Session Status
- `POST /api/telegram/test` - Test Telegram Connection
- `WS /ws` - Real-time WebSocket Updates
