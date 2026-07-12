#====================================================================================================
# START - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================

# THIS SECTION CONTAINS CRITICAL TESTING INSTRUCTIONS FOR BOTH AGENTS
# BOTH MAIN_AGENT AND TESTING_AGENT MUST PRESERVE THIS ENTIRE BLOCK

# Communication Protocol:
# If the `testing_agent` is available, main agent should delegate all testing tasks to it.
#
# You have access to a file called `test_result.md`. This file contains the complete testing state
# and history, and is the primary means of communication between main and the testing agent.
#
# Main and testing agents must follow this exact format to maintain testing data. 
# The testing data must be entered in yaml format Below is the data structure:
# 
## user_problem_statement: {problem_statement}
## backend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.py"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## frontend:
##   - task: "Task name"
##     implemented: true
##     working: true  # or false or "NA"
##     file: "file_path.js"
##     stuck_count: 0
##     priority: "high"  # or "medium" or "low"
##     needs_retesting: false
##     status_history:
##         -working: true  # or false or "NA"
##         -agent: "main"  # or "testing" or "user"
##         -comment: "Detailed comment about status"
##
## metadata:
##   created_by: "main_agent"
##   version: "1.0"
##   test_sequence: 0
##   run_ui: false
##
## test_plan:
##   current_focus:
##     - "Task name 1"
##     - "Task name 2"
##   stuck_tasks:
##     - "Task name with persistent issues"
##   test_all: false
##   test_priority: "high_first"  # or "sequential" or "stuck_first"
##
## agent_communication:
##     -agent: "main"  # or "testing" or "user"
##     -message: "Communication message between agents"

# Protocol Guidelines for Main agent
#
# 1. Update Test Result File Before Testing:
#    - Main agent must always update the `test_result.md` file before calling the testing agent
#    - Add implementation details to the status_history
#    - Set `needs_retesting` to true for tasks that need testing
#    - Update the `test_plan` section to guide testing priorities
#    - Add a message to `agent_communication` explaining what you've done
#
# 2. Incorporate User Feedback:
#    - When a user provides feedback that something is or isn't working, add this information to the relevant task's status_history
#    - Update the working status based on user feedback
#    - If a user reports an issue with a task that was marked as working, increment the stuck_count
#    - Whenever user reports issue in the app, if we have testing agent and task_result.md file so find the appropriate task for that and append in status_history of that task to contain the user concern and problem as well 
#
# 3. Track Stuck Tasks:
#    - Monitor which tasks have high stuck_count values or where you are fixing same issue again and again, analyze that when you read task_result.md
#    - For persistent issues, use websearch tool to find solutions
#    - Pay special attention to tasks in the stuck_tasks list
#    - When you fix an issue with a stuck task, don't reset the stuck_count until the testing agent confirms it's working
#
# 4. Provide Context to Testing Agent:
#    - When calling the testing agent, provide clear instructions about:
#      - Which tasks need testing (reference the test_plan)
#      - Any authentication details or configuration needed
#      - Specific test scenarios to focus on
#      - Any known issues or edge cases to verify
#
# 5. Call the testing agent with specific instructions referring to test_result.md
#
# IMPORTANT: Main agent must ALWAYS update test_result.md BEFORE calling the testing agent, as it relies on this file to understand what to test next.

#====================================================================================================
# END - Testing Protocol - DO NOT EDIT OR REMOVE THIS SECTION
#====================================================================================================



#====================================================================================================
# Testing Data - Main Agent and testing sub agent both should log testing data below this section
#====================================================================================================

user_problem_statement: >
  Crypto scalping signal app using Bitunix broker data. Problem reported by user:
  no signals ever appear (middle of the UI stays empty), even after raising RSI
  thresholds. App should work 24/7 and eventually run outside Emergent. User also
  wants to confirm that changing trading hours (24/7 / London / US) + saving works.

