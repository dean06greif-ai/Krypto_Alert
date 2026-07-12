"""
Simple RSI-based strategy (Example second strategy).
- LONG: RSI < 30 (Oversold)
- SHORT: RSI > 70 (Overbought)
- SL: 2% Distance
- TP: 4% Distance (CRV 2)
"""
from typing import Dict, List, Optional
from strategies.base_strategy import BaseStrategy


class RSIOnlyStrategy(BaseStrategy):
    STRATEGY_ID = "rsi_only"
    STRATEGY_NAME = "RSI Oversold/Overbought"
    STRATEGY_DESCRIPTION = "Einfache RSI-Strategie. LONG bei RSI < 30, SHORT bei RSI > 70. Für schnelle Reversal-Trades."
    STRATEGY_TIMEFRAME = "1m"
    
    def check_signal(self, candles: List[Dict], symbol: str, settings: Dict) -> Optional[Dict]:
        if len(candles) < 30:
            return None
        
        close_prices = [c['close'] for c in candles]
        rsi = self.indicators.calculate_rsi(close_prices, 14)
        
        current_price = close_prices[-1]
        current_rsi = rsi[-1] if rsi[-1] is not None else None
        
        if current_rsi is None:
            return None
        
        signal_type = None
        rules_met_count = 0
        
        if current_rsi < 30:
            signal_type = "LONG"
            rules_met_count = 1
        elif current_rsi > 70:
            signal_type = "SHORT"
            rules_met_count = 1
        
        if not signal_type:
            return None
        
        entry_price = current_price
        
        if signal_type == "LONG":
            stop_loss = entry_price * 0.98
            take_profit_1 = entry_price * 1.02
            take_profit_full = entry_price * 1.04
        else:
            stop_loss = entry_price * 1.02
            take_profit_1 = entry_price * 0.98
            take_profit_full = entry_price * 0.96
        
        crv = self.indicators.calculate_crv(entry_price, stop_loss, take_profit_full)
        
        return {
            "type": signal_type,
            "signal_class": "SIGNAL",
            "entry_price": round(entry_price, 6),
            "stop_loss": round(stop_loss, 6),
            "take_profit_1": round(take_profit_1, 6),
            "take_profit_full": round(take_profit_full, 6),
            "crv": round(crv, 2),
            "rsi": round(current_rsi, 2),
            "ema_9": 0,
            "ema_50": 0,
            "rules_met_count": rules_met_count,
            "rules_met": {
                "rsi_extreme": True
            }
        }
