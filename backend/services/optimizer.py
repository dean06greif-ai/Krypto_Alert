"""
Strategie-Optimizer:
- mode "params":    Random-Search ODER Bayes'sche Optimierung (TPE) über
                    Strategie- und Trade-Parameter
- mode "discovery": Greedy Indikator-Discovery (Regel für Regel hinzufügen,
                    nur behalten wenn Score sich verbessert) – optional
                    ausgehend von einer bestehenden Custom-Strategie
- mode "combo":     Discovery + anschließendes Feintuning der Schwellenwerte
"""
import asyncio
import copy
import gc
import logging
import math
import random
import uuid
from datetime import datetime, timezone
from typing import Dict, List

import aiohttp

from services.backtester import JobCancelled, fetch_history, simulate_pair
from services.timeframes import TIMEFRAMES, aggregate_candles
from services import fast_sim
from strategies.custom_strategy import CustomStrategy

logger = logging.getLogger(__name__)

JOBS: Dict[str, Dict] = {}

TRADE_SPACES = {
    "tpsl": {
        "tp1_crv": [0.5, 0.75, 1.0, 1.5, 2.0, 2.5, 3.0],
        "tp_full_crv": [1.0, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0],
        "sl_lookback": [5, 8, 10, 14, 20, 30],
        "tp1_close_percent": [30, 40, 50, 60, 70],
        "sl_mode": ["structure", "atr", "fixed"],
        "sl_fixed_percent": [0.5, 0.8, 1.0, 1.5, 2.0],
        "atr_sl_multiplier": [0.8, 1.0, 1.2, 1.5, 2.0],
    },
    "breakeven": {
        "be_mode": ["off", "tp1", "crv", "profit_pct"],
        "be_trigger_crv": [0.5, 1.0, 1.5, 2.0],
        "be_trigger_profit_pct": [15, 30, 50, 80],
    },
    "profit_secure": {
        "profit_secure_enabled": [False, True],
        "profit_secure_trigger_pct": [20, 30, 50, 80],
        "profit_lock_pct": [30, 50, 70],
    },
    "leverage": {
        "leverage": [3, 5, 10, 15, 20, 28, 40, 50],
    },
    "auto_leverage": {
        "auto_leverage_enabled": [False, True],
        "auto_lev_mode": ["liq_pct", "liq_ticks"],
        "auto_lev_value": [0.1, 0.25, 0.5, 1.0, 3, 5, 10],
        "auto_lev_max": [25, 50, 75, 100],
    },
    "sessions": {
        "sessions": ["", "07:00-22:00", "09:00-12:00", "15:30-18:30",
                     "09:00-12:00,15:30-18:30", "13:00-22:00", "22:00-06:00"],
    },
}

# Back-Compat: alter Gesamtraum
TRADE_PARAM_SPACE = TRADE_SPACES["tpsl"]


def build_trade_space(flags: Dict) -> Dict:
    space = {}
    for group, on in (flags or {}).items():
        if on and group in TRADE_SPACES:
            space.update(TRADE_SPACES[group])
    return space


def create_job(params: Dict) -> str:
    job_id = uuid.uuid4().hex[:12]
    JOBS[job_id] = {"id": job_id, "status": "running", "progress": 0,
                    "phase": "Startet", "params": params, "best": None, "cancel": False,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "result": None, "error": None}
    if len(JOBS) > 10:
        for k in list(JOBS.keys())[:-10]:
            JOBS.pop(k, None)
    return job_id


def _score(m: Dict, objective: str, min_trades: int) -> float:
    trades = m.get("trades", 0)
    if trades < min_trades:
        return -1e9 + trades
    wr = m.get("win_rate", 0.0)
    pnl = m.get("pnl", 0.0)
    if objective == "win_rate":
        return wr * 1000 + pnl
    if objective == "pnl":
        return pnl
    return pnl * (0.5 + wr / 100.0)


