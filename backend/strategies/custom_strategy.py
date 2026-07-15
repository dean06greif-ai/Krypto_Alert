"""
Custom, user-defined strategy engine.
A definition (stored in MongoDB) describes indicators + long/short rules.
Rules: {"indicator", "op", "value", "label"} where
  indicator in: rsi, ema_fast, ema_slow, price, ha_color (1=green/0=red), ema_gap_pct
  op in: <, >, <=, >=, cross_above, cross_below
  value: number OR indicator-name string
"""
from typing import Dict, List, Optional
from strategies.base_strategy import BaseStrategy

INDICATORS = ["rsi", "ema_fast", "ema_slow", "price", "ha_color", "ema_gap_pct"]
OPERATORS = ["<", ">", "<=", ">=", "cross_above", "cross_below"]


class CustomStrategy(BaseStrategy):
    IS_CUSTOM = True
    STRATEGY_TIMEFRAME = "1m"
    DEFAULT_PARAMS = {}

    def __init__(self, definition: Dict):
        super().__init__()
        self.definition = definition
        self.STRATEGY_ID = definition["id"]
        self.STRATEGY_NAME = definition.get("name", "Custom")
        self.STRATEGY_DESCRIPTION = definition.get("description", "Custom Strategie")

    def _series(self, candles):
        d = self.definition.get("indicators", {})
        efp = int(d.get("ema_fast_period", 9))
        esp = int(d.get("ema_slow_period", 50))
        rp = int(d.get("rsi_period", 14))
        closes = [c["close"] for c in candles]
        ema_f = self.indicators.calculate_ema(closes, efp)
        ema_s = self.indicators.calculate_ema(closes, esp)
        rsi = self.indicators.calculate_rsi(closes, rp)
        ha = self.indicators.calculate_heikin_ashi(candles)

        def snap(i):
            ef, es = ema_f[i], ema_s[i]
            gap = ((ef - es) / es * 100) if (ef and es) else None
            return {"rsi": rsi[i], "ema_fast": ef, "ema_slow": es,
                    "price": closes[i], "ha_color": 1 if ha[i]["is_green"] else 0,
                    "ema_gap_pct": gap}
        return snap(-1), snap(-2), esp, efp, rp

    def _resolve(self, snap, value):
        if isinstance(value, str) and value in INDICATORS:
            return snap.get(value)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    def _eval_rule(self, rule, cur, prev):
        ind = rule.get("indicator")
        op = rule.get("op")
        left = cur.get(ind)
        left_prev = prev.get(ind)
        right = self._resolve(cur, rule.get("value"))
        right_prev = self._resolve(prev, rule.get("value"))
        if left is None or right is None:
            return False
        if op == "<":
            return left < right
        if op == ">":
            return left > right
        if op == "<=":
            return left <= right
        if op == ">=":
            return left >= right
        if op == "cross_above":
            return left_prev is not None and right_prev is not None and left_prev <= right_prev and left > right
        if op == "cross_below":
            return left_prev is not None and right_prev is not None and left_prev >= right_prev and left < right
        return False

    def analyze(self, candles: List[Dict], symbol: str, params: Dict) -> Optional[Dict]:
        d = self.definition
        need = max(int(d.get("indicators", {}).get("ema_slow_period", 50)) + 10, 60)
        if len(candles) < need:
            return None
        cur, prev, esp, efp, rp = self._series(candles)
        if cur["rsi"] is None or cur["ema_slow"] is None:
            return None

        long_rules = d.get("long_rules", [])
        short_rules = d.get("short_rules", [])
        rules = []
        long_evals, short_evals = [], []
        for i, r in enumerate(long_rules):
            ev = self._eval_rule(r, cur, prev)
            long_evals.append(ev)
            rules.append({"id": f"L{i}", "label": r.get("label") or self._auto_label(r),
                          "description": "LONG Bedingung", "long": ev, "short": False})
        for i, r in enumerate(short_rules):
            ev = self._eval_rule(r, cur, prev)
            short_evals.append(ev)
            rules.append({"id": f"S{i}", "label": r.get("label") or self._auto_label(r),
                          "description": "SHORT Bedingung", "long": False, "short": ev})

        signal_type = None
        if long_evals and all(long_evals):
            signal_type = "LONG"
        elif short_evals and all(short_evals):
            signal_type = "SHORT"

        long_cnt = sum(long_evals)
        short_cnt = sum(short_evals)
        bias = "LONG" if long_cnt > short_cnt else ("SHORT" if short_cnt > long_cnt else None)

        levels = self._levels(candles, cur["price"], signal_type) if signal_type else None

        return {
            "indicators": {"rsi": round(cur["rsi"], 2),
                           "ema_fast": round(cur["ema_fast"], 6) if cur["ema_fast"] else 0,
                           "ema_slow": round(cur["ema_slow"], 6) if cur["ema_slow"] else 0,
                           "price": round(cur["price"], 6)},
            "rules": rules, "bias": bias,
            "long_count": long_cnt, "short_count": short_cnt,
            "rules_total": len(long_rules) if signal_type == "LONG" else (len(short_rules) or len(long_rules)),
            "signal_type": signal_type, "is_pre_signal": False, "levels": levels,
        }

    def _auto_label(self, r):
        return f"{r.get('indicator')} {r.get('op')} {r.get('value')}"

    def _levels(self, candles, entry, side):
        d = self.definition
        crv = float(d.get("crv_target", 2.0))
        if d.get("sl_mode", "percent") == "structure":
            lookback = int(d.get("structure_lookback", 10))
            ticks = int(d.get("sl_ticks", 4))
            tick = entry * 0.0001
            if side == "LONG":
                sl = self.indicators.get_recent_low(candles, lookback) - ticks * tick
            else:
                sl = self.indicators.get_recent_high(candles, lookback) + ticks * tick
        else:
            pct = float(d.get("sl_percent", 2.0)) / 100
            sl = entry * (1 - pct) if side == "LONG" else entry * (1 + pct)
        risk = abs(entry - sl)
        if side == "LONG":
            tp1, tpf = entry + risk, entry + risk * crv
        else:
            tp1, tpf = entry - risk, entry - risk * crv
        return {"entry": round(entry, 6), "stop_loss": round(sl, 6),
                "take_profit_1": round(tp1, 6), "take_profit_full": round(tpf, 6),
                "crv": round(self.indicators.calculate_crv(entry, sl, tpf), 2)}
