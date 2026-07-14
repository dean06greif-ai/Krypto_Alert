"""
Simple RSI reversal strategy with configurable parameters.
"""
from typing import Dict, List, Optional
from strategies.base_strategy import BaseStrategy


class RSIOnlyStrategy(BaseStrategy):
    STRATEGY_ID = "rsi_only"
    STRATEGY_NAME = "RSI Oversold/Overbought"
    STRATEGY_DESCRIPTION = "LONG bei RSI oversold, SHORT bei overbought - schnelle Reversals"
    STRATEGY_TIMEFRAME = "1m"

    DEFAULT_PARAMS = {
        "rsi_period": {"value": 14, "min": 5, "max": 30, "step": 1,
                       "label": "RSI Period", "description": "RSI Berechnungs-Periode"},
        "rsi_long_threshold": {"value": 30, "min": 10, "max": 40, "step": 1,
                               "label": "RSI LONG (Oversold)", "description": "RSI unter diesem Wert = LONG"},
        "rsi_short_threshold": {"value": 70, "min": 60, "max": 90, "step": 1,
                                "label": "RSI SHORT (Overbought)", "description": "RSI über diesem Wert = SHORT"},
        "sl_percent": {"value": 2.0, "min": 0.5, "max": 10.0, "step": 0.1,
                       "label": "Stop Loss %", "description": "SL Abstand vom Entry in %"},
        "tp_percent": {"value": 4.0, "min": 1.0, "max": 20.0, "step": 0.1,
                       "label": "Take Profit %", "description": "TP Abstand vom Entry in %"},
    }

    def analyze(self, candles: List[Dict], symbol: str, params: Dict) -> Optional[Dict]:
        rsi_period = int(params["rsi_period"])
        rsi_long = params["rsi_long_threshold"]
        rsi_short = params["rsi_short_threshold"]
        sl_pct = params["sl_percent"] / 100
        tp_pct = params["tp_percent"] / 100

        if len(candles) < rsi_period + 5:
            return None

        closes = [c["close"] for c in candles]
        rsi_arr = self.indicators.calculate_rsi(closes, rsi_period)
        price = closes[-1]
        rsi = rsi_arr[-1]
        if rsi is None:
            return None

        r_long = rsi < rsi_long
        r_short = rsi > rsi_short
        rules = [{"id": "rsi_extreme", "label": "RSI Extremwert",
                  "description": f"RSI < {rsi_long} (Long) / > {rsi_short} (Short)",
                  "long": r_long, "short": r_short}]
        bias = "LONG" if r_long else ("SHORT" if r_short else None)
        signal_type = "LONG" if r_long else ("SHORT" if r_short else None)

        levels = None
        if signal_type == "LONG":
            sl = price * (1 - sl_pct); tp1 = price * (1 + tp_pct / 2); tpf = price * (1 + tp_pct)
            levels = self._lv(price, sl, tp1, tpf)
        elif signal_type == "SHORT":
            sl = price * (1 + sl_pct); tp1 = price * (1 - tp_pct / 2); tpf = price * (1 - tp_pct)
            levels = self._lv(price, sl, tp1, tpf)

        return {
            "indicators": {"rsi": round(rsi, 2), "ema_fast": 0, "ema_slow": 0, "price": round(price, 6)},
            "rules": rules, "bias": bias,
            "long_count": int(r_long), "short_count": int(r_short), "rules_total": 1,
            "signal_type": signal_type, "is_pre_signal": False, "levels": levels,
        }

    def _lv(self, entry, sl, tp1, tpf):
        return {"entry": round(entry, 6), "stop_loss": round(sl, 6),
                "take_profit_1": round(tp1, 6), "take_profit_full": round(tpf, 6),
                "crv": round(self.indicators.calculate_crv(entry, sl, tpf), 2)}
