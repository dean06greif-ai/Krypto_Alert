"""Robustheits-Layer für Strategy Finder / Optimizer / Kombi.

Optionale, rückwärtskompatible Zusatz-Prüfungen (alle per Body-Flag aktivierbar):
- Walk-Forward:     Training/Test-Split. Strategien werden nur auf den
                    Trainingsdaten gefunden/optimiert und danach auf unbekannten
                    Testdaten geprüft. Der WF-Score bevorzugt Strategien, die auf
                    BEIDEN Datensätzen ähnlich gut laufen (Overfitting-Schutz).
- Drawdown-Filter:  max. Drawdown relativ zum PnL (z.B. 40% -> DD darf höchstens
                    40% des PnL betragen).
- Konstanz-Test:    Zeitraum in Abschnitte teilen (z.B. 30 Tage) und prüfen, ob
                    der Gewinn gleichmäßig verteilt ist oder nur aus wenigen
                    Phasen stammt.
- TopTracker:       hält die besten N unterschiedlichen Kandidaten eines Laufs
                    (für die Top-5-Anzeige).

Kein bestehender Optimizer-Pfad wird verändert – dieses Modul wird nur additiv
von services.optimizer genutzt.
"""
import asyncio
import json
import math
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

DEFAULT_TRAIN_PCT = 75.0
DEFAULT_DD_MAX_PCT = 40.0
DEFAULT_CT_CHUNK_DAYS = 30
DEFAULT_CT_MAX_DEV_PCT = 20.0


def _num(v, dflt, lo, hi):
    if v is None:
        return dflt
    try:
        return min(max(float(v), lo), hi)
    except (TypeError, ValueError):
        return dflt


def parse_config(body: Dict) -> Dict:
    """Robustheits-Konfiguration aus dem Request-Body lesen (alles optional)."""
    wf = body.get("walk_forward") or {}
    dd = body.get("dd_filter") or {}
    ct = body.get("constancy") or {}
    st = body.get("stress_test") or {}
    sb = body.get("stability") or {}
    mc = body.get("monte_carlo") or {}
    rg = body.get("regime_analysis") or {}
    _wfm = str(wf.get("mode") or "").lower()
    cfg = {
        "wf_enabled": bool(wf.get("enabled")),
        "wf_mode": _wfm if _wfm in ("rolling", "anchored") else "single",
        "wf_windows": int(_num(wf.get("windows"), 4, 2, 12)),
        "train_pct": _num(wf.get("train_pct"), DEFAULT_TRAIN_PCT, 50.0, 95.0),
        "dd_enabled": bool(dd.get("enabled")),
        "dd_max_pct": _num(dd.get("max_dd_pct"), DEFAULT_DD_MAX_PCT, 1.0, 1000.0),
        "ct_enabled": bool(ct.get("enabled")),
        "ct_chunk_days": int(_num(ct.get("chunk_days"), DEFAULT_CT_CHUNK_DAYS, 2, 365)),
        "ct_max_dev_pct": _num(ct.get("max_deviation_pct"), DEFAULT_CT_MAX_DEV_PCT, 1.0, 1000.0),
        # 1. Fee-/Slippage-Stresstest
        "st_enabled": bool(st.get("enabled")),
        "st_mult": _num(st.get("cost_multiplier"), 1.5, 1.1, 5.0),
        # 3. Parameter-Stabilität
        "sb_enabled": bool(sb.get("enabled")),
        "sb_var_pct": _num(sb.get("variation_pct"), 10.0, 1.0, 50.0),
        # 2. Monte-Carlo (Trade-Reihenfolge mischen -> Drawdown-Verteilung)
        "mc_enabled": bool(mc.get("enabled")),
        "mc_runs": int(_num(mc.get("runs"), 200, 50, 2000)),
        "mc_max_dd_pct": _num(mc.get("max_dd_p95_pct"), 100.0, 10.0, 1000.0),
        # 4. Regime-Aufschlüsselung (nur Info, kein Filter)
        "rg_enabled": bool(rg.get("enabled")),
    }
    cfg["any"] = (cfg["wf_enabled"] or cfg["dd_enabled"] or cfg["ct_enabled"]
                  or cfg["st_enabled"] or cfg["sb_enabled"] or cfg["mc_enabled"]
                  or cfg["rg_enabled"])
    return cfg


