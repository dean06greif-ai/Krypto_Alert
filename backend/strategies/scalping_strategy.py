"""
Scalping strategy with 4 rules:
1. EMA 50: Price above = Long, Price below = Short
2. RSI: RSI < 32 = Long, RSI > 64 = Short
3. EMA 9 Trigger: Green HA candle above EMA9 = Long, Red HA below = Short
4. Time: 2 candles window (signal candle + 1 confirmation)

Uses Heikin Ashi candles.
"""
from typing import Dict, List, Optional
from datetime import datetime, timezone
from strategies.base_strategy import BaseStrategy


class ScalpingStrategy(BaseStrategy):
    STRATEGY_ID = "scalping_4_rules"
    STRATEGY_NAME = "Scalping 4-Regeln (Heikin Ashi)"
    STRATEGY_DESCRIPTION = "EMA 50 + RSI + EMA 9 Trigger auf Heikin Ashi Candles. Standard-Scalping-Strategie."
    STRATEGY_TIMEFRAME = "1m"
    
    def check_signal(self, candles: List[Dict], symbol: str, settings: Dict) -> Optional[Dict]:
        """Check all 4 scalping rules"""
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
        
        # Rules
        rule1_long = current_price > current_ema_50
        rule2_long = current_rsi < 32
        rule3_long = current_ha_candle['is_green'] and current_ha_candle['close'] > current_ema_9
        
        rule1_short = current_price < current_ema_50
        rule2_short = current_rsi > 64
        rule3_short = not current_ha_candle['is_green'] and current_ha_candle['close'] < current_ema_9
        
        signal_type = None
        is_pre_signal = False
        rules_met_count = 0
        
        # Full signal
        if rule1_long and rule2_long and rule3_long:
            signal_type = "LONG"
            rules_met_count = 4
        elif rule1_short and rule2_short and rule3_short:
            signal_type = "SHORT"
            rules_met_count = 4
        # Pre-signal (3 of 4 rules)
        elif settings.get("pre_signal_enabled", True):
            rsi_zone = settings.get("pre_signal_rsi_zone", 4)
            
            if rule1_long and rule3_long and (current_rsi < 32 + rsi_zone):
                signal_type = "LONG"
                is_pre_signal = True
                rules_met_count = 3
            elif rule1_long and rule2_long and current_ha_candle['is_green'] and \
                 abs(current_ha_candle['close'] - current_ema_9) / current_ema_9 < 0.001:
                signal_type = "LONG"
                is_pre_signal = True
                rules_met_count = 3
            elif rule1_short and rule3_short and (current_rsi > 64 - rsi_zone):
                signal_type = "SHORT"
                is_pre_signal = True
                rules_met_count = 3
            elif rule1_short and rule2_short and not current_ha_candle['is_green'] and \
                 abs(current_ha_candle['close'] - current_ema_9) / current_ema_9 < 0.001:
                signal_type = "SHORT"
                is_pre_signal = True
                rules_met_count = 3
        
        if not signal_type:
            return None
        
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
        
        return {
            "type": signal_type,
            "signal_class": "PRE_SIGNAL" if is_pre_signal else "SIGNAL",
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
