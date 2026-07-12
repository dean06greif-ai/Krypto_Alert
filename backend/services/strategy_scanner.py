from datetime import datetime, timezone
from typing import Dict, List, Optional
import logging
from services.technical_indicators import TechnicalIndicators

logger = logging.getLogger(__name__)

class StrategyScanner:
    """
    Scalping strategy scanner based on 4 rules:
    1. EMA 50: Price above = Long, Price below = Short
    2. RSI: RSI < 32 = Long, RSI > 64 = Short
    3. EMA 9 Trigger: Green HA candle closes above EMA9 = Long, Red HA candle closes below EMA9 = Short
    4. Time: 2 candles window (signal candle + 1 confirmation)
    """
    
    def __init__(self):
        self.indicators = TechnicalIndicators()
        self.candle_buffer = {}
        self.signal_candle = {}
        self.last_pre_signal = {}  # Track pre-signals to avoid duplicates
        self.LONDON_OPEN = (9, 0)
        self.LONDON_CLOSE = (12, 0)
        self.US_OPEN = (15, 30)
        self.US_CLOSE = (18, 30)
        
        # Settings (can be modified via API)
        self.settings = {
            "test_mode_24_7": False,  # If True, ignore session times
            "pre_signal_enabled": True,  # Send early warnings
            "pre_signal_rsi_zone": 4,  # RSI can be 4 points off (28-36 or 60-68)
        }
    
    def update_settings(self, new_settings: Dict):
        """Update scanner settings"""
        self.settings.update(new_settings)
        logger.info(f"Settings updated: {self.settings}")
    
    def is_trading_session(self) -> bool:
        """Check if in trading session or 24/7 test mode"""
        if self.settings.get("test_mode_24_7", False):
            return True
        
        now = datetime.now(timezone.utc)
        german_hour = (now.hour + 1) % 24
        german_minute = now.minute
        current_time = (german_hour, german_minute)
        
        if self.LONDON_OPEN <= current_time < self.LONDON_CLOSE:
            return True
        if self.US_OPEN <= current_time < self.US_CLOSE:
            return True
        return False
    
    def get_current_session(self) -> str:
        """Get name of current session"""
        if self.settings.get("test_mode_24_7", False):
            return "24/7 Test Mode"
        
        now = datetime.now(timezone.utc)
        german_hour = (now.hour + 1) % 24
        german_minute = now.minute
        current_time = (german_hour, german_minute)
        
        if self.LONDON_OPEN <= current_time < self.LONDON_CLOSE:
            return "London"
        if self.US_OPEN <= current_time < self.US_CLOSE:
            return "US"
        return "Closed"
    
    def add_candle(self, symbol: str, candle: Dict):
        """Add new candle to buffer for a symbol"""
        if symbol not in self.candle_buffer:
            self.candle_buffer[symbol] = []
        
        self.candle_buffer[symbol].append(candle)
        
        if len(self.candle_buffer[symbol]) > 100:
            self.candle_buffer[symbol] = self.candle_buffer[symbol][-100:]
    
    def check_signal(self, symbol: str) -> Optional[Dict]:
        """
        Check if all 4 rules are met for a trading signal
        Returns signal dict with type='SIGNAL' or type='PRE_SIGNAL'
        """
        if not self.is_trading_session():
            return None
        
        candles = self.candle_buffer.get(symbol, [])
        
        if len(candles) < 60:
            return None
        
        ha_candles = self.indicators.calculate_heikin_ashi(candles)
        close_prices = [c['close'] for c in candles]
        
        ema_50 = self.indicators.calculate_ema(close_prices, 50)
        ema_9 = self.indicators.calculate_ema(close_prices, 9)
        rsi = self.indicators.calculate_rsi(close_prices, 14)
        
        current_price = close_prices[-1]
        current_ema_50 = ema_50[-1] if ema_50[-1] is not None else None
        current_ema_9 = ema_9[-1] if ema_9[-1] is not None else None
        current_rsi = rsi[-1] if rsi[-1] is not None else None
        current_ha_candle = ha_candles[-1]
        
        if None in [current_ema_50, current_ema_9, current_rsi]:
            return None
        
        # FULL SIGNAL CHECK (all 4 rules)
        rule1_long = current_price > current_ema_50
        rule2_long = current_rsi < 32
        rule3_long = current_ha_candle['is_green'] and current_ha_candle['close'] > current_ema_9
        
        rule1_short = current_price < current_ema_50
        rule2_short = current_rsi > 64
        rule3_short = not current_ha_candle['is_green'] and current_ha_candle['close'] < current_ema_9
        
        signal_type = None
        is_pre_signal = False
        rules_met_count = 0
        
        # Check FULL signal
        if rule1_long and rule2_long and rule3_long:
            signal_type = "LONG"
            rules_met_count = 4
        elif rule1_short and rule2_short and rule3_short:
            signal_type = "SHORT"
            rules_met_count = 4
        
        # Check PRE-SIGNAL (3 rules met + close to 4th) - only if not already full signal
        elif self.settings.get("pre_signal_enabled", True):
            rsi_zone = self.settings.get("pre_signal_rsi_zone", 4)
            
            # Pre-LONG: EMA50 + close-to-RSI + EMA9 trigger
            if rule1_long and rule3_long and (current_rsi < 32 + rsi_zone):
                signal_type = "LONG"
                is_pre_signal = True
                rules_met_count = 3
            # Pre-LONG variant: EMA50 + RSI + close-to-EMA9
            elif rule1_long and rule2_long and current_ha_candle['is_green'] and \
                 abs(current_ha_candle['close'] - current_ema_9) / current_ema_9 < 0.001:
                signal_type = "LONG"
                is_pre_signal = True
                rules_met_count = 3
            # Pre-SHORT: EMA50 + close-to-RSI + EMA9 trigger
            elif rule1_short and rule3_short and (current_rsi > 64 - rsi_zone):
                signal_type = "SHORT"
                is_pre_signal = True
                rules_met_count = 3
            # Pre-SHORT variant: EMA50 + RSI + close-to-EMA9
            elif rule1_short and rule2_short and not current_ha_candle['is_green'] and \
                 abs(current_ha_candle['close'] - current_ema_9) / current_ema_9 < 0.001:
                signal_type = "SHORT"
                is_pre_signal = True
                rules_met_count = 3
        
        if signal_type:
            # Deduplicate pre-signals (only one per symbol per 5 minutes)
            if is_pre_signal:
                last_pre = self.last_pre_signal.get(symbol)
                now_ts = datetime.now(timezone.utc).timestamp()
                if last_pre and (now_ts - last_pre) < 300:  # 5 min
                    return None
                self.last_pre_signal[symbol] = now_ts
            
            entry_price = current_price
            
            if signal_type == "LONG":
                recent_low = self.indicators.get_recent_low(candles, 10)
                tick_size = entry_price * 0.0001
                stop_loss = recent_low - (4 * tick_size)
                risk = entry_price - stop_loss
                take_profit_full = entry_price + (risk * 2)
                take_profit_1 = entry_price + risk
            else:
                recent_high = self.indicators.get_recent_high(candles, 10)
                tick_size = entry_price * 0.0001
                stop_loss = recent_high + (4 * tick_size)
                risk = stop_loss - entry_price
                take_profit_full = entry_price - (risk * 2)
                take_profit_1 = entry_price - risk
            
            crv = self.indicators.calculate_crv(entry_price, stop_loss, take_profit_full)
            
            # Time-based data for analytics
            now = datetime.now(timezone.utc)
            german_hour = (now.hour + 1) % 24
            weekday = now.weekday()  # 0=Monday, 6=Sunday
            
            signal = {
                "symbol": symbol,
                "type": signal_type,
                "signal_class": "PRE_SIGNAL" if is_pre_signal else "SIGNAL",
                "timestamp": now.isoformat(),
                "hour": german_hour,
                "weekday": weekday,
                "session": self.get_current_session(),
                "entry_price": round(entry_price, 6),
                "stop_loss": round(stop_loss, 6),
                "take_profit_1": round(take_profit_1, 6),
                "take_profit_full": round(take_profit_full, 6),
                "crv": round(crv, 2),
                "rsi": round(current_rsi, 2),
                "ema_9": round(current_ema_9, 6),
                "ema_50": round(current_ema_50, 6),
                "rules_met_count": rules_met_count,
                "rules_met": {
                    "rule1_ema50": rule1_long if signal_type == "LONG" else rule1_short,
                    "rule2_rsi": rule2_long if signal_type == "LONG" else rule2_short,
                    "rule3_ema9_trigger": rule3_long if signal_type == "LONG" else rule3_short,
                    "rule4_time_window": True
                }
            }
            
            logger.info(f"{'PRE-' if is_pre_signal else ''}Signal detected: {signal_type} for {symbol} at {entry_price}")
            return signal
        
        return None
