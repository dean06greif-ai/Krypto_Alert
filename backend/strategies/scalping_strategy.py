"""
Scalping strategy with 4 rules - fully configurable.
"""
from typing import Dict, List, Optional
from strategies.base_strategy import BaseStrategy


class ScalpingStrategy(BaseStrategy):
    STRATEGY_ID = "scalping_4_rules"
    STRATEGY_NAME = "Scalping 4-Regeln (Heikin Ashi)"
    STRATEGY_DESCRIPTION = "EMA 50 + RSI + EMA 9 Trigger auf Heikin Ashi Candles"
    STRATEGY_TIMEFRAME = "1m"
    
    DEFAULT_PARAMS = {
        "ema_slow_period": {
            "value": 50, "min": 20, "max": 200, "step": 1,
            "label": "EMA Slow Period",
            "description": "Trend-Filter EMA (Standard: 50)"
        },
        "ema_fast_period": {
            "value": 9, "min": 3, "max": 30, "step": 1,
            "label": "EMA Fast Period",
            "description": "Trigger EMA (Standard: 9)"
        },
        "rsi_period": {
            "value": 14, "min": 5, "max": 30, "step": 1,
            "label": "RSI Period",
            "description": "RSI Berechnungs-Periode (Standard: 14)"
        },
        "rsi_long_threshold": {
            "value": 32, "min": 10, "max": 50, "step": 1,
            "label": "RSI LONG Threshold",
            "description": "RSI unter diesem Wert = LONG (Standard: 32)"
        },
        "rsi_short_threshold": {
            "value": 64, "min": 50, "max": 90, "step": 1,
            "label": "RSI SHORT Threshold",
            "description": "RSI über diesem Wert = SHORT (Standard: 64)"
        },
        "pre_signal_rsi_zone": {
            "value": 4, "min": 0, "max": 10, "step": 1,
            "label": "Pre-Signal RSI Zone",
            "description": "Wie weit vom Threshold entfernt für Frühwarnung (Standard: 4)"
        },
        "crv_target": {
            "value": 2.0, "min": 1.0, "max": 5.0, "step": 0.1,
            "label": "CRV Target",
            "description": "Risk/Reward Ratio Ziel (Standard: 2.0)"
        },
        "sl_tick_multiplier": {
            "value": 4, "min": 1, "max": 20, "step": 1,
            "label": "SL Tick Multiplier",
            "description": "Wie viele Ticks unter/über recent low/high (Standard: 4)"
        }
    }
    
    def check_signal(self, candles: List[Dict], symbol: str, settings: Dict) -> Optional[Dict]:
        params = self.get_params(settings)
        
        ema_slow_period = int(params["ema_slow_period"])
        ema_fast_period = int(params["ema_fast_period"])
        rsi_period = int(params["rsi_period"])
        rsi_long_threshold = params["rsi_long_threshold"]
        rsi_short_threshold = params["rsi_short_threshold"]
        pre_signal_zone = params["pre_signal_rsi_zone"]
        crv_target = params["crv_target"]
        sl_tick_multiplier = int(params["sl_tick_multiplier"])
        
        min_candles = max(ema_slow_period + 10, 60)
        if len(candles) < min_candles:
            return None
        
        ha_candles = self.indicators.calculate_heikin_ashi(candles)
        close_prices = [c['close'] for c in candles]
        
        ema_slow = self.indicators.calculate_ema(close_prices, ema_slow_period)
        ema_fast = self.indicators.calculate_ema(close_prices, ema_fast_period)
        rsi = self.indicators.calculate_rsi(close_prices, rsi_period)
        
        current_price = close_prices[-1]
        current_ema_slow = ema_slow[-1] if ema_slow[-1] is not None else None
        current_ema_fast = ema_fast[-1] if ema_fast[-1] is not None else None
        current_rsi = rsi[-1] if rsi[-1] is not None else None
        current_ha_candle = ha_candles[-1]
        
        if None in [current_ema_slow, current_ema_fast, current_rsi]:
            return None
        
        rule1_long = current_price > current_ema_slow
        rule2_long = current_rsi < rsi_long_threshold
        rule3_long = current_ha_candle['is_green'] and current_ha_candle['close'] > current_ema_fast
        
        rule1_short = current_price < current_ema_slow
        rule2_short = current_rsi > rsi_short_threshold
        rule3_short = not current_ha_candle['is_green'] and current_ha_candle['close'] < current_ema_fast
        
        signal_type = None
        is_pre_signal = False
        rules_met_count = 0
        
        if rule1_long and rule2_long and rule3_long:
            signal_type = "LONG"
            rules_met_count = 4
        elif rule1_short and rule2_short and rule3_short:
            signal_type = "SHORT"
            rules_met_count = 4
        elif settings.get("pre_signal_enabled", True):
            if rule1_long and rule3_long and (current_rsi < rsi_long_threshold + pre_signal_zone):
                signal_type = "LONG"
                is_pre_signal = True
                rules_met_count = 3
            elif rule1_long and rule2_long and current_ha_candle['is_green'] and \
                 abs(current_ha_candle['close'] - current_ema_fast) / current_ema_fast < 0.001:
                signal_type = "LONG"
                is_pre_signal = True
                rules_met_count = 3
            elif rule1_short and rule3_short and (current_rsi > rsi_short_threshold - pre_signal_zone):
                signal_type = "SHORT"
                is_pre_signal = True
                rules_met_count = 3
            elif rule1_short and rule2_short and not current_ha_candle['is_green'] and \
                 abs(current_ha_candle['close'] - current_ema_fast) / current_ema_fast < 0.001:
                signal_type = "SHORT"
                is_pre_signal = True
                rules_met_count = 3
        
        if not signal_type:
            return None
        
        entry_price = current_price
        
        if signal_type == "LONG":
            recent_low = self.indicators.get_recent_low(candles, 10)
            tick_size = entry_price * 0.0001
            stop_loss = recent_low - (sl_tick_multiplier * tick_size)
            risk = entry_price - stop_loss
            take_profit_full = entry_price + (risk * crv_target)
            take_profit_1 = entry_price + risk
        else:
            recent_high = self.indicators.get_recent_high(candles, 10)
            tick_size = entry_price * 0.0001
            stop_loss = recent_high + (sl_tick_multiplier * tick_size)
            risk = stop_loss - entry_price
            take_profit_full = entry_price - (risk * crv_target)
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
            "ema_fast": round(current_ema_fast, 6),
            "ema_slow": round(current_ema_slow, 6),
            "rules_met_count": rules_met_count,
            "rules_met": {
                "rule1_ema_slow": rule1_long if signal_type == "LONG" else rule1_short,
                "rule2_rsi": rule2_long if signal_type == "LONG" else rule2_short,
                "rule3_ema_fast_trigger": rule3_long if signal_type == "LONG" else rule3_short,
                "rule4_time_window": True
            },
            "used_params": params
        }
