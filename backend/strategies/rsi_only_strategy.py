"""
Simple RSI-based strategy with configurable parameters.
"""
from typing import Dict, List, Optional
from strategies.base_strategy import BaseStrategy


class RSIOnlyStrategy(BaseStrategy):
    STRATEGY_ID = "rsi_only"
    STRATEGY_NAME = "RSI Oversold/Overbought"
    STRATEGY_DESCRIPTION = "LONG bei RSI oversold, SHORT bei RSI overbought - für schnelle Reversal-Trades"
    STRATEGY_TIMEFRAME = "1m"
    
    DEFAULT_PARAMS = {
        "rsi_period": {
            "value": 14, "min": 5, "max": 30, "step": 1,
            "label": "RSI Period",
            "description": "RSI Berechnungs-Periode (Standard: 14)"
        },
        "rsi_long_threshold": {
            "value": 30, "min": 10, "max": 40, "step": 1,
            "label": "RSI LONG Threshold (Oversold)",
            "description": "RSI unter diesem Wert = LONG (Standard: 30)"
        },
        "rsi_short_threshold": {
            "value": 70, "min": 60, "max": 90, "step": 1,
            "label": "RSI SHORT Threshold (Overbought)",
            "description": "RSI über diesem Wert = SHORT (Standard: 70)"
        },
        "sl_percent": {
            "value": 2.0, "min": 0.5, "max": 10.0, "step": 0.1,
            "label": "Stop Loss %",
            "description": "SL Distance vom Entry in % (Standard: 2%)"
        },
        "tp_percent": {
            "value": 4.0, "min": 1.0, "max": 20.0, "step": 0.1,
            "label": "Take Profit %",
            "description": "TP Distance vom Entry in % (Standard: 4%)"
        }
    }
    
    def check_signal(self, candles: List[Dict], symbol: str, settings: Dict) -> Optional[Dict]:
        params = self.get_params(settings)
        
        rsi_period = int(params["rsi_period"])
        rsi_long_threshold = params["rsi_long_threshold"]
        rsi_short_threshold = params["rsi_short_threshold"]
        sl_percent = params["sl_percent"] / 100
        tp_percent = params["tp_percent"] / 100
        
        if len(candles) < rsi_period + 5:
            return None
        
        close_prices = [c['close'] for c in candles]
        rsi = self.indicators.calculate_rsi(close_prices, rsi_period)
        
        current_price = close_prices[-1]
        current_rsi = rsi[-1] if rsi[-1] is not None else None
        
        if current_rsi is None:
            return None
        
        signal_type = None
        
        if current_rsi < rsi_long_threshold:
            signal_type = "LONG"
        elif current_rsi > rsi_short_threshold:
            signal_type = "SHORT"
        
        if not signal_type:
            return None
        
        entry_price = current_price
        
        if signal_type == "LONG":
            stop_loss = entry_price * (1 - sl_percent)
            take_profit_1 = entry_price * (1 + tp_percent / 2)
            take_profit_full = entry_price * (1 + tp_percent)
        else:
            stop_loss = entry_price * (1 + sl_percent)
            take_profit_1 = entry_price * (1 - tp_percent / 2)
            take_profit_full = entry_price * (1 - tp_percent)
        
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
            "ema_fast": 0,
            "ema_slow": 0,
            "rules_met_count": 1,
            "rules_met": {
                "rsi_extreme": True
            },
            "used_params": params
        }
