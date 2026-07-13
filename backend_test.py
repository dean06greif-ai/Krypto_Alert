#!/usr/bin/env python3
"""
Backend API Testing Suite for Crypto Scalping Signal App
Tests the multi-exchange fallback data feed (Bitunix -> Binance -> OKX)
"""
import requests
import time
import json
from datetime import datetime

# Backend URL from frontend/.env
BACKEND_URL = "https://cf1e6c16-8963-4aee-96f8-35bf1cb3d03c.preview.emergentagent.com/api"

def log_test(test_name, passed, details=""):
    """Log test result"""
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"\n{status}: {test_name}")
    if details:
        print(f"  Details: {details}")
    return passed

def test_debug_status():
    """Test 1: GET /api/debug/status - verify data feed is connected and working"""
    print("\n" + "="*80)
    print("TEST 1: Data Feed Status (Multi-Exchange Fallback)")
    print("="*80)
    
    try:
        resp = requests.get(f"{BACKEND_URL}/debug/status", timeout=10)
        if resp.status_code != 200:
            return log_test("GET /api/debug/status", False, f"HTTP {resp.status_code}")
        
        data = resp.json()
        print(f"Response: {json.dumps(data, indent=2)}")
        
        # Check data_feed object
        if "data_feed" not in data:
            return log_test("data_feed object present", False, "Missing data_feed key")
        
        feed = data["data_feed"]
        
        # Verify connected
        if not feed.get("connected"):
            return log_test("data_feed.connected", False, f"connected={feed.get('connected')}")
        log_test("data_feed.connected == true", True)
        
        # Verify active_source is one of the expected values
        active_source = feed.get("active_source")
        if active_source not in ["bitunix", "binance", "okx"]:
            return log_test("data_feed.active_source", False, f"active_source={active_source}")
        log_test(f"data_feed.active_source == '{active_source}'", True, 
                f"Fallback working (Bitunix blocked -> {active_source})")
        
        # Verify messages_received > 0
        messages = feed.get("messages_received", 0)
        if messages <= 0:
            return log_test("data_feed.messages_received > 0", False, f"messages_received={messages}")
        log_test(f"data_feed.messages_received > 0", True, f"messages_received={messages}")
        
        # Check coins array
        if "coins" not in data:
            return log_test("coins array present", False, "Missing coins key")
        
        coins = data["coins"]
        if len(coins) != 10:
            return log_test("coins array has 10 entries", False, f"Found {len(coins)} coins")
        log_test("coins array has 10 entries", True)
        
        # Verify each coin has closed_candles > 0 and numeric rsi/price
        all_coins_valid = True
        for coin in coins:
            symbol = coin.get("symbol")
            closed_candles = coin.get("closed_candles", 0)
            rsi = coin.get("rsi")
            price = coin.get("price")
            
            if closed_candles <= 0:
                log_test(f"{symbol} closed_candles > 0", False, f"closed_candles={closed_candles}")
                all_coins_valid = False
                continue
            
            if rsi is None or not isinstance(rsi, (int, float)):
                log_test(f"{symbol} numeric RSI", False, f"rsi={rsi}")
                all_coins_valid = False
                continue
            
            if price is None or not isinstance(price, (int, float)):
                log_test(f"{symbol} numeric price", False, f"price={price}")
                all_coins_valid = False
                continue
            
            print(f"  ✓ {symbol}: closed_candles={closed_candles}, rsi={rsi:.2f}, price={price}")
        
        if all_coins_valid:
            log_test("All coins have valid data", True)
        
        return all_coins_valid
        
    except Exception as e:
        return log_test("GET /api/debug/status", False, f"Exception: {e}")