# ---------------- Walk-Forward ----------------
def split_histories(histories: Dict[str, List[Dict]], train_pct: float
                    ) -> Tuple[Dict[str, List[Dict]], Dict[str, List[Dict]]]:
    """Kerzen-Historien chronologisch in Training/Test aufteilen."""
    train, test = {}, {}
    for sym, candles in histories.items():
        cut = int(len(candles) * train_pct / 100.0)
        train[sym] = candles[:cut]
        test[sym] = candles[cut:]
    return train, test


def _iso_date(ts_ms) -> Optional[str]:
    try:
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).date().isoformat()
    except (TypeError, ValueError, OSError):
        return None


def rolling_windows(histories: Dict[str, List[Dict]], train_pct: float,
                    n_windows: int, anchored: bool = False) -> List[Dict]:
    """Walk-Forward-Fenster über den Gesamtzeitraum.
    anchored=False (Rolling): Training gleitet mit konstanter Länge.
      Fenster i: Training = [i*test_len, i*test_len+train_len)
    anchored=True (Anchored): Training beginnt immer am Anfang und WÄCHST.
      Fenster i: Training = [0, train_len+i*test_len)
    Test = jeweils direkt anschließend (test_len Kerzen); die W Test-Segmente
    decken zusammen den kompletten Out-of-Sample-Anteil ab.
    Rückgabe: [{"train": {sym: candles}, "test": {sym: candles}, "range": {...}}]"""
    wins = []
    for i in range(n_windows):
        train, test = {}, {}
        rng = None
        for sym, candles in histories.items():
            n = len(candles)
            train_len = int(n * train_pct / 100.0)
            test_len = max(int((n - train_len) / n_windows), 1)
            if anchored:
                tr = candles[0: train_len + i * test_len]
            else:
                tr = candles[i * test_len: i * test_len + train_len]
            te = candles[train_len + i * test_len: train_len + (i + 1) * test_len]
            train[sym] = tr
            test[sym] = te
            if rng is None and tr and te:
                rng = {"train_from": _iso_date(tr[0]["timestamp"]),
                       "train_to": _iso_date(tr[-1]["timestamp"]),
                       "test_from": _iso_date(te[0]["timestamp"]),
                       "test_to": _iso_date(te[-1]["timestamp"])}
        wins.append({"train": train, "test": test, "range": rng or {}})
    return wins


def aggregate_rolling(window_evals: List[Dict]) -> Dict:
    """Fenster-Ergebnisse zu einem Gesamt-WF-Score verdichten."""
    n = len(window_evals)
    if not n:
        return {"wf_score": 0.0, "consistency_pct": 0.0,
                "positive_windows_pct": 0.0, "windows": 0}
    wf = sum(w.get("wf_score", 0.0) for w in window_evals) / n
    cons = sum(w.get("consistency_pct", 0.0) for w in window_evals) / n
    pos = sum(1 for w in window_evals
              if float((w.get("test_metrics") or {}).get("pnl") or 0) > 0) / n * 100.0
    return {"wf_score": round(wf, 4), "consistency_pct": round(cons, 1),
            "positive_windows_pct": round(pos, 1), "windows": n}


def combine_test_metrics(metrics_list: List[Dict]) -> Dict:
    """Test-Metriken mehrerer Fenster kombinieren (PnL/Trades summiert,
    Drawdown konservativ = schlechtestes Fenster)."""
    tot = {"trades": 0, "wins": 0, "losses": 0, "breakevens": 0,
           "pnl": 0.0, "fees": 0.0, "max_drawdown": 0.0}
    for m in metrics_list:
        for k in ("trades", "wins", "losses", "breakevens"):
            tot[k] += int(m.get(k) or 0)
        tot["pnl"] += float(m.get("pnl") or 0)
        tot["fees"] += float(m.get("fees") or 0)
        tot["max_drawdown"] = max(tot["max_drawdown"], float(m.get("max_drawdown") or 0))
    decided = tot["wins"] + tot["losses"]
    tot["win_rate"] = round(tot["wins"] / decided * 100, 1) if decided else 0.0
    tot["pnl"] = round(tot["pnl"], 2)
    tot["fees"] = round(tot["fees"], 2)
    tot["max_drawdown"] = round(tot["max_drawdown"], 2)
    return tot