def _evaluate(strategy, histories: Dict[str, List[Dict]], settings: Dict, cfg: Dict,
              fs_map: Dict = None, should_stop=None) -> Dict:
    agg = {"trades": 0, "wins": 0, "losses": 0, "breakevens": 0,
           "pnl": 0.0, "fees": 0.0, "max_drawdown": 0.0}
    for sym, candles in histories.items():
        provider = None
        if fs_map is not None:
            if getattr(strategy, "IS_CUSTOM", False):
                try:
                    provider = fast_sim.build_signal_provider(strategy.definition, fs_map[sym])
                except Exception:
                    provider = None
            else:
                try:
                    provider = fast_sim.build_builtin_signal_provider(
                        strategy, fs_map[sym], settings, sym)
                except Exception:
                    provider = None
        r = simulate_pair(strategy, candles, sym, settings, cfg,
                          should_stop=should_stop, signal_provider=provider)
        for k in agg:
            agg[k] += r.get(k, 0) or 0
    decided = agg["wins"] + agg["losses"]
    agg["win_rate"] = round(agg["wins"] / decided * 100, 1) if decided else 0.0
    agg["pnl"] = round(agg["pnl"], 2)
    agg["fees"] = round(agg["fees"], 2)
    agg["max_drawdown"] = round(agg["max_drawdown"], 2)
    agg["avg_pnl"] = round(agg["pnl"] / agg["trades"], 3) if agg["trades"] else 0.0
    _cap = float(cfg.get("max_capital", 100) or 100)
    agg["pnl_pct"] = round(agg["pnl"] / _cap * 100, 1)
    agg["max_drawdown_pct"] = round(agg["max_drawdown"] / _cap * 100, 1)
    return agg


# ---------------- Kandidaten-Regeln für Discovery ----------------
def build_candidates(allowed: List[str] = None) -> List[Dict]:
    def ok(ind):
        return not allowed or ind in allowed

    C = []

    def add(ind, label, long_rule, short_rule):
        if ok(ind):
            C.append({"ind": ind, "label": label, "long": long_rule, "short": short_rule})

    for t in (25, 30, 35, 40):
        add("rsi", f"RSI < {t} / > {100 - t}",
            {"indicator": "rsi", "op": "<", "value": t},
            {"indicator": "rsi", "op": ">", "value": 100 - t})
    add("ema_slow", "Trend: Preis vs. EMA slow",
        {"indicator": "price", "op": ">", "value": "ema_slow"},
        {"indicator": "price", "op": "<", "value": "ema_slow"})
    add("ema_fast", "Trend: Preis vs. EMA fast",
        {"indicator": "price", "op": ">", "value": "ema_fast"},
        {"indicator": "price", "op": "<", "value": "ema_fast"})
    add("ema_fast", "EMA fast vs. EMA slow",
        {"indicator": "ema_fast", "op": ">", "value": "ema_slow"},
        {"indicator": "ema_fast", "op": "<", "value": "ema_slow"})
    add("macd_hist", "MACD Momentum (Histogramm)",
        {"indicator": "macd_hist", "op": ">", "value": 0},
        {"indicator": "macd_hist", "op": "<", "value": 0})
    add("macd", "MACD Cross",
        {"indicator": "macd", "op": "cross_above", "value": "macd_signal"},
        {"indicator": "macd", "op": "cross_below", "value": "macd_signal"})
    add("bb_lower", "Bollinger Reversion",
        {"indicator": "price", "op": "<", "value": "bb_lower"},
        {"indicator": "price", "op": ">", "value": "bb_upper"})
    add("bb_upper", "Bollinger Breakout (Cross)",
        {"indicator": "price", "op": "cross_above", "value": "bb_upper"},
        {"indicator": "price", "op": "cross_below", "value": "bb_lower"})
    for t in (20, 30):
        add("stoch_k", f"Stochastik < {t} / > {100 - t}",
            {"indicator": "stoch_k", "op": "<", "value": t},
            {"indicator": "stoch_k", "op": ">", "value": 100 - t})
    add("stoch_k", "Stochastik Cross",
        {"indicator": "stoch_k", "op": "cross_above", "value": "stoch_d"},
        {"indicator": "stoch_k", "op": "cross_below", "value": "stoch_d"})
    add("vwap", "VWAP Trend",
        {"indicator": "price", "op": ">", "value": "vwap"},
        {"indicator": "price", "op": "<", "value": "vwap"})
    add("vwap", "VWAP Reversion",
        {"indicator": "price", "op": "<", "value": "vwap"},
        {"indicator": "price", "op": ">", "value": "vwap"})
    for t in (1.2, 1.5, 2.0):
        add("rel_volume", f"Rel. Volumen > {t}",
            {"indicator": "rel_volume", "op": ">", "value": t},
            {"indicator": "rel_volume", "op": ">", "value": t})
    add("ha_color", "Heikin-Ashi Farbe",
        {"indicator": "ha_color", "op": ">=", "value": 1},
        {"indicator": "ha_color", "op": "<=", "value": 0})
    for t in (0.2, 0.5):
        add("price_change_pct", f"Momentum > {t}%",
            {"indicator": "price_change_pct", "op": ">", "value": t},
            {"indicator": "price_change_pct", "op": "<", "value": -t})
    for t in (0.3, 0.8):
        add("bb_width_pct", f"BB Breite > {t}%",
            {"indicator": "bb_width_pct", "op": ">", "value": t},
            {"indicator": "bb_width_pct", "op": ">", "value": t})
    for t in (0.05, 0.15):
        add("atr_pct", f"ATR > {t}% vom Preis",
            {"indicator": "atr_pct", "op": ">", "value": t},
            {"indicator": "atr_pct", "op": ">", "value": t})
    return C