backend:
  - task: "Bitunix WebSocket data parsing fix (root cause of no signals)"
    implemented: true
    working: "NA"
    file: "backend/services/bitunix_client.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: "NA"
        -agent: "main"
        -comment: >
          ROOT CAUSE FIXED. Old code read data['data']['k'] and kline['t'] which do
          not exist in Bitunix's real format ({ch,symbol,ts,data:{o,h,l,c,b,q}}). No
          candle was ever processed -> buffer empty -> never a signal. Rewrote parsing
          + switched to a SINGLE websocket connection subscribed to all symbols (was
          10 parallel connections causing HTTP 429). NOTE: live verification currently
          BLOCKED in this env because Bitunix Cloudflare WAF returned HTTP 403 to this
          datacenter IP after an initial burst. Pipeline verified offline instead.
  - task: "Historical kline bootstrap via REST"
    implemented: true
    working: "NA"
    file: "backend/services/bitunix_client.py, backend/server.py"
    stuck_count: 0
    priority: "medium"
    needs_retesting: false
    status_history:
        -working: "NA"
        -agent: "main"
        -comment: >
          Added fetch_historical_klines + scanner.bootstrap so indicators work
          immediately instead of waiting ~60 min. Blocked live by Cloudflare 403.
  - task: "Candle aggregation + evaluate strategy on candle close"
    implemented: true
    working: true
    file: "backend/services/strategy_scanner.py, backend/server.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: true
        -agent: "main"
        -comment: >
          Rewrote add_candle to bucket WS snapshots into distinct 1-min candles and
          return True only on candle close. Verified OFFLINE (/tmp/pipeline_test.py):
          aggregation/close-detection correct, rsi_only fired LONG, scalping ran clean.
  - task: "Settings save/load + trading session logic (24/7 / London / US)"
    implemented: true
    working: true
    file: "backend/server.py, backend/services/strategy_scanner.py"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: "NA"
        -agent: "main"
        -comment: >
          USER'S EXPLICIT REQUEST: ensure changing trading hours + saving works.
          GET/POST /api/settings persist to MongoDB. Empty custom_sessions => 24/7.
          is_trading_session()/get_current_session() drive whether signals are allowed.
          Needs testing: save 24/7 (empty sessions), save London-only, save US-only,
          persistence, and /api/session/status reflects the change correctly.
        -working: true
        -agent: "testing"
        -comment: >
          TESTED AND VERIFIED: All settings save/load and trading session logic working correctly.
          ✓ GET /api/health returns status "alive"
          ✓ GET /api/settings returns all required keys (active_strategy, strategy_params, custom_sessions, pre_signal_enabled)
          ✓ 24/7 mode: POST empty custom_sessions → GET /api/session/status shows is_active=true, current_session="24/7 Mode", persistence confirmed
          ✓ London-only: POST London session → GET /api/session/status shows correct consistency (is_active matches current_session based on time)
          ✓ US-only: POST US session → GET /api/session/status shows correct consistency
          ✓ AllDay toggle: POST 00:00-23:59 session → is_active=true, current_session="AllDay"
          ✓ pre_signal toggle: POST pre_signal_enabled=false → persisted correctly
          ✓ Strategy switch: POST active_strategy="rsi_only" → GET /api/strategies shows active="rsi_only", both strategies listed with current_params
          ✓ strategy_params save: POST rsi_long_threshold=25 → persisted correctly
          ✓ Default settings restored successfully
          All 11 tests passed. Backend logs show proper settings updates and MongoDB persistence.
  - task: "Debug status endpoint for data-feed observability"
    implemented: true
    working: true
    file: "backend/server.py, backend/services/strategy_scanner.py, backend/services/bitunix_client.py"
    stuck_count: 0
    priority: "medium"
    needs_retesting: false
    status_history:
        -working: "NA"
        -agent: "main"
        -comment: >
          Added GET /api/debug/status exposing data_feed connection state + per-coin
          candle count/RSI. Verify it returns valid JSON with expected keys.
        -working: true
        -agent: "testing"
        -comment: >
          TESTED AND VERIFIED: GET /api/debug/status returns valid JSON with correct structure.
          ✓ data_feed object present with keys: connected, last_error, last_message_at, messages_received, reconnects
          ✓ coins array present with 10 items, each having: symbol, closed_candles, last_candle_time, price, rsi, ema_fast, ema_slow
          ✓ data_feed.connected=false and closed_candles=0 is EXPECTED (Cloudflare WAF blocking Bitunix API - external infra issue, not code bug)
          ✓ GET /api/signals?limit=5 returns valid JSON (empty array expected due to no live data)
          ✓ GET /api/performance returns valid JSON (empty array expected due to no live data)
          Endpoint structure and error handling working correctly.