def _quality(m: Dict, span_days: float) -> float:
    """Vergleichbare Qualitätszahl pro Datensatz: PnL/Tag, gewichtet mit Winrate
    (gleiche Idee wie das 'combo'-Ziel, aber zeit-normiert für den WF-Vergleich)."""
    pnl = float(m.get("pnl") or 0.0)
    wr = float(m.get("win_rate") or 0.0)
    per_day = pnl / max(span_days, 0.01)
    return per_day * (0.5 + wr / 200.0)


def walk_forward_eval(train_m: Dict, test_m: Dict,
                      train_days: float, test_days: float) -> Dict:
    """WF-Score: hoch, wenn Training UND Test positiv sind und ähnlich gut laufen.
    Nur-Training-gut oder nur-Test-gut wird abgewertet (Zufall/Overfitting)."""
    qa = _quality(train_m, train_days)
    qb = _quality(test_m, test_days)
    if qa <= 0 or qb <= 0:
        consistency = 0.0
        score = min(qa, qb)
    else:
        consistency = min(qa, qb) / max(qa, qb)
        score = (qa + qb) / 2.0 * (0.4 + 0.6 * consistency)
    return {"wf_score": round(score, 4),
            "consistency_pct": round(consistency * 100, 1),
            "train_quality": round(qa, 4), "test_quality": round(qb, 4)}


# ---------------- Drawdown-Filter ----------------
def dd_check(metrics: Dict, max_dd_pct: float) -> Tuple[bool, Optional[float]]:
    """(bestanden, DD-in-%-vom-PnL). PnL <= 0 fällt immer durch (Ratio undefiniert)."""
    pnl = float(metrics.get("pnl") or 0.0)
    dd = float(metrics.get("max_drawdown") or 0.0)
    if pnl <= 0:
        return False, None
    ratio = dd / pnl * 100.0
    return ratio <= max_dd_pct, round(ratio, 1)


# ---------------- Konstanz-Test / Trade-Sammlung ----------------
async def collect_trades_list(strategy, histories: Dict[str, List[Dict]],
                              settings: Dict, cfg: Dict, fs_map: Dict = None,
                              should_stop=None) -> List[Tuple[str, float, float]]:
    """Alle geschlossenen Trades als (symbol, close_ts_ms, pnl) – EINE Simulation
    pro Symbol, wird von Konstanz-Test, Monte-Carlo und Regime-Analyse geteilt."""
    from services.backtester import simulate_pair
    from services import fast_sim
    out = []
    for sym, candles in histories.items():
        provider = None
        if fs_map is not None and sym in fs_map:
            try:
                if getattr(strategy, "IS_CUSTOM", False):
                    provider = fast_sim.build_signal_provider(strategy.definition, fs_map[sym])
                else:
                    provider = fast_sim.build_builtin_signal_provider(
                        strategy, fs_map[sym], settings, sym)
            except Exception:  # noqa: BLE001 – Fallback wie im Optimizer
                provider = None
        r = await asyncio.to_thread(simulate_pair, strategy, candles, sym, settings,
                                    cfg, None, True, should_stop, provider)
        for t in r.get("all_trades") or []:
            closed = t.get("closed")
            if not closed:
                continue
            try:
                ts = datetime.fromisoformat(closed).timestamp() * 1000
            except ValueError:
                continue
            out.append((sym, ts, float(t.get("pnl") or 0.0)))
    out.sort(key=lambda x: x[1])
    return out