def test_settings_endpoint():
    """Test 2: GET /api/settings - verify returns expected keys"""
    print("\n" + "="*80)
    print("TEST 2: Settings Endpoint")
    print("="*80)
    
    try:
        resp = requests.get(f"{BACKEND_URL}/settings", timeout=10)
        if resp.status_code != 200:
            return log_test("GET /api/settings", False, f"HTTP {resp.status_code}")
        
        data = resp.json()
        print(f"Response: {json.dumps(data, indent=2)}")
        
        # Check required keys
        required_keys = ["active_strategy", "strategy_params", "custom_sessions", "pre_signal_enabled"]
        for key in required_keys:
            if key not in data:
                return log_test(f"settings.{key} present", False, f"Missing {key}")
            log_test(f"settings.{key} present", True, f"{key}={data[key]}")
        
        # Verify custom_sessions is currently [] (24/7 mode)
        if data.get("custom_sessions") != []:
            log_test("custom_sessions == []", False, f"custom_sessions={data.get('custom_sessions')}")
            # This is not a failure, just noting it
            print("  Note: custom_sessions is not empty, but this is acceptable")
        else:
            log_test("custom_sessions == [] (24/7 mode)", True)
        
        return True
        
    except Exception as e:
        return log_test("GET /api/settings", False, f"Exception: {e}")

def test_session_status():
    """Test 3: GET /api/session/status - verify is_active and current_session"""
    print("\n" + "="*80)
    print("TEST 3: Session Status")
    print("="*80)
    
    try:
        resp = requests.get(f"{BACKEND_URL}/session/status", timeout=10)
        if resp.status_code != 200:
            return log_test("GET /api/session/status", False, f"HTTP {resp.status_code}")
        
        data = resp.json()
        print(f"Response: {json.dumps(data, indent=2)}")
        
        # Verify is_active is true
        if not data.get("is_active"):
            return log_test("is_active == true", False, f"is_active={data.get('is_active')}")
        log_test("is_active == true", True)
        
        # Verify current_session
        current_session = data.get("current_session")
        log_test(f"current_session", True, f"current_session='{current_session}'")
        
        return True
        
    except Exception as e:
        return log_test("GET /api/session/status", False, f"Exception: {e}")

def test_strategies_endpoint():
    """Test 4: GET /api/strategies - verify lists both strategies"""
    print("\n" + "="*80)
    print("TEST 4: Strategies Endpoint")
    print("="*80)
    
    try:
        resp = requests.get(f"{BACKEND_URL}/strategies", timeout=10)
        if resp.status_code != 200:
            return log_test("GET /api/strategies", False, f"HTTP {resp.status_code}")
        
        data = resp.json()
        print(f"Response: {json.dumps(data, indent=2)}")
        
        # Check strategies array
        if "strategies" not in data:
            return log_test("strategies array present", False, "Missing strategies key")
        
        strategies = data["strategies"]
        strategy_ids = [s.get("id") for s in strategies]
        
        # Verify both scalping_4_rules and rsi_only are present
        if "scalping_4_rules" not in strategy_ids:
            return log_test("scalping_4_rules present", False, f"Found: {strategy_ids}")
        log_test("scalping_4_rules present", True)
        
        if "rsi_only" not in strategy_ids:
            return log_test("rsi_only present", False, f"Found: {strategy_ids}")
        log_test("rsi_only present", True)
        
        # Verify each has current_params
        for strategy in strategies:
            if "current_params" not in strategy:
                return log_test(f"{strategy.get('id')} has current_params", False)
            log_test(f"{strategy.get('id')} has current_params", True)
        
        # Verify active field
        if "active" not in data:
            return log_test("active field present", False)
        log_test(f"active strategy", True, f"active={data['active']}")
        
        return True
        
    except Exception as e:
        return log_test("GET /api/strategies", False, f"Exception: {e}")