frontend:
  - task: "Settings panel - trading hours change + save"
    implemented: true
    working: "NA"
    file: "frontend/src/components/SettingsPanel.js"
    stuck_count: 0
    priority: "high"
    needs_retesting: false
    status_history:
        -working: "NA"
        -agent: "main"
        -comment: "Not yet tested via automation. Awaiting user go-ahead for frontend testing."

metadata:
  created_by: "main_agent"
  version: "1.0"
  test_sequence: 1
  run_ui: false

test_plan:
  current_focus: []
  stuck_tasks: []
  test_all: false
  test_priority: "high_first"

agent_communication:
    -agent: "main"
    -message: >
      Please test the BACKEND only. Focus on the settings/session flow (user's explicit
      request): 1) GET /api/settings returns current settings. 2) POST /api/settings with
      {"custom_sessions": []} then GET /api/session/status -> is_active true and
      current_session "24/7 Mode". 3) POST London-only session covering current time vs
      not, and US-only; verify /api/session/status current_session + is_active reflect it.
      4) Confirm persistence: POST a change, then GET /api/settings shows it. 5) GET
      /api/strategies returns both strategies with current_params. 6) GET /api/debug/status
      returns JSON with data_feed + coins array. IMPORTANT: live Bitunix market data is
      currently BLOCKED by Cloudflare WAF (HTTP 403) on this datacenter IP, so data_feed
      will show connected=false and coins will have 0 candles - that is EXPECTED here and
      NOT a code bug; do not fail the run for missing live signals. Restore settings to
      default (London+US) at the end if possible.
    -agent: "testing"
    -message: >
      BACKEND TESTING COMPLETE - ALL TESTS PASSED (11/11). Created comprehensive test suite
      in /app/backend_test.py covering all requested scenarios. Key findings:
      ✓ All settings save/load endpoints working correctly with MongoDB persistence
      ✓ Trading session logic (24/7, London, US, custom) working as expected
      ✓ Session status API correctly reflects active/inactive state based on time
      ✓ Strategy switching and parameter persistence working
      ✓ Debug status endpoint returns proper structure
      ✓ Signals and performance endpoints return valid JSON
      ✓ Cloudflare 403 blocking Bitunix data (expected external issue, not code bug)
      ✓ Default settings restored successfully
      Backend logs confirm all API calls successful (200 OK) and settings properly persisted.
      No critical issues found. User's explicit request (trading hours save/load) fully verified.

#=== Update: Multi-exchange fallback data feed (Weg C) ===
backend_update:
  - task: "Multi-exchange fallback market data feed (Bitunix -> Binance -> OKX)"
    implemented: true
    working: true
    file: "backend/services/market_data.py, backend/server.py, backend/services/strategy_scanner.py"
    priority: "high"
    status_history:
        -working: true
        -agent: "main"
        -comment: >
          Replaced Bitunix-only websocket with REST polling MarketDataFeed that probes
          sources in order Bitunix->Binance->OKX and auto-falls-back when one is blocked
          (Cloudflare 403). VERIFIED LIVE: Bitunix 403 -> auto-selected binance,
          bootstrapped 100 candles/coin, /api/debug/status connected=true source=binance.
          Temporarily switched to rsi_only and 2 REAL signals (BTCUSDT/ETHUSDT LONG) were
          generated from live data, stored in Mongo, and delivered to Telegram (200 OK).
          Reverted active_strategy to scalping_4_rules. Poll interval 12s, evaluate on
          candle close only (1/min/coin => low DB writes).