def _mk_strategy(definition: Dict) -> CustomStrategy:
    return CustomStrategy({**definition, "id": definition.get("id") or "opt_eval"})


def _labels(definition: Dict) -> Dict:
    s = _mk_strategy(definition)
    return {"long": [s._auto_label(r) for r in definition.get("long_rules", [])],
            "short": [s._auto_label(r) for r in definition.get("short_rules", [])]}


# ---------------- Bayes (TPE-lite) ----------------
def _tpe_suggest(space: Dict[str, List], history: List[Dict], rng: random.Random,
                 n_candidates: int = 24, gamma: float = 0.25) -> Dict:
    """Tree-structured Parzen Estimator über diskrete Parameter-Räume.
    history: [{"flat": {k: v}, "score": float}]"""
    if len(history) < 8:
        return {k: rng.choice(v) for k, v in space.items()}
    ranked = sorted(history, key=lambda x: -x["score"])
    n_good = max(2, int(len(ranked) * gamma))
    good, bad = ranked[:n_good], ranked[n_good:]

    def dens(obs, value, values):
        cnt = sum(1 for o in obs if o["flat"].get(k) == value)
        return (cnt + 1.0) / (len(obs) + len(values))

    best_cand, best_ratio = None, -1e18
    for _ in range(n_candidates):
        cand = {}
        for k, values in space.items():
            if rng.random() < 0.8:
                # aus der "guten" Verteilung ziehen
                weights = [sum(1 for o in good if o["flat"].get(k) == v) + 1.0 for v in values]
                cand[k] = rng.choices(values, weights=weights)[0]
            else:
                cand[k] = rng.choice(values)
        ratio = 0.0
        for k, values in space.items():
            ratio += math.log(dens(good, cand[k], values)) - math.log(dens(bad, cand[k], values) if bad else 1.0)
        if ratio > best_ratio:
            best_ratio, best_cand = ratio, cand
    return best_cand


