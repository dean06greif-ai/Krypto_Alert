"""
Scalping strategy with 3 rules on Heikin Ashi - fully configurable.
Provides live rule-state (long/short) for circle pre-fill + full signal.
"""
from typing import Dict, List, Optional
from strategies.base_strategy import BaseStrategy


class ScalpingStrategy(BaseStrategy):
    STRATEGY_ID = "scalping_4_rules"
    STRATEGY_NAME = "Scalping (Heikin Ashi)"
    STRATEGY_DESCRIPTION = "EMA 50 Trend + RSI + EMA 9 Trigger auf Heikin-Ashi Kerzen"
    STRATEGY_TIMEFRAME = "1m"

    DEFAULT_PARAMS = {
        "ema_slow_period": {"value": 50, "min": 20, "max": 200, "step": 1,
                            "label": "EMA Slow", "description": "Trend-Filter EMA"},
        "ema_fast_period": {"value": 9, "min": 3, "max": 30, "step": 1,
                            "label": "EMA Fast", "description": "Trigger EMA"},
        "rsi_period": {"value": 14, "min": 5, "max": 30, "step": 1,
                       "label": "RSI Period", "description": "RSI Berechnungs-Periode"},
        "rsi_long_threshold": {"value": 32, "min": 10, "max": 50, "step": 1,
                               "label": "RSI LONG", "description": "RSI unter diesem Wert = LONG"},
        "rsi_short_threshold": {"value": 64, "min": 50, "max": 90, "step": 1,
                                "label": "RSI SHORT", "description": "RSI über diesem Wert = SHORT"},
        "pre_signal_rsi_zone": {"value": 4, "min": 0, "max": 10, "step": 1,
                                "label": "Pre-Signal Zone", "description": "RSI-Abstand für Frühwarnung"},
        "crv_target": {"value": 2.0, "min": 1.0, "max": 5.0, "step": 0.1,
                       "label": "CRV Ziel", "description": "Risk/Reward Ziel"},
        "sl_tick_multiplier": {"value": 4, "min": 1, "max": 20, "step": 1,
                               "label": "SL Ticks", "description": "Ticks unter/über recent low/high"},
        "structure_lookback": {"value": 10, "min": 3, "max": 30, "step": 1,
                               "label": "Struktur Lookback", "description": "Kerzen für Support/Widerstand"},
    }

    def analyze(self, candles: List[Dict], symbol: str, params: Dict) -> Optional[Dict]:
        ema_slow_period = int(params["ema_slow_period"])
        ema_fast_period = int(params["ema_fast_period"])
        rsi_period = int(params["rsi_period"])
        rsi_long = params["rsi_long_threshold"]
        rsi_short = params["rsi_short_threshold"]
        pre_zone = params["pre_signal_rsi_zone"]
        crv_target = params["crv_target"]
        sl_ticks = int(params["sl_tick_multiplier"])
        lookback = int(params.get("structure_lookback", 10))

        min_candles = max(ema_slow_period + 10, 60)
        if len(candles) < min_candles:
            return None

        ha = self.indicators.calculate_heikin_ashi(candles)
        closes = [c["close"] for c in candles]
        ema_slow_arr = self.indicators.calculate_ema(closes, ema_slow_period)
        ema_fast_arr = self.indicators.calculate_ema(closes, ema_fast_period)
        rsi_arr = self.indicators.calculate_rsi(closes, rsi_period)

        price = closes[-1]
        ema_slow = ema_slow_arr[-1]
        ema_fast = ema_fast_arr[-1]
        rsi = rsi_arr[-1]
        ha_last = ha[-1]
        if None in [ema_slow, ema_fast, rsi]:
            return None

        r1_long = price > ema_slow
        r2_long = rsi < rsi_long
        r3_long = ha_last["is_green"] and ha_last["close"] > ema_fast
        r1_short = price < ema_slow
        r2_short = rsi > rsi_short
        r3_short = (not ha_last["is_green"]) and ha_last["close"] < ema_fast

        rules = [
            {"id": "rule1_ema_slow", "label": f"EMA {ema_slow_period} Trend",
             "description": f"Preis über/unter EMA {ema_slow_period}", "long": r1_long, "short": r1_short},
            {"id": "rule2_rsi", "label": "RSI Level",
             "description": f"RSI < {rsi_long} (Long) / > {rsi_short} (Short)", "long": r2_long, "short": r2_short},
            {"id": "rule3_ema_fast_trigger", "label": f"EMA {ema_fast_period} Trigger",
             "description": f"HA-Kerze schließt über/unter EMA {ema_fast_period}", "long": r3_long, "short": r3_short},
        ]

        long_cnt = sum([r1_long, r2_long, r3_long])
        short_cnt = sum([r1_short, r2_short, r3_short])
        bias = "LONG" if long_cnt > short_cnt else ("SHORT" if short_cnt > long_cnt else None)

        signal_type = None
        is_pre = False
        if r1_long and r2_long and r3_long:
            signal_type = "LONG"
        elif r1_short and r2_short and r3_short:
            signal_type = "SHORT"
        else:
            # pre-signal: 2 of 3 aligned with RSI near threshold
            if r1_long and r3_long and rsi < rsi_long + pre_zone:
                signal_type, is_pre = "LONG", True
            elif r1_short and r3_short and rsi > rsi_short - pre_zone:
                signal_type, is_pre = "SHORT", True

        levels = None
        if signal_type:
            levels = self._levels(candles, price, signal_type, sl_ticks, crv_target, lookback)

        return {
            "indicators": {"rsi": round(rsi, 2), "ema_fast": round(ema_fast, 6),
                           "ema_slow": round(ema_slow, 6), "price": round(price, 6)},
            "rules": rules,
            "bias": bias,
            "long_count": long_cnt, "short_count": short_cnt, "rules_total": 3,
            "signal_type": signal_type,
            "is_pre_signal": is_pre,
            "levels": levels,
        }

    def _levels(self, candles, entry, side, sl_ticks, crv_target, lookback):
        tick = entry * 0.0001
        if side == "LONG":
            low = self.indicators.get_recent_low(candles, lookback)
            sl = low - sl_ticks * tick
            risk = entry - sl
            tp1 = entry + risk
            tpf = entry + risk * crv_target
        else:
            high = self.indicators.get_recent_high(candles, lookback)
            sl = high + sl_ticks * tick
            risk = sl - entry
            tp1 = entry - risk
            tpf = entry - risk * crv_target
        return {"entry": round(entry, 6), "stop_loss": round(sl, 6),
                "take_profit_1": round(tp1, 6), "take_profit_full": round(tpf, 6),
                "crv": round(self.indicators.calculate_crv(entry, sl, tpf), 2)}