agent_communication_update:
    -agent: "main"
    -message: >
      Please RE-TEST backend after the data-feed refactor. Verify: 1) GET /api/debug/status
      returns data_feed.connected=true with active_source in [bitunix,binance,okx] and every
      coin has closed_candles>0 with numeric rsi. 2) GET /api/settings still works and POST
      persists (24/7 => empty custom_sessions). 3) GET /api/strategies lists both strategies.
      4) POST /api/settings {"active_strategy":"rsi_only"} then within ~90s GET /api/signals
      should return >=1 signal (live oversold market); then POST back {"active_strategy":
      "scalping_4_rules"}. 5) GET /api/session/status consistent. 6) /api/performance valid.
      NOTE: Bitunix itself is Cloudflare-blocked on this IP (expected) so active_source will
      be 'binance' here - that is CORRECT (fallback working), not a bug.

        -working: true
        -agent: "testing"
        -comment: >
          RE-TESTED AND VERIFIED: Multi-exchange fallback data feed working perfectly after refactor.
          All 7 comprehensive backend tests PASSED (7/7).
          
          ✅ TEST 1 - Data Feed Status (Multi-Exchange Fallback):
          - data_feed.connected = true
          - active_source = "binance" (Bitunix blocked with HTTP 403 as expected, fallback working)
          - messages_received = 280 (live data flowing)
          - All 10 coins have 100 closed_candles with valid numeric RSI and price
          - Sample: BTCUSDT (rsi=36.99, price=64070.43), ETHUSDT (rsi=38.74, price=1813.86)
          
          ✅ TEST 2 - Settings Endpoint:
          - GET /api/settings returns all required keys: active_strategy, strategy_params, custom_sessions, pre_signal_enabled
          - custom_sessions = [] (24/7 mode confirmed)
          
          ✅ TEST 3 - Session Status:
          - GET /api/session/status: is_active=true, current_session="24/7 Mode"
          
          ✅ TEST 4 - Strategies Endpoint:
          - GET /api/strategies lists both scalping_4_rules and rsi_only
          - Each strategy has current_params populated correctly
          - active = "scalping_4_rules"
          
          ✅ TEST 5 - LIVE Signal Generation (End-to-End):
          - Switched to rsi_only strategy successfully
          - 2 REAL signals generated IMMEDIATELY (within 0s): BTCUSDT LONG @ 64054.61 (RSI=27.56), ETHUSDT LONG @ 1812.77 (RSI=28.92)
          - All required signal fields present: symbol, type, entry_price, stop_loss, take_profit_1, take_profit_full, crv, rsi, timestamp, strategy_id, id
          - Telegram notifications sent successfully (200 OK per backend logs)
          - Successfully restored to scalping_4_rules and verified
          
          ✅ TEST 6 - Settings Persistence:
          - POST pre_signal_enabled=false → GET confirms persisted
          - POST pre_signal_enabled=true → GET confirms persisted
          - MongoDB persistence working correctly
          
          ✅ TEST 7 - Performance Endpoint:
          - GET /api/performance returns valid JSON with 2 performance records (BTCUSDT, ETHUSDT)
          
          Backend logs confirm:
          - Bitunix blocked (HTTP 403) → Binance fallback selected automatically
          - All 10 coins bootstrapped with 100 closed candles from Binance
          - Live polling scanner running (12s interval)
          - Signals generated, stored in MongoDB, and delivered to Telegram
          - All API endpoints responding with 200 OK
          
          Final state verified: active_strategy=scalping_4_rules, custom_sessions=[]
          
          NO CRITICAL ISSUES FOUND. Multi-exchange fallback data feed is production-ready.