def chunk_pnls_from_trades(trades: List[Tuple[str, float, float]],
                           histories: Dict[str, List[Dict]],
                           chunk_days: int) -> List[float]:
    """PnL je Zeit-Abschnitt aus einer bereits gesammelten Trade-Liste."""
    chunk_ms = chunk_days * 86400000
    starts = [c[0]["timestamp"] for c in histories.values() if c]
    ends = [c[-1]["timestamp"] for c in histories.values() if c]
    if not starts:
        return []
    start_ts, end_ts = min(starts), max(ends)
    n_chunks = max(1, math.ceil((end_ts - start_ts + 1) / chunk_ms))
    pnls = [0.0] * n_chunks
    for _sym, ts, pnl in trades:
        idx = min(max(int((ts - start_ts) // chunk_ms), 0), n_chunks - 1)
        pnls[idx] += pnl
    return pnls


async def collect_chunk_pnls(strategy, histories: Dict[str, List[Dict]],
                             settings: Dict, cfg: Dict, chunk_days: int,
                             fs_map: Dict = None, should_stop=None) -> List[float]:
    """PnL je Zeit-Abschnitt über alle Symbole (Trades nach Schließzeit gebucht)."""
    trades = await collect_trades_list(strategy, histories, settings, cfg,
                                       fs_map, should_stop)
    return chunk_pnls_from_trades(trades, histories, chunk_days)


def evaluate_chunks(chunk_pnls: List[float], max_dev_pct: float) -> Dict:
    """Konstanz bewerten: relative Streuung (std/mean) der Abschnitts-PnLs.
    Durchschnitt <= 0 fällt immer durch (kein konstanter Gewinn vorhanden)."""
    n = len(chunk_pnls)
    if n == 0:
        return {"chunks": 0, "chunk_pnls": [], "mean_pnl": 0.0,
                "deviation_pct": None, "profitable_chunks_pct": 0.0, "passed": False}
    mean = sum(chunk_pnls) / n
    profitable = sum(1 for p in chunk_pnls if p > 0) / n * 100.0
    if n < 2:
        return {"chunks": n, "chunk_pnls": [round(p, 2) for p in chunk_pnls],
                "mean_pnl": round(mean, 2), "deviation_pct": 0.0,
                "profitable_chunks_pct": round(profitable, 1), "passed": mean > 0}
    std = (sum((p - mean) ** 2 for p in chunk_pnls) / n) ** 0.5
    if mean <= 0:
        deviation = None
        passed = False
    else:
        deviation = std / mean * 100.0
        passed = deviation <= max_dev_pct
    return {"chunks": n, "chunk_pnls": [round(p, 2) for p in chunk_pnls],
            "mean_pnl": round(mean, 2),
            "deviation_pct": round(deviation, 1) if deviation is not None else None,
            "profitable_chunks_pct": round(profitable, 1), "passed": passed}


# ---------------- 1. Fee-/Slippage-Stresstest ----------------
def stressed_cfg(cfg: Dict, cost_multiplier: float) -> Dict:
    """Trade-Konfiguration mit vervielfachten Kosten (fee_percent)."""
    return {**cfg, "fee_percent": float(cfg.get("fee_percent", 0.06)) * cost_multiplier}


# ---------------- 3. Parameter-Stabilität ----------------
def _shift(v, factor):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return v
    nv = v * (1.0 + factor)
    return round(nv) if isinstance(v, int) else round(nv, 6)


def perturb_definition(definition: Dict, factor: float) -> Dict:
    """Alle numerischen Regel-Schwellen um `factor` (z.B. ±0.1) verschieben."""
    d = {**definition,
         "long_rules": [dict(r) for r in definition.get("long_rules") or []],
         "short_rules": [dict(r) for r in definition.get("short_rules") or []]}
    for r in d["long_rules"] + d["short_rules"]:
        r["value"] = _shift(r.get("value"), factor)
    return d


def perturb_params(params: Dict, factor: float) -> Dict:
    return {k: _shift(v, factor) for k, v in (params or {}).items()}


def stability_eval(base_pnl: float, variant_pnls: List, var_pct: float) -> Dict:
    """Wie viele ±Varianten bleiben profitabel? Plateau (robust) vs. Spike (Zufall)."""
    vals = [float(p or 0.0) for p in variant_pnls]
    n = len(vals)
    pos = sum(1 for p in vals if p > 0) / n * 100.0 if n else 0.0
    retention = (sum(vals) / n / base_pnl * 100.0) if (n and base_pnl > 0) else 0.0
    return {"variation_pct": var_pct, "variants": n,
            "positive_pct": round(pos, 1),
            "retention_pct": round(max(min(retention, 999.0), -999.0), 1),
            "passed": base_pnl > 0 and pos >= 50.0}


# ---------------- 2. Monte-Carlo ----------------
def monte_carlo(trade_pnls: List[float], runs: int, max_dd_p95_pct: float) -> Dict:
    """Trade-Reihenfolge `runs`-mal mischen -> Drawdown-Verteilung statt Einzelwert."""
    import random
    arr = [float(p) for p in trade_pnls]
    total = round(sum(arr), 2)
    if len(arr) < 3 or total <= 0:
        return {"runs": 0, "total_pnl": total, "dd_p50": None, "dd_p95": None,
                "dd_worst": None, "dd_p95_pct": None, "passed": False}
    rnd = random.Random(42)  # deterministisch/reproduzierbar
    dds = []
    for _ in range(runs):
        rnd.shuffle(arr)
        eq = peak = dd = 0.0
        for p in arr:
            eq += p
            peak = max(peak, eq)
            dd = max(dd, peak - eq)
        dds.append(dd)
    dds.sort()
    p50 = dds[len(dds) // 2]
    p95 = dds[min(int(len(dds) * 0.95), len(dds) - 1)]
    p95_pct = p95 / total * 100.0
    return {"runs": runs, "total_pnl": total,
            "dd_p50": round(p50, 2), "dd_p95": round(p95, 2),
            "dd_worst": round(dds[-1], 2), "dd_p95_pct": round(p95_pct, 1),
            "passed": p95_pct <= max_dd_p95_pct}


# ---------------- 4. Regime-Aufschlüsselung ----------------
def classify_regimes(candles: List[Dict], thresh_pct: float = 1.0) -> List[str]:
    """Je Kerze: 'bull' | 'bear' | 'sideways' anhand der SMA-Steigung."""
    import numpy as np
    import pandas as pd
    n = len(candles)
    if n < 30:
        return ["sideways"] * n
    closes = np.array([float(c["close"]) for c in candles])
    win = min(200, max(20, n // 10))
    look = max(10, win // 4)
    sma = pd.Series(closes).rolling(win, min_periods=1).mean().to_numpy()
    out = ["sideways"] * n
    for i in range(n):
        j = max(i - look, 0)
        base = sma[j]
        if base <= 0:
            continue
        slope = (sma[i] - base) / base * 100.0
        if slope > thresh_pct:
            out[i] = "bull"
        elif slope < -thresh_pct:
            out[i] = "bear"
    return out


def regime_breakdown(trades: List[Tuple[str, float, float]],
                     histories: Dict[str, List[Dict]]) -> Dict:
    """Trade-PnL je Marktphase (Regime zur Schließzeit des Trades)."""
    import bisect
    cls, ts_idx = {}, {}
    for sym, candles in histories.items():
        cls[sym] = classify_regimes(candles)
        ts_idx[sym] = [c["timestamp"] for c in candles]
    agg = {k: {"pnl": 0.0, "trades": 0} for k in ("bull", "bear", "sideways")}
    for sym, ts, pnl in trades:
        if sym not in ts_idx or not ts_idx[sym]:
            continue
        i = min(max(bisect.bisect_right(ts_idx[sym], ts) - 1, 0), len(cls[sym]) - 1)
        reg = cls[sym][i]
        agg[reg]["pnl"] += pnl
        agg[reg]["trades"] += 1
    for v in agg.values():
        v["pnl"] = round(v["pnl"], 2)
    return agg


# ---------------- Top-N-Tracker ----------------
def rule_key(definition: Dict, trade_params: Dict = None) -> str:
    """Dedupe-Schlüssel: gleiche Regeln + gleiche Trade-Parameter = gleicher Kandidat."""
    return json.dumps({"l": definition.get("long_rules") or [],
                       "s": definition.get("short_rules") or [],
                       "tp": trade_params or {}}, sort_keys=True, default=str)


class TopTracker:
    """Hält die besten `limit` unterschiedlichen Kandidaten (dedupe per key)."""

    def __init__(self, limit: int = 10):
        self.limit = limit
        self._items: Dict[str, Dict] = {}

    def add(self, key: str, entry: Dict):
        cur = self._items.get(key)
        if cur is None or entry.get("score", -1e18) > cur.get("score", -1e18):
            self._items[key] = entry
        if len(self._items) > self.limit * 4:
            keep = sorted(self._items.items(), key=lambda kv: -kv[1].get("score", -1e18))
            self._items = dict(keep[: self.limit * 2])

    def top(self, n: int = None) -> List[Dict]:
        return sorted(self._items.values(),
                      key=lambda e: -e.get("score", -1e18))[: (n or self.limit)]
