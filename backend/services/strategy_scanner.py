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
    "custom_sessions": [
        {"start": "09:00", "end": "12:00", "name": "London", "enabled": True},
        {"start": "15:30", "end": "18:30", "name": "US", "enabled": True}
    ],
    "pre_signal_enabled": True,
}


class StrategyScanner:
    """Coordinates strategy checks and session management"""
    
    def __init__(self):
        self.indicators = TechnicalIndicators()
        self.candle_buffer = {}
        self.last_pre_signal = {}
        self.settings = dict(DEFAULT_SETTINGS)
    
    def update_settings(self, new_settings: Dict):
        for key, value in new_settings.items():
            if key in DEFAULT_SETTINGS:
                self.settings[key] = value
        logger.info(f"Settings updated: {self.settings}")
    
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
    
    def add_candle(self, symbol: str, candle: Dict):
        if symbol not in self.candle_buffer:
            self.candle_buffer[symbol] = []
        
        self.candle_buffer[symbol].append(candle)
        
        if len(self.candle_buffer[symbol]) > 100:
            self.candle_buffer[symbol] = self.candle_buffer[symbol][-100:]
    
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