agent_communication_update:
    -agent: "testing"
    -message: >
      BACKEND RE-TESTING COMPLETE - ALL TESTS PASSED (7/7) ✅
      
      Multi-exchange fallback data feed (Bitunix -> Binance -> OKX) is working perfectly.
      Comprehensive end-to-end testing completed covering all requested verification points:
      
      1. ✅ Data feed connected with active_source="binance" (Bitunix blocked as expected)
      2. ✅ All 10 coins have 100 closed candles with valid RSI/price data
      3. ✅ Settings and session endpoints working correctly (24/7 mode)
      4. ✅ Both strategies (scalping_4_rules, rsi_only) listed with current_params
      5. ✅ LIVE signal generation verified: 2 real signals generated immediately (BTCUSDT, ETHUSDT LONG)
      6. ✅ Settings persistence working (MongoDB)
      7. ✅ Performance endpoint returns valid JSON
      
      Backend logs confirm:
      - Automatic fallback from Bitunix (403) to Binance working correctly
      - Live data polling every 12s
      - Signals generated, persisted, and sent to Telegram (200 OK)
      - All API endpoints healthy (200 OK)
      
      The data-feed refactor is PRODUCTION-READY. No critical issues found.
      Main agent can summarize and finish.

#=== Update: "Other" instruments (Gold/Silver/Oil) added ===
feature_update:
  - task: "Add OTHER group (Gold/Silver/Oil) to left sidebar + backend data feed"
    implemented: true
    working: true
    files: "backend/services/market_data.py (fetch_yahoo), backend/server.py (OTHER_INSTRUMENTS, ALL_SYMBOLS, polling+bootstrap+/api/coins+/api/debug), frontend/src/components/CoinSidebar.js (+css)"
    status_history:
        -working: true
        -agent: "main"
        -comment: >
          Added commodities via free Yahoo Finance 1m data (GC=F gold, SI=F silver, CL=F oil,
          browser UA required, range=5d bootstrap / 1d poll). Backend scans them like coins
          (same strategies/signals/telegram). Frontend sidebar now shows two groups: TOP 10 COINS
          (POLUSDT replaces MATICUSDT) and OTHER (GOLD/SILVER/OIL). VERIFIED via screenshot:
          bootstrap 100 candles each, /api/debug/status shows GOLD rsi 28.49 / SILVER 38.93 /
          OIL 31.87 live. Clicking GOLD updates chart header to GOLD and signal panel targets GOLD.
          Frontend WS 'Connected to scanner' confirmed.

#=== Update: per-instrument notification toggle + header cleanup ===
feature_update_2:
  - task: "Per-instrument signal notification on/off toggle + remove lower 24/7 text"
    implemented: true
    working: true
    files: "backend/services/strategy_scanner.py (notifications setting + is_notify_enabled), backend/server.py (process_signal gates telegram + notify flag), frontend/src/App.js (fetch/toggle notifications, gate popups), frontend/src/components/CoinSidebar.js (bell toggle per item), frontend/src/components/Header.js (remove lower 24/7 text)"
    status_history:
        -working: true
        -agent: "main"
        -comment: >
          Each instrument (coins + GOLD/SILVER/OIL) has a bell toggle in the sidebar.
          OFF => no Telegram + no popup/sound for that symbol (signal still stored/broadcast).
          Persisted via settings.notifications {symbol: bool}. VERIFIED: backend persists
          {'GOLD': false}; frontend bell click toggles, shows toast, does NOT change selected
          coin. Header lower '24/7 MODUS AKTIV' removed (session-times empty in 24/7); top
          badge '24/7 MODE - ACTIVE' kept.