# ---------------- Modus 1: Parameter-Optimierung ----------------
async def _optimize_params(job, strategy, histories, settings, cfg, objective,
                           min_trades, iterations, trade_space, progress,
                           algorithm="random", fs_map=None, should_stop=None):
    meta = strategy.DEFAULT_PARAMS or {}
    space = {}
    for k, mm in meta.items():
        try:
            lo, hi = float(mm["min"]), float(mm["max"])
            step = float(mm.get("step") or 1)
            vals, v = [], lo
            while v <= hi + 1e-9:
                vals.append(round(v, 4))
                v += step
            if len(vals) > 60:
                stride = len(vals) // 60 + 1
                vals = vals[::stride]
            if vals:
                space[k] = vals
        except (KeyError, TypeError, ValueError):
            continue
    trade_space = trade_space or {}
    # Flacher Suchraum für Bayes: Strategie-Parameter "p:", Trade-Parameter "t:"
    flat_space = {**{f"p:{k}": v for k, v in space.items()},
                  **{f"t:{k}": v for k, v in trade_space.items()}}
    rng = random.Random()
    base_params = strategy.get_params(settings)
    baseline = await asyncio.to_thread(_evaluate, strategy, histories, settings, cfg,
                                       fs_map, should_stop)
    best_score = _score(baseline, objective, min_trades)
    best = {"params": {}, "trade_params": {}, "metrics": baseline,
            "score": round(best_score, 3), "is_baseline": True}
    results = []
    history = []
    for it in range(iterations):
        if should_stop and should_stop():
            raise JobCancelled()
        if algorithm == "bayes":
            flat = _tpe_suggest(flat_space, history, rng)
        else:
            flat = {k: rng.choice(v) for k, v in flat_space.items()}
        p = {k[2:]: v for k, v in flat.items() if k.startswith("p:")}
        tp = {k[2:]: v for k, v in flat.items() if k.startswith("t:")}
        if isinstance(tp.get("tp_full_crv"), (int, float)) and isinstance(tp.get("tp1_crv"), (int, float)) \
                and tp["tp_full_crv"] < tp["tp1_crv"]:
            tp["tp_full_crv"], tp["tp1_crv"] = tp["tp1_crv"], tp["tp_full_crv"]
        sid = strategy.STRATEGY_ID
        sp = dict(settings.get("strategy_params", {}))
        sp[sid] = {**sp.get(sid, {}), **p}
        eff_settings = {**settings, "strategy_params": sp}
        eff_cfg = {**cfg, **tp}
        m = await asyncio.to_thread(_evaluate, strategy, histories, eff_settings, eff_cfg,
                                    fs_map, should_stop)
        sc = _score(m, objective, min_trades)
        results.append({"params": p, "trade_params": tp, "metrics": m, "score": round(sc, 3)})
        history.append({"flat": flat, "score": sc})
        if sc > best_score:
            best_score = sc
            best = {"params": p, "trade_params": tp, "metrics": m,
                    "score": round(sc, 3), "is_baseline": False}
            job["best"] = best
        algo_tag = "Bayes" if algorithm == "bayes" else "Random"
        progress(it + 1, iterations,
                 f"{algo_tag} · Kombination {it + 1}/{iterations} · Best Score {round(best_score, 2)}")
    top = sorted(results, key=lambda x: -x["score"])[:10]
    return {"params": base_params, "metrics": baseline}, best, top


