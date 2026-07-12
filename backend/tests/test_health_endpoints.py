"""
Backend tests for Crypto Scalping Scanner REST endpoints.
Focus: /api/health keepalive endpoint + related read-only endpoints.
"""
import os
import re
import requests
from datetime import datetime

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")
if not BASE_URL:
    # Fallback to reading frontend/.env directly
    env_path = "/app/frontend/.env"
    if os.path.exists(env_path):
        with open(env_path) as f:
            for line in f:
                if line.startswith("REACT_APP_BACKEND_URL="):
                    BASE_URL = line.split("=", 1)[1].strip().rstrip("/")
                    break

TIMEOUT = 15


# ---------- /api/health endpoint ----------
class TestHealthEndpoint:
    """Tests for the lightweight /api/health keepalive endpoint."""

    def test_health_status_code_200(self):
        r = requests.get(f"{BASE_URL}/api/health", timeout=TIMEOUT)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"

    def test_health_returns_json(self):
        r = requests.get(f"{BASE_URL}/api/health", timeout=TIMEOUT)
        assert r.headers.get("content-type", "").startswith("application/json")
        data = r.json()
        assert isinstance(data, dict)

    def test_health_required_fields_present(self):
        r = requests.get(f"{BASE_URL}/api/health", timeout=TIMEOUT)
        data = r.json()
        for field in ["status", "timestamp", "websocket_clients", "session_active"]:
            assert field in data, f"Missing field '{field}' in response: {data}"

    def test_health_status_alive(self):
        r = requests.get(f"{BASE_URL}/api/health", timeout=TIMEOUT)
        data = r.json()
        assert data["status"] == "alive", f"Expected status='alive', got {data['status']}"

    def test_health_timestamp_iso_format(self):
        r = requests.get(f"{BASE_URL}/api/health", timeout=TIMEOUT)
        data = r.json()
        ts = data["timestamp"]
        assert isinstance(ts, str)
        # Should parse as ISO 8601
        parsed = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert parsed is not None
        # Timestamp should be recent (within 60 seconds)
        now = datetime.now(parsed.tzinfo)
        diff = abs((now - parsed).total_seconds())
        assert diff < 60, f"Timestamp not recent, diff={diff}s"

    def test_health_websocket_clients_is_integer(self):
        r = requests.get(f"{BASE_URL}/api/health", timeout=TIMEOUT)
        data = r.json()
        assert isinstance(data["websocket_clients"], int), (
            f"websocket_clients should be int, got {type(data['websocket_clients'])}"
        )
        assert data["websocket_clients"] >= 0

    def test_health_session_active_is_boolean(self):
        r = requests.get(f"{BASE_URL}/api/health", timeout=TIMEOUT)
        data = r.json()
        assert isinstance(data["session_active"], bool), (
            f"session_active should be bool, got {type(data['session_active'])}"
        )

    def test_health_is_lightweight_fast(self):
        """Health endpoint should be fast (< 3s) since it's used for keepalive."""
        import time
        start = time.time()
        r = requests.get(f"{BASE_URL}/api/health", timeout=TIMEOUT)
        elapsed = time.time() - start
        assert r.status_code == 200
        assert elapsed < 3.0, f"Health endpoint too slow: {elapsed:.2f}s"


# ---------- Root endpoint ----------
# NOTE: On the preview environment the K8s ingress routes non-/api/* paths to
# the React frontend (port 3000). The backend root ("/") is therefore only
# reachable directly on localhost:8001. We test it there since it's the
# actual backend endpoint behavior we want to verify.
LOCAL_BACKEND = "http://localhost:8001"


class TestRootEndpoint:
    def test_root_200(self):
        r = requests.get(f"{LOCAL_BACKEND}/", timeout=TIMEOUT)
        assert r.status_code == 200

    def test_root_returns_expected_fields(self):
        r = requests.get(f"{LOCAL_BACKEND}/", timeout=TIMEOUT)
        data = r.json()
        assert data.get("app") == "Crypto Scalping Scanner"
        assert data.get("status") == "running"
        assert "coins_tracked" in data
        assert "active_signals" in data
        assert isinstance(data["coins_tracked"], int)
        assert data["coins_tracked"] == 10


# ---------- /api/coins endpoint ----------
class TestCoinsEndpoint:
    def test_coins_200(self):
        r = requests.get(f"{BASE_URL}/api/coins", timeout=TIMEOUT)
        assert r.status_code == 200

    def test_coins_list_of_10(self):
        r = requests.get(f"{BASE_URL}/api/coins", timeout=TIMEOUT)
        data = r.json()
        assert "coins" in data
        assert isinstance(data["coins"], list)
        assert len(data["coins"]) == 10, f"Expected 10 coins, got {len(data['coins'])}"

    def test_coins_contain_expected_symbols(self):
        r = requests.get(f"{BASE_URL}/api/coins", timeout=TIMEOUT)
        coins = r.json()["coins"]
        for expected in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
            assert expected in coins, f"Missing expected coin: {expected}"


# ---------- /api/session/status endpoint ----------
class TestSessionStatusEndpoint:
    def test_session_status_200(self):
        r = requests.get(f"{BASE_URL}/api/session/status", timeout=TIMEOUT)
        assert r.status_code == 200

    def test_session_status_fields(self):
        r = requests.get(f"{BASE_URL}/api/session/status", timeout=TIMEOUT)
        data = r.json()
        assert "is_active" in data
        assert isinstance(data["is_active"], bool)
        assert "current_time_utc" in data
        assert "sessions" in data
        assert isinstance(data["sessions"], dict)
        assert "london" in data["sessions"]
        assert "us" in data["sessions"]

    def test_session_current_time_iso(self):
        r = requests.get(f"{BASE_URL}/api/session/status", timeout=TIMEOUT)
        data = r.json()
        parsed = datetime.fromisoformat(data["current_time_utc"].replace("Z", "+00:00"))
        assert parsed is not None
