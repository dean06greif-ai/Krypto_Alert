"""
Strategy Scanner - Coordinates strategy execution across coins.
Supports multiple strategies via the strategy registry.
"""
from datetime import datetime, timezone
from typing import Dict, List, Optional
import logging
from services.technical_indicators import TechnicalIndicators
from strategies.registry import registry

logger = logging.getLogger(__name__)

DEFAULT_SETTINGS = {
    "active_strategy": "scalping_4_rules",
    "strategy_params": {},  # {strategy_id: {param_key: value}}
    "custom_sessions": [],  # empty => 24/7 mode (default: never miss a signal)
    "pre_signal_enabled": True,
    "notifications": {},  # {symbol: bool} - missing => enabled. Per-instrument alert toggle
}


class StrategyScanner:
    """Coordinates strategy checks and session management"""
    
    def __init__(self):
        self.indicators = TechnicalIndicators()
        self.candle_buffer = {}   # closed candles per symbol
        self.forming = {}         # current forming candle per symbol
        self.last_pre_signal = {}
        self.settings = dict(DEFAULT_SETTINGS)

    def bootstrap(self, symbol: str, candles: list):
        """Seed the buffer with historical closed candles (oldest-first)."""
        if not candles:
            return
        self.candle_buffer[symbol] = candles[-100:]
        # The most recent historical candle may still be forming; track it separately
        self.forming[symbol] = dict(candles[-1])
        logger.info(f"Bootstrapped {symbol} with {len(self.candle_buffer[symbol])} closed candles")

    def debug_snapshot(self, symbol: str) -> Dict:
        """Diagnostic snapshot of live indicator state for a coin (no signal required)."""
        candles = self.candle_buffer.get(symbol, [])
        info = {
            "symbol": symbol,
            "closed_candles": len(candles),
            "last_candle_time": candles[-1]["timestamp"] if candles else None,
            "price": candles[-1]["close"] if candles else None,
            "rsi": None,
            "ema_fast": None,
            "ema_slow": None,
        }
        if len(candles) >= 15:
            closes = [c["close"] for c in candles]
            rsi = self.indicators.calculate_rsi(closes, 14)
            ema_fast = self.indicators.calculate_ema(closes, 9)
            ema_slow = self.indicators.calculate_ema(closes, 50)
            info["rsi"] = round(rsi[-1], 2) if rsi and rsi[-1] is not None else None
            info["ema_fast"] = round(ema_fast[-1], 6) if ema_fast and ema_fast[-1] is not None else None
            info["ema_slow"] = round(ema_slow[-1], 6) if ema_slow and ema_slow[-1] is not None else None
        return info
    
    def update_settings(self, new_settings: Dict):
        for key, value in new_settings.items():
            if key in DEFAULT_SETTINGS:
                self.settings[key] = value
        logger.info(f"Settings updated: {self.settings}")

    def is_notify_enabled(self, symbol: str) -> bool:
        """Per-instrument notification toggle. Missing entry => enabled."""
        return self.settings.get("notifications", {}).get(symbol, True)
    
    def get_active_strategy(self):
        """Get the currently active strategy"""
        strategy_id = self.settings.get("active_strategy", "scalping_4_rules")
        strategy = registry.get(strategy_id)
        if not strategy:
            logger.warning(f"Strategy {strategy_id} not found, falling back to default")
            return registry.get_default()
        return strategy
    
    def _parse_time(self, time_str: str) -> tuple:
        try:
            parts = time_str.split(':')
            return (int(parts[0]), int(parts[1]))
        except Exception:
            return (0, 0)
    
    def is_trading_session(self) -> bool:
        sessions = self.settings.get("custom_sessions", [])
        enabled_sessions = [s for s in sessions if s.get("enabled", True)]
        
        # No sessions → 24/7 mode
        if not enabled_sessions:
            return True
        
        now = datetime.now(timezone.utc)
        german_hour = (now.hour + 1) % 24
        german_minute = now.minute
        current_time = (german_hour, german_minute)
        
        for session in enabled_sessions:
            start = self._parse_time(session.get("start", "00:00"))
            end = self._parse_time(session.get("end", "23:59"))
            
            if start <= current_time < end:
                return True
        
        return False
    
    def get_current_session(self) -> str:
        sessions = self.settings.get("custom_sessions", [])
        enabled_sessions = [s for s in sessions if s.get("enabled", True)]
        
        if not enabled_sessions:
            return "24/7 Mode"
        
        now = datetime.now(timezone.utc)
        german_hour = (now.hour + 1) % 24
        german_minute = now.minute
        current_time = (german_hour, german_minute)
        
        for session in enabled_sessions:
            start = self._parse_time(session.get("start", "00:00"))
            end = self._parse_time(session.get("end", "23:59"))
            
            if start <= current_time < end:
                return session.get("name", "Custom")
        
        return "Closed"
    
    def add_closed_candle(self, symbol: str, candle: Dict) -> bool:
        """
        Append an already-CLOSED candle (from REST polling). Deduplicates by
        timestamp so each 1-minute candle is only evaluated once.
        Returns True if this is a genuinely new closed candle.
        """
        buf = self.candle_buffer.setdefault(symbol, [])
        if buf and candle["timestamp"] <= buf[-1]["timestamp"]:
            return False
        buf.append(candle)
        if len(buf) > 100:
            self.candle_buffer[symbol] = buf[-100:]
        return True

    def add_candle(self, symbol: str, candle: Dict) -> bool:
        """
        Ingest a live candle snapshot.

        Bitunix streams ~2 snapshots/second for the *current forming* minute.
        We keep only distinct closed 1-minute candles in candle_buffer.

        Returns True when a candle has just CLOSED (i.e. a new minute started),
        which is the moment the strategy should be evaluated.
        """
        forming = self.forming.get(symbol)

        # First candle ever for this symbol
        if forming is None:
            self.forming[symbol] = candle
            return False

        # Same minute -> just update the forming candle (no evaluation)
        if candle["timestamp"] == forming["timestamp"]:
            self.forming[symbol] = candle
            return False

        # New minute -> the previous forming candle is now CLOSED
        if candle["timestamp"] > forming["timestamp"]:
            buf = self.candle_buffer.setdefault(symbol, [])
            # Avoid duplicating a candle already present (e.g. from bootstrap)
            if buf and buf[-1]["timestamp"] == forming["timestamp"]:
                buf[-1] = forming
            else:
                buf.append(forming)
            if len(buf) > 100:
                self.candle_buffer[symbol] = buf[-100:]
            # Start tracking the new forming candle
            self.forming[symbol] = candle
            return True

        # Out-of-order / stale message
        return False
    
    def check_signal(self, symbol: str) -> Optional[Dict]:
        """Check for signal using active strategy"""
        if not self.is_trading_session():
            return None
        
        candles = self.candle_buffer.get(symbol, [])
        
        # Get active strategy and check
        strategy = self.get_active_strategy()
        result = strategy.check_signal(candles, symbol, self.settings)
        
        if not result:
            return None
        
        # Deduplicate pre-signals
        if result.get("signal_class") == "PRE_SIGNAL":
            last_pre = self.last_pre_signal.get(symbol)
            now_ts = datetime.now(timezone.utc).timestamp()
            if last_pre and (now_ts - last_pre) < 300:
                return None
            self.last_pre_signal[symbol] = now_ts
        
        # Enrich signal with time metadata
        now = datetime.now(timezone.utc)
        german_hour = (now.hour + 1) % 24
        weekday = now.weekday()
        
        signal = {
            **result,
            "symbol": symbol,
            "timestamp": now.isoformat(),
            "hour": german_hour,
            "weekday": weekday,
            "session": self.get_current_session(),
            "strategy_id": strategy.STRATEGY_ID,
            "strategy_name": strategy.STRATEGY_NAME,
        }
        
        logger.info(f"{'PRE-' if result.get('signal_class') == 'PRE_SIGNAL' else ''}Signal ({strategy.STRATEGY_ID}): {result['type']} for {symbol} at {result['entry_price']}")
        return signal