# ---------------- Modus 2: Strategie-Discovery ----------------
async def _discover(job, histories, settings, cfg, objective, min_trades,
                    max_rules, allowed, progress, base_definition=None,
                    fs_map=None, should_stop=None):
    cands = build_candidates(allowed)
    if not cands:
        raise RuntimeError("Keine Indikatoren ausgewählt")
    if base_definition:
        definition = copy.deepcopy(base_definition)
        definition.setdefault("indicators", {})
        definition.setdefault("long_rules", [])
        definition.setdefault("short_rules", [])
    else:
        definition = {"name": "Discovery", "indicators": {}, "long_rules": [], "short_rules": []}
    used = set()
    best_score = -1e18
    best_metrics = None
    steps = []
    # Basis-Strategie zuerst bewerten (Weiterentwicklung bestehender Strategien)
    if definition["long_rules"] or definition["short_rules"]:
        m0 = await asyncio.to_thread(_evaluate, _mk_strategy(definition), histories,
                                     settings, cfg, fs_map, should_stop)
        best_score = _score(m0, objective, min_trades)
        best_metrics = m0
        steps.append({"round": 0, "added": "Basis-Strategie",
                      "score": round(best_score, 3), "metrics": m0})
        job["best"] = {"rules": _labels(definition), "metrics": m0}
    total = len(cands) * max_rules
    done = 0
    for round_i in range(max_rules):
        round_best = None
        for cand in cands:
            done += 1
            if should_stop and should_stop():
                raise JobCancelled()
            if cand["label"] in used:
                continue
            d = {**definition,
                 "long_rules": definition["long_rules"] + [dict(cand["long"])],
                 "short_rules": definition["short_rules"] + [dict(cand["short"])]}
            m = await asyncio.to_thread(_evaluate, _mk_strategy(d), histories, settings,
                                        cfg, fs_map, should_stop)
            sc = _score(m, objective, min_trades)
            progress(done, total, f"Runde {round_i + 1}: teste '{cand['label']}'")
            if round_best is None or sc > round_best[0]:
                round_best = (sc, cand, m)
        if round_best is None:
            break
        sc, cand, m = round_best
        if sc <= best_score + 1e-9:
            steps.append({"round": round_i + 1, "added": None,
                          "info": "Keine Regel verbessert den Score mehr – Stopp"})
            done = (round_i + 1) * len(cands)
            break
        best_score, best_metrics = sc, m
        definition["long_rules"].append(dict(cand["long"]))
        definition["short_rules"].append(dict(cand["short"]))
        used.add(cand["label"])
        steps.append({"round": round_i + 1, "added": cand["label"],
                      "score": round(sc, 3), "metrics": m})
        job["best"] = {"rules": _labels(definition), "metrics": m}
    return definition, best_metrics, best_score, steps


# ---------------- Modus 3: Feintuning der Schwellenwerte ----------------
async def _refine(job, definition, base_score, base_metrics, histories, settings,
                  cfg, objective, min_trades, iterations, progress,
                  fs_map=None, should_stop=None):
    numeric = [(side, i) for side in ("long_rules", "short_rules")
               for i, r in enumerate(definition.get(side, []))
               if isinstance(r.get("value"), (int, float))]
    if not numeric:
        return definition, base_metrics, []
    best_def, best_score, best_m = definition, base_score, base_metrics
    log = []
    for it in range(iterations):
        if should_stop and should_stop():
            raise JobCancelled()
        side, i = random.choice(numeric)
        d = {**best_def,
             "long_rules": [dict(r) for r in best_def["long_rules"]],
             "short_rules": [dict(r) for r in best_def["short_rules"]]}
        r = d[side][i]
        v = float(r["value"])
        delta = (abs(v) if abs(v) > 0.01 else 1.0) * random.uniform(-0.25, 0.25)
        r["value"] = round(v + delta, 3)
        m = await asyncio.to_thread(_evaluate, _mk_strategy(d), histories, settings,
                                    cfg, fs_map, should_stop)
        sc = _score(m, objective, min_trades)
        progress(it + 1, iterations, f"Feintuning {it + 1}/{iterations}")
        if sc > best_score:
            best_def, best_score, best_m = d, sc, m
            log.append({"iteration": it + 1,
                        "change": f"{r['indicator']} {r['op']} {r['value']}",
                        "score": round(sc, 3), "metrics": m})
            job["best"] = {"rules": _labels(d), "metrics": m}
    return best_def, best_m, log