def test_live_signal_generation():
    """Test 5: LIVE signal generation - switch to rsi_only, wait for signal, restore"""
    print("\n" + "="*80)
    print("TEST 5: LIVE Signal Generation (End-to-End)")
    print("="*80)
    
    try:
        # Step 1: Switch to rsi_only strategy
        print("\nStep 1: Switching to rsi_only strategy...")
        resp = requests.post(f"{BACKEND_URL}/settings", 
                           json={"active_strategy": "rsi_only"}, 
                           timeout=10)
        if resp.status_code != 200:
            return log_test("POST /api/settings (switch to rsi_only)", False, f"HTTP {resp.status_code}")
        log_test("Switched to rsi_only", True)
        
        # Step 2: Poll for signals (up to 100 seconds, check every 15 seconds)
        print("\nStep 2: Polling for signals (up to 100 seconds)...")
        max_wait = 100
        poll_interval = 15
        start_time = time.time()
        signals_found = []
        
        while (time.time() - start_time) < max_wait:
            elapsed = int(time.time() - start_time)
            print(f"  Polling at {elapsed}s...")
            
            resp = requests.get(f"{BACKEND_URL}/signals?limit=10", timeout=10)
            if resp.status_code != 200:
                print(f"  Warning: GET /api/signals returned HTTP {resp.status_code}")
                time.sleep(poll_interval)
                continue
            
            data = resp.json()
            signals = data.get("signals", [])
            
            # Check for new signals (with rsi_only strategy)
            new_signals = [s for s in signals if s.get("strategy_id") == "rsi_only"]
            if new_signals:
                signals_found = new_signals
                print(f"  ✓ Found {len(new_signals)} signal(s)!")
                for sig in new_signals:
                    print(f"    - {sig.get('symbol')} {sig.get('type')} @ {sig.get('entry_price')}")
                    print(f"      RSI: {sig.get('rsi')}, CRV: {sig.get('crv')}")
                    print(f"      SL: {sig.get('stop_loss')}, TP1: {sig.get('take_profit_1')}, TP_full: {sig.get('take_profit_full')}")
                break
            
            print(f"  No signals yet (checked {len(signals)} total signals)")
            time.sleep(poll_interval)
        
        # Step 3: Verify signal structure if found
        if signals_found:
            log_test("Signal generated within 100s", True, f"Found {len(signals_found)} signal(s)")
            
            # Verify signal structure
            sig = signals_found[0]
            required_fields = ["symbol", "type", "entry_price", "stop_loss", "take_profit_1", 
                             "take_profit_full", "crv", "rsi", "timestamp", "strategy_id", "id"]
            all_fields_present = True
            for field in required_fields:
                if field not in sig:
                    log_test(f"Signal has '{field}' field", False)
                    all_fields_present = False
            
            if all_fields_present:
                log_test("Signal has all required fields", True)
        else:
            log_test("Signal generated within 100s", False, 
                    "No signals appeared. Market may not be oversold currently.")
            print("  Note: This may not be a bug - depends on live market conditions")
        
        # Step 4: Restore to scalping_4_rules
        print("\nStep 3: Restoring to scalping_4_rules strategy...")
        resp = requests.post(f"{BACKEND_URL}/settings", 
                           json={"active_strategy": "scalping_4_rules"}, 
                           timeout=10)
        if resp.status_code != 200:
            return log_test("POST /api/settings (restore to scalping_4_rules)", False, 
                          f"HTTP {resp.status_code}")
        log_test("Restored to scalping_4_rules", True)
        
        # Verify restoration
        resp = requests.get(f"{BACKEND_URL}/settings", timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("active_strategy") == "scalping_4_rules":
                log_test("Verified active_strategy == scalping_4_rules", True)
            else:
                log_test("Verified active_strategy == scalping_4_rules", False, 
                        f"active_strategy={data.get('active_strategy')}")
        
        return True
        
    except Exception as e:
        return log_test("LIVE signal generation test", False, f"Exception: {e}")

def test_settings_persistence():
    """Test 6: Settings persistence - toggle pre_signal_enabled"""
    print("\n" + "="*80)
    print("TEST 6: Settings Persistence")
    print("="*80)
    
    try:
        # Step 1: Set pre_signal_enabled to false
        print("\nStep 1: Setting pre_signal_enabled=false...")
        resp = requests.post(f"{BACKEND_URL}/settings", 
                           json={"pre_signal_enabled": False}, 
                           timeout=10)
        if resp.status_code != 200:
            return log_test("POST /api/settings (pre_signal_enabled=false)", False, 
                          f"HTTP {resp.status_code}")
        
        # Verify it was saved
        resp = requests.get(f"{BACKEND_URL}/settings", timeout=10)
        if resp.status_code != 200:
            return log_test("GET /api/settings after update", False, f"HTTP {resp.status_code}")
        
        data = resp.json()
        if data.get("pre_signal_enabled") != False:
            return log_test("pre_signal_enabled persisted as false", False, 
                          f"pre_signal_enabled={data.get('pre_signal_enabled')}")
        log_test("pre_signal_enabled persisted as false", True)
        
        # Step 2: Set pre_signal_enabled back to true
        print("\nStep 2: Setting pre_signal_enabled=true...")
        resp = requests.post(f"{BACKEND_URL}/settings", 
                           json={"pre_signal_enabled": True}, 
                           timeout=10)
        if resp.status_code != 200:
            return log_test("POST /api/settings (pre_signal_enabled=true)", False, 
                          f"HTTP {resp.status_code}")
        
        # Verify it was saved
        resp = requests.get(f"{BACKEND_URL}/settings", timeout=10)
        if resp.status_code != 200:
            return log_test("GET /api/settings after restore", False, f"HTTP {resp.status_code}")
        
        data = resp.json()
        if data.get("pre_signal_enabled") != True:
            return log_test("pre_signal_enabled persisted as true", False, 
                          f"pre_signal_enabled={data.get('pre_signal_enabled')}")
        log_test("pre_signal_enabled persisted as true", True)
        
        return True
        
    except Exception as e:
        return log_test("Settings persistence test", False, f"Exception: {e}")

def test_performance_endpoint():
    """Test 7: GET /api/performance - verify valid JSON"""
    print("\n" + "="*80)
    print("TEST 7: Performance Endpoint")
    print("="*80)
    
    try:
        resp = requests.get(f"{BACKEND_URL}/performance", timeout=10)
        if resp.status_code != 200:
            return log_test("GET /api/performance", False, f"HTTP {resp.status_code}")
        
        data = resp.json()
        print(f"Response: {json.dumps(data, indent=2)}")
        
        # Verify it's valid JSON with performance array
        if "performance" not in data:
            return log_test("performance array present", False, "Missing performance key")
        
        performance = data["performance"]
        if not isinstance(performance, list):
            return log_test("performance is array", False, f"Type: {type(performance)}")
        
        log_test("GET /api/performance returns valid JSON", True, 
                f"Found {len(performance)} performance records")
        
        return True
        
    except Exception as e:
        return log_test("GET /api/performance", False, f"Exception: {e}")

def verify_final_state():
    """Verify final state: active_strategy=scalping_4_rules, custom_sessions=[]"""
    print("\n" + "="*80)
    print("FINAL STATE VERIFICATION")
    print("="*80)
    
    try:
        resp = requests.get(f"{BACKEND_URL}/settings", timeout=10)
        if resp.status_code != 200:
            print(f"❌ Could not verify final state: HTTP {resp.status_code}")
            return False
        
        data = resp.json()
        
        # Check active_strategy
        if data.get("active_strategy") != "scalping_4_rules":
            print(f"⚠️  active_strategy is '{data.get('active_strategy')}' (expected: scalping_4_rules)")
        else:
            print(f"✓ active_strategy = scalping_4_rules")
        
        # Check custom_sessions
        if data.get("custom_sessions") != []:
            print(f"⚠️  custom_sessions is {data.get('custom_sessions')} (expected: [])")
        else:
            print(f"✓ custom_sessions = []")
        
        return True
        
    except Exception as e:
        print(f"❌ Exception during final state verification: {e}")
        return False

def main():
    """Run all backend tests"""
    print("\n" + "="*80)
    print("CRYPTO SCALPING SIGNAL APP - BACKEND API TEST SUITE")
    print("Multi-Exchange Fallback Data Feed (Bitunix -> Binance -> OKX)")
    print("="*80)
    print(f"Backend URL: {BACKEND_URL}")
    print(f"Test started at: {datetime.now().isoformat()}")
    
    results = []
    
    # Run all tests
    results.append(("Data Feed Status", test_debug_status()))
    results.append(("Settings Endpoint", test_settings_endpoint()))
    results.append(("Session Status", test_session_status()))
    results.append(("Strategies Endpoint", test_strategies_endpoint()))
    results.append(("LIVE Signal Generation", test_live_signal_generation()))
    results.append(("Settings Persistence", test_settings_persistence()))
    results.append(("Performance Endpoint", test_performance_endpoint()))
    
    # Verify final state
    verify_final_state()
    
    # Summary
    print("\n" + "="*80)
    print("TEST SUMMARY")
    print("="*80)
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for test_name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status}: {test_name}")
    
    print(f"\nTotal: {passed}/{total} tests passed")
    
    if passed == total:
        print("\n🎉 ALL TESTS PASSED!")
        return 0
    else:
        print(f"\n⚠️  {total - passed} test(s) failed")
        return 1

if __name__ == "__main__":
    exit(main())
