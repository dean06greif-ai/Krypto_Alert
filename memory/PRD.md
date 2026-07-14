# Krypto Alert – Crypto Scalping Scanner (PRD)

## Origin
Imported from user's external repo github.com/dean06greif-ai/Krypto_Alert (branch NEW).
Stack: React 19 (CRA/craco) + FastAPI + MongoDB + WebSocket live scanner. Single-admin auth (JWT, lock icon).

## Problem statement (this session)
1. Delete analysis data by time range like clearing browser/Google history (last hour, 24h, 7d, 4w, all).
2. Fully edit custom strategies via the strategy management panel.
3. Permanently delete strategies including predefined/built-in ones.
4. External code download (handled via platform "Save to GitHub").

## Implemented (June 2026)
- Backend (server.py):
  - POST /api/analytics/clear {range: hour|24h|7d|4w|all} — admin. Deletes signals/auto_trades by timestamp, analytics_daily/trade_stats by date, rebuilds performance; 'all' wipes everything.
  - DELETE /api/strategies/{id} — unified delete (custom => DB removal; predefined => added to settings.deleted_strategies).
  - POST /api/strategies/restore-defaults — un-hide predefined.
  - GET /api/strategies now returns raw `definition` for custom (enables editing).
  - POST /api/strategies/custom already upserts by id => edit path.
  - strategy_scanner: DEFAULT_SETTINGS.deleted_strategies; enabled_strategies() excludes deleted.
- Frontend:
  - PerformanceAnalytics.js: red trash button -> clear-analytics-modal with 5 time ranges (browser-history style), admin-gated.
  - StrategyBuilder.js: 'Alle Strategien' list with delete for ALL strategies + edit for custom; edit populates form and saves via same id; restore-defaults button. Added authHeaders to all write calls.
  - SettingsPanel.js: added authHeaders to settings save + telegram test.
  - App.js: passes isAdmin/onNeedAdmin/onCleared to analytics; reloads on WS 'analytics_cleared'.

## Status
All 3 features E2E tested (testing agent iteration_5): 100% (7/7 flows). No blocking issues.

## Backlog / Next
- P2: convert window.confirm deletes to in-app modal for consistency.
- P2: confirm dialog before admin logout to prevent accidental logout.
- P2: move connection toast away from lock icon area.