# ---------------- Trade-Einstellungen für Discovery-Strategien ----------------
async def _optimize_trade_settings(job, definition, base_score, base_metrics,
                                   histories, settings, cfg, objective, min_trades,
                                   iterations, trade_space, progress,
                                   fs_map=None, should_stop=None):
    """Random-Search über Trade-Einstellungen (TP/SL, BE, Gewinnsicherung,
    Hebel, Auto-Leverage, Zeitfenster) für eine (entdeckte) Strategie."""
    rng = random.Random()
    strategy = _mk_strategy(definition)
    best_tp: Dict = {}
    best_score = base_score
    best_m = base_metrics
    for it in range(iterations):
        if should_stop and should_stop():
            raise JobCancelled()
        tp = {k: rng.choice(v) for k, v in trade_space.items()}
        if isinstance(tp.get("tp_full_crv"), (int, float)) and isinstance(tp.get("tp1_crv"), (int, float)) \
                and tp["tp_full_crv"] < tp["tp1_crv"]:
            tp["tp_full_crv"], tp["tp1_crv"] = tp["tp1_crv"], tp["tp_full_crv"]
        m = await asyncio.to_thread(_evaluate, strategy, histories, settings,
                                    {**cfg, **tp}, fs_map, should_stop)
        sc = _score(m, objective, min_trades)
        progress(it + 1, iterations, f"Trade-Einstellungen {it + 1}/{iterations}")
        if sc > best_score:
            best_score, best_tp, best_m = sc, tp, m
            job["best"] = {"rules": _labels(definition), "metrics": m, "trade_params": tp}
    return best_tp, best_m, best_score


