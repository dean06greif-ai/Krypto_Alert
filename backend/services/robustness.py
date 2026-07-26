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
from datetime import datetime
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
    cfg = {
        "wf_enabled": bool(wf.get("enabled")),
        "train_pct": _num(wf.get("train_pct"), DEFAULT_TRAIN_PCT, 50.0, 95.0),
        "dd_enabled": bool(dd.get("enabled")),
        "dd_max_pct": _num(dd.get("max_dd_pct"), DEFAULT_DD_MAX_PCT, 1.0, 1000.0),
        "ct_enabled": bool(ct.get("enabled")),
        "ct_chunk_days": int(_num(ct.get("chunk_days"), DEFAULT_CT_CHUNK_DAYS, 2, 365)),
        "ct_max_dev_pct": _num(ct.get("max_deviation_pct"), DEFAULT_CT_MAX_DEV_PCT, 1.0, 1000.0),
    }
    cfg["any"] = cfg["wf_enabled"] or cfg["dd_enabled"] or cfg["ct_enabled"]
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


# ---------------- Konstanz-Test ----------------
async def collect_chunk_pnls(strategy, histories: Dict[str, List[Dict]],
                             settings: Dict, cfg: Dict, chunk_days: int,
                             fs_map: Dict = None, should_stop=None) -> List[float]:
    """PnL je Zeit-Abschnitt über alle Symbole (Trades nach Schließzeit gebucht)."""
    from services.backtester import simulate_pair
    from services import fast_sim
    chunk_ms = chunk_days * 86400000
    starts = [c[0]["timestamp"] for c in histories.values() if c]
    ends = [c[-1]["timestamp"] for c in histories.values() if c]
    if not starts:
        return []
    start_ts, end_ts = min(starts), max(ends)
    n_chunks = max(1, math.ceil((end_ts - start_ts + 1) / chunk_ms))
    pnls = [0.0] * n_chunks
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
            idx = min(max(int((ts - start_ts) // chunk_ms), 0), n_chunks - 1)
            pnls[idx] += float(t.get("pnl") or 0.0)
    return pnls


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
