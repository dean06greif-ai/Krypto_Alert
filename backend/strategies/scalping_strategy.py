"""
Smart-Money Scalping strategy on Heikin Ashi - fully configurable.

Upgraded entry logic (5-rule confluence for a HIGH win-rate):
  1. Trend       - price on the correct side of EMA-slow  (+ optional VWAP bias)
  2. RSI         - momentum extreme (pullback/exhaustion)
  3. Trigger     - Heikin-Ashi candle reclaims EMA-fast
  4. Volume      - above-average participation (real move, not noise)
  5. Smart Money - discount/premium location OR a liquidity sweep of a swing level

Exits are volatility-aware: stop-loss sits beyond market structure with an ATR
buffer (so normal wicks / stop-hunts don't take you out), TP1 at 1R for a partial
+ break-even, and a runner to the CRV target.
Provides live rule-state (long/short) for circle pre-fill + full signal.
"""
from typing import Dict, List, Optional
from strategies.base_strategy import BaseStrategy


class ScalpingStrategy(BaseStrategy):
    STRATEGY_ID = "scalping_4_rules"
    STRATEGY_NAME = "Smart Money Scalping"
    STRATEGY_DESCRIPTION = "EMA-Trend + RSI + EMA-Trigger + Volumen + Liquidity/VWAP (Smart Money) auf Heikin-Ashi"
    STRATEGY_TIMEFRAME = "1m"

    DEFAULT_PARAMS = {
        "ema_slow_period": {"value": 50, "min": 20, "max": 200, "step": 1,
                            "label": "EMA Slow", "description": "Trend-Filter EMA"},
        "ema_fast_period": {"value": 9, "min": 3, "max": 30, "step": 1,
                            "label": "EMA Fast", "description": "Trigger EMA"},
        "rsi_period": {"value": 14, "min": 5, "max": 30, "step": 1,
                       "label": "RSI Period", "description": "RSI Berechnungs-Periode"},
        "rsi_long_threshold": {"value": 35, "min": 10, "max": 50, "step": 1,
                               "label": "RSI LONG", "description": "RSI unter diesem Wert = LONG-Zone"},
        "rsi_short_threshold": {"value": 65, "min": 50, "max": 90, "step": 1,
                                "label": "RSI SHORT", "description": "RSI über diesem Wert = SHORT-Zone"},
        "pre_signal_rsi_zone": {"value": 4, "min": 0, "max": 10, "step": 1,
                                "label": "Pre-Signal Zone", "description": "RSI-Abstand für Frühwarnung"},
        "crv_target": {"value": 2.0, "min": 1.0, "max": 5.0, "step": 0.1,
                       "label": "CRV Ziel", "description": "Risk/Reward Ziel für den Runner"},
        "structure_lookback": {"value": 10, "min": 3, "max": 30, "step": 1,
                               "label": "Struktur Lookback", "description": "Kerzen für Support/Widerstand"},
        "atr_period": {"value": 14, "min": 5, "max": 30, "step": 1,
                       "label": "ATR Period", "description": "Volatilitäts-Periode für den Stop"},
        "atr_sl_multiplier": {"value": 1.2, "min": 0.2, "max": 4.0, "step": 0.1,
                              "label": "ATR SL Puffer", "description": "ATR-Puffer hinter der Struktur (Anti-Stop-Hunt)"},
        "volume_factor": {"value": 1.1, "min": 0.5, "max": 3.0, "step": 0.1,
                          "label": "Volumen Faktor", "description": "Volumen muss x-fach über dem Durchschnitt liegen"},
        "volume_lookback": {"value": 20, "min": 5, "max": 60, "step": 1,
                            "label": "Volumen Lookback", "description": "Kerzen für Volumen-Durchschnitt"},
        "use_vwap_filter": {"value": 1, "min": 0, "max": 1, "step": 1,
                            "label": "VWAP Filter", "description": "1 = nur LONG über / SHORT unter VWAP als Confluence"},
        "smc_lookback": {"value": 20, "min": 10, "max": 60, "step": 1,
                         "label": "Smart-Money Lookback", "description": "Range/Liquidity Fenster"},
    }

    def analyze(self, candles: List[Dict], symbol: str, params: Dict) -> Optional[Dict]:
        ema_slow_period = int(params["ema_slow_period"])
        ema_fast_period = int(params["ema_fast_period"])
        rsi_period = int(params["rsi_period"])
        rsi_long = params["rsi_long_threshold"]
        rsi_short = params["rsi_short_threshold"]
        pre_zone = params["pre_signal_rsi_zone"]
        crv_target = params["crv_target"]
        lookback = int(params.get("structure_lookback", 10))
        atr_period = int(params.get("atr_period", 14))
        atr_mult = float(params.get("atr_sl_multiplier", 1.2))
        vol_factor = float(params.get("volume_factor", 1.1))
        vol_lookback = int(params.get("volume_lookback", 20))
        use_vwap = bool(int(params.get("use_vwap_filter", 1)))
        smc_lb = int(params.get("smc_lookback", 20))

        min_candles = max(ema_slow_period + 10, 60)
        if len(candles) < min_candles:
            return None

        ha = self.indicators.calculate_heikin_ashi(candles)
        closes = [c["close"] for c in candles]
        ema_slow_arr = self.indicators.calculate_ema(closes, ema_slow_period)
        ema_fast_arr = self.indicators.calculate_ema(closes, ema_fast_period)
        rsi_arr = self.indicators.calculate_rsi(closes, rsi_period)
        atr_arr = self.indicators.calculate_atr(candles, atr_period)
        vwap_arr = self.indicators.calculate_vwap(candles)

        price = closes[-1]
        ema_slow = ema_slow_arr[-1]
        ema_fast = ema_fast_arr[-1]
        rsi = rsi_arr[-1]
        atr = atr_arr[-1]
        vwap = vwap_arr[-1]
        ha_last = ha[-1]
        if None in [ema_slow, ema_fast, rsi]:
            return None

        rel_vol = self.indicators.relative_volume(candles, vol_lookback) or 0.0
        range_pos = self.indicators.range_position(candles, smc_lb)
        sweep = self.indicators.liquidity_sweep(candles)

        # Rule 1 - Trend (+ VWAP bias as confluence)
        r1_long = price > ema_slow and (not use_vwap or price >= vwap)
        r1_short = price < ema_slow and (not use_vwap or price <= vwap)
        # Rule 2 - RSI momentum extreme
        r2_long = rsi < rsi_long
        r2_short = rsi > rsi_short
        # Rule 3 - EMA-fast trigger on Heikin-Ashi
        r3_long = ha_last["is_green"] and ha_last["close"] > ema_fast
        r3_short = (not ha_last["is_green"]) and ha_last["close"] < ema_fast
        # Rule 4 - Volume confirmation
        r4_long = rel_vol >= vol_factor
        r4_short = rel_vol >= vol_factor
        # Rule 5 - Smart Money: discount/premium location OR liquidity sweep
        r5_long = (range_pos <= 0.55) or (sweep == "bullish")
        r5_short = (range_pos >= 0.45) or (sweep == "bearish")

        rules = [
            {"id": "rule1_trend", "label": f"Trend EMA {ema_slow_period}",
             "description": f"Preis über/unter EMA {ema_slow_period}" + (" & VWAP" if use_vwap else ""),
             "long": r1_long, "short": r1_short},
            {"id": "rule2_rsi", "label": "RSI Momentum",
             "description": f"RSI < {rsi_long} (Long) / > {rsi_short} (Short)", "long": r2_long, "short": r2_short},
            {"id": "rule3_ema_fast_trigger", "label": f"EMA {ema_fast_period} Trigger",
             "description": f"HA-Kerze schließt über/unter EMA {ema_fast_period}", "long": r3_long, "short": r3_short},
            {"id": "rule4_volume", "label": "Volumen",
             "description": f"Volumen ≥ {vol_factor}x Durchschnitt (rel: {round(rel_vol,2)})",
             "long": r4_long, "short": r4_short},
            {"id": "rule5_smart_money", "label": "Smart Money / Liquidity",
             "description": "Discount/Premium-Zone oder Liquidity-Sweep", "long": r5_long, "short": r5_short},
        ]

        long_flags = [r1_long, r2_long, r3_long, r4_long, r5_long]
        short_flags = [r1_short, r2_short, r3_short, r4_short, r5_short]
        long_cnt = sum(long_flags)
        short_cnt = sum(short_flags)
        bias = "LONG" if long_cnt > short_cnt else ("SHORT" if short_cnt > long_cnt else None)

        signal_type = None
        is_pre = False
        if all(long_flags):
            signal_type = "LONG"
        elif all(short_flags):
            signal_type = "SHORT"
        else:
            # Pre-signal: structure+trigger+volume aligned, RSI approaching the zone
            if r1_long and r3_long and r4_long and rsi < rsi_long + pre_zone:
                signal_type, is_pre = "LONG", True
            elif r1_short and r3_short and r4_short and rsi > rsi_short - pre_zone:
                signal_type, is_pre = "SHORT", True

        levels = None
        if signal_type:
            levels = self._levels(candles, price, signal_type, atr, atr_mult, crv_target, lookback)

        return {
            "indicators": {"rsi": round(rsi, 2), "ema_fast": round(ema_fast, 6),
                           "ema_slow": round(ema_slow, 6), "price": round(price, 6),
                           "atr": round(atr, 6) if atr else 0,
                           "vwap": round(vwap, 6) if vwap else 0,
                           "rel_volume": round(rel_vol, 2),
                           "range_pos": round(range_pos, 2),
                           "liquidity_sweep": sweep or "none"},
            "rules": rules,
            "bias": bias,
            "long_count": long_cnt, "short_count": short_cnt, "rules_total": 5,
            "signal_type": signal_type,
            "is_pre_signal": is_pre,
            "levels": levels,
        }

    def _levels(self, candles, entry, side, atr, atr_mult, crv_target, lookback):
        # ATR buffer keeps the stop just beyond the noise / stop-hunt zone.
        buffer = (atr * atr_mult) if atr else (entry * 0.0004 * lookback)
        if side == "LONG":
            low = self.indicators.get_recent_low(candles, lookback)
            sl = low - buffer
            risk = entry - sl
            if risk <= 0:
                risk = buffer or entry * 0.003
                sl = entry - risk
            tp1 = entry + risk
            tpf = entry + risk * crv_target
        else:
            high = self.indicators.get_recent_high(candles, lookback)
            sl = high + buffer
            risk = sl - entry
            if risk <= 0:
                risk = buffer or entry * 0.003
                sl = entry + risk
            tp1 = entry - risk
            tpf = entry - risk * crv_target
        return {"entry": round(entry, 6), "stop_loss": round(sl, 6),
                "take_profit_1": round(tp1, 6), "take_profit_full": round(tpf, 6),
                "crv": round(self.indicators.calculate_crv(entry, sl, tpf), 2)}