# ---------------- Haupt-Runner ----------------
async def run_optimizer(job_id: str, body: Dict, registry, settings: Dict,
                        default_cfg: Dict, db=None):
    job = JOBS[job_id]

    def cancelled():
        return bool(job.get("cancel"))

    try:
        mode = body.get("mode", "params")
        symbols = body.get("symbols") or ["BTCUSDT"]
        days = min(max(int(body.get("days") or 3), 1), 1500)
        tf = body.get("timeframe") or "1m"
        if tf not in TIMEFRAMES:
            tf = "1m"
        objective = body.get("objective") or "combo"
        algorithm = body.get("algorithm") or "random"
        min_trades = max(int(body.get("min_trades") or 10), 1)
        iterations = min(max(int(body.get("iterations") or 40), 5), 500)
        max_rules = min(max(int(body.get("max_rules") or 4), 1), 6)
        allowed = body.get("indicators") or None
        cfg = dict(default_cfg)
        for k in ("max_capital", "leverage", "fee_percent"):
            if body.get(k) is not None:
                cfg[k] = body[k]

        # Welche Einstellungs-Gruppen sollen mitoptimiert werden?
        opt_flags = body.get("optimize")
        if not isinstance(opt_flags, dict):
            # Back-Compat: alter Schalter "include_trade_params"
            opt_flags = {"tpsl": bool(body.get("include_trade_params", True))}
        trade_space = build_trade_space(opt_flags)

        # Weiterentwicklung einer bestehenden Custom-Strategie
        base_definition = None
        bsid = body.get("base_strategy_id")
        if mode in ("discovery", "combo") and bsid:
            bstrat = registry.get(bsid)
            if not bstrat or not getattr(bstrat, "IS_CUSTOM", False):
                raise RuntimeError("Basis-Strategie muss eine Custom-Strategie sein")
            base_definition = copy.deepcopy(bstrat.definition)

        histories: Dict[str, List[Dict]] = {}
        async with aiohttp.ClientSession() as session:
            for idx, sym in enumerate(symbols):
                if cancelled():
                    raise JobCancelled()
                job["phase"] = f"Lade Daten: {sym}"
                job["progress"] = round(idx / max(len(symbols), 1) * 10)
                raw = await fetch_history(session, sym, days, job=job)
                candles = aggregate_candles(raw, tf)
                del raw
                gc.collect()
                if len(candles) > 100:
                    histories[sym] = candles
        if not histories:
            raise RuntimeError("Zu wenig Daten für diesen Timeframe/Zeitraum")

        # Vorberechnete Indikator-Serien für den schnellen Custom-Pfad
        fs_map = {sym: fast_sim.FastSeries(c) for sym, c in histories.items()}

        result = {"mode": mode, "timeframe": tf, "days": days,
                  "symbols": list(histories.keys()), "objective": objective,
                  "algorithm": algorithm, "min_trades": min_trades,
                  "optimize": opt_flags, "max_capital": cfg.get("max_capital")}

        if mode == "params":
            sid = body.get("strategy_id")
            strategy = registry.get(sid)
            if not strategy:
                raise RuntimeError("Strategie nicht gefunden")

            def prog(done, total, phase):
                job["progress"] = 10 + round(done / max(total, 1) * 89)
                job["phase"] = phase

            baseline, best, top = await _optimize_params(
                job, strategy, histories, settings, cfg, objective, min_trades,
                iterations, trade_space, prog,
                algorithm, fs_map, cancelled)
            result.update({"strategy_id": sid,
                           "strategy_name": getattr(strategy, "STRATEGY_NAME", sid),
                           "baseline": baseline, "best": best, "top": top,
                           "iterations": iterations})
        else:
            do_refine = mode == "combo"
            do_trade = bool(trade_space)
            span_end = 99
            if do_refine and do_trade:
                span_end = 55
            elif do_refine or do_trade:
                span_end = 70

            def prog_d(done, total, phase):
                job["progress"] = 10 + round(done / max(total, 1) * (span_end - 10))
                job["phase"] = phase

            definition, best_m, best_sc, steps = await _discover(
                job, histories, settings, cfg, objective, min_trades,
                max_rules, allowed, prog_d, base_definition, fs_map, cancelled)
            refine_log = []
            refine_end = span_end
            if do_refine and best_m:
                refine_end = 80 if do_trade else 99

                def prog_r(done, total, phase, _s=span_end, _e=refine_end):
                    job["progress"] = _s + round(done / max(total, 1) * (_e - _s))
                    job["phase"] = phase

                definition, best_m, refine_log = await _refine(
                    job, definition, best_sc, best_m, histories, settings, cfg,
                    objective, min_trades, iterations, prog_r, fs_map, cancelled)
                best_sc = _score(best_m, objective, min_trades) if best_m else best_sc
            best_trade_params = {}
            if do_trade and best_m:
                def prog_t(done, total, phase, _s=refine_end):
                    job["progress"] = _s + round(done / max(total, 1) * (99 - _s))
                    job["phase"] = phase

                best_trade_params, best_m, best_sc = await _optimize_trade_settings(
                    job, definition, best_sc, best_m, histories, settings, cfg,
                    objective, min_trades, iterations, trade_space, prog_t,
                    fs_map, cancelled)
            result.update({"definition": definition, "rules": _labels(definition),
                           "metrics": best_m, "steps": steps, "refine_log": refine_log,
                           "trade_params": best_trade_params,
                           "base_strategy_id": bsid if base_definition else None})

        job["result"] = result
        job["status"] = "done"
        job["progress"] = 100
        job["phase"] = "Fertig"
        if db is not None:
            try:
                await db.optimizer_runs.insert_one({"id": job_id, "params": job["params"],
                                                    "created_at": job["created_at"],
                                                    "result": result})
            except Exception as e:
                logger.warning(f"optimizer persist failed: {e}")
    except JobCancelled:
        job["status"] = "cancelled"
        job["phase"] = "Abgebrochen"
        job["error"] = None
        logger.info(f"optimizer {job_id} cancelled")
    except Exception as e:
        logger.exception("optimizer failed")
        job["status"] = "error"
        job["error"] = str(e)[:300]
