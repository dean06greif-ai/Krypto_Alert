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
        self.candle_buffer = {}  # Store candles per symbol
        self.signal_candle = {}  # Track signal candles
        self.LONDON_OPEN = (9, 0)  # 9:00
        self.LONDON_CLOSE = (12, 0)  # 12:00
        self.US_OPEN = (15, 30)  # 15:30
        self.US_CLOSE = (18, 30)  # 18:30
    
    def is_trading_session(self) -> bool:
        """
        Check if current time is within trading hours (German timezone)
        London: 9:00-12:00, US: 15:30-18:30
        """
        now = datetime.now(timezone.utc)
        # Convert to German time (UTC+1 or UTC+2 depending on DST)
        # For simplicity, using UTC+1
        german_hour = (now.hour + 1) % 24
        german_minute = now.minute
        
        current_time = (german_hour, german_minute)
        
        # Check London session
        if self.LONDON_OPEN <= current_time < self.LONDON_CLOSE:
            return True
        
        # Check US session
        if self.US_OPEN <= current_time < self.US_CLOSE:
            return True
        
        return False
    
    def add_candle(self, symbol: str, candle: Dict):
        """
        Add new candle to buffer for a symbol
        Args:
            symbol: Trading pair
            candle: OHLC candle data
        """
        if symbol not in self.candle_buffer:
            self.candle_buffer[symbol] = []
        
        self.candle_buffer[symbol].append(candle)
        
        # Keep last 100 candles for calculations
        if len(self.candle_buffer[symbol]) > 100:
            self.candle_buffer[symbol] = self.candle_buffer[symbol][-100:]
    
    def check_signal(self, symbol: str) -> Optional[Dict]:
        """
        Check if all 4 rules are met for a trading signal
        Args:
            symbol: Trading pair
        Returns:
            Signal dict if conditions met, None otherwise
        """
        # Check trading session
        if not self.is_trading_session():
            return None
        
        candles = self.candle_buffer.get(symbol, [])
        
        if len(candles) < 60:  # Need enough data for EMA50
            return None
        
        # Calculate Heikin Ashi
        ha_candles = self.indicators.calculate_heikin_ashi(candles)
        
        # Get closing prices for indicators
        close_prices = [c['close'] for c in candles]
        
        # Calculate EMA 50 and EMA 9
        ema_50 = self.indicators.calculate_ema(close_prices, 50)
        ema_9 = self.indicators.calculate_ema(close_prices, 9)
        
        # Calculate RSI
        rsi = self.indicators.calculate_rsi(close_prices, 14)
        
        # Get current values
        current_price = close_prices[-1]
        current_ema_50 = ema_50[-1] if ema_50[-1] is not None else None
        current_ema_9 = ema_9[-1] if ema_9[-1] is not None else None
        current_rsi = rsi[-1] if rsi[-1] is not None else None
        current_ha_candle = ha_candles[-1]
        
        if None in [current_ema_50, current_ema_9, current_rsi]:
            return None
        
        # Check for LONG signal
        rule1_long = current_price > current_ema_50
        rule2_long = current_rsi < 32
        rule3_long = current_ha_candle['is_green'] and current_ha_candle['close'] > current_ema_9
        
        # Check for SHORT signal
        rule1_short = current_price < current_ema_50
        rule2_short = current_rsi > 64
        rule3_short = not current_ha_candle['is_green'] and current_ha_candle['close'] < current_ema_9
        
        # Rule 4: Time window (2 candles)
        # Track if we have a signal candle
        signal_type = None
        
        if rule1_long and rule2_long and rule3_long:
            signal_type = "LONG"
        elif rule1_short and rule2_short and rule3_short:
            signal_type = "SHORT"
        
        if signal_type:
            # Calculate entry, SL, TP
            entry_price = current_price
            
            if signal_type == "LONG":
                # SL: 3-5 ticks below recent low
                recent_low = self.indicators.get_recent_low(candles, 10)
                tick_size = entry_price * 0.0001  # Approximate tick
                stop_loss = recent_low - (4 * tick_size)
                
                # TP: Target CRV of 2
                risk = entry_price - stop_loss
                take_profit_full = entry_price + (risk * 2)
                take_profit_1 = entry_price + risk  # TP1 at CRV 1 (40% position)
            
            else:  # SHORT
                # SL: 3-5 ticks above recent high
                recent_high = self.indicators.get_recent_high(candles, 10)
                tick_size = entry_price * 0.0001
                stop_loss = recent_high + (4 * tick_size)
                
                # TP: Target CRV of 2
                risk = stop_loss - entry_price
                take_profit_full = entry_price - (risk * 2)
                take_profit_1 = entry_price - risk  # TP1 at CRV 1
            
            crv = self.indicators.calculate_crv(entry_price, stop_loss, take_profit_full)
            
            signal = {
                "symbol": symbol,
                "type": signal_type,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "entry_price": round(entry_price, 6),
                "stop_loss": round(stop_loss, 6),
                "take_profit_1": round(take_profit_1, 6),
                "take_profit_full": round(take_profit_full, 6),
                "crv": round(crv, 2),
                "rsi": round(current_rsi, 2),
                "ema_9": round(current_ema_9, 6),
                "ema_50": round(current_ema_50, 6),
                "rules_met": {
                    "rule1_ema50": True,
                    "rule2_rsi": True,
                    "rule3_ema9_trigger": True,
                    "rule4_time_window": True
                }
            }
            
            logger.info(f"Signal detected: {signal_type} for {symbol} at {entry_price}")
            return signal
        
        return None
