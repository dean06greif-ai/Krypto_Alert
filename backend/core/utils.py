"""Gemeinsame Helfer (aus server.py verschoben, Logik unverändert)."""
import logging
from datetime import datetime, timezone
from typing import Dict, List

logger = logging.getLogger(__name__)


def _clean(d: Dict) -> Dict:
    d = dict(d)
    d.pop("_id", None)
    return d


def _enrich_trade(t: Dict) -> Dict:
    """Add computed analytics fields to a trade without changing stored schema.
    Gives the UI and the AI exact numbers: durations, distances (%), R-multiple.
    """
    t = _clean(t)
    entry = float(t.get("entry") or 0)
    side = t.get("side", "LONG")
    sl = float(t.get("sl") or 0)
    init_sl = float(t.get("initial_sl") or sl or 0)
    tp1 = float(t.get("tp1") or 0)
    tpf = float(t.get("tpf") or 0)
    qty = float(t.get("qty") or 0)
    risk = float(t.get("risk") or 0)
    exit_price = t.get("exit_price")

    def pct_from_entry(p):
        if not entry or not p:
            return None
        return round((p - entry) / entry * 100, 3)

    # timings
    dur = None
    o, c = t.get("opened_at"), t.get("closed_at")
    try:
        if o:
            o_dt = datetime.fromisoformat(o.replace("Z", "+00:00"))
            end_dt = datetime.fromisoformat(c.replace("Z", "+00:00")) if c \
                else datetime.now(timezone.utc)
            dur = int((end_dt - o_dt).total_seconds())
    except Exception:
        dur = None

    # R-multiple: realized PnL relative to the initial 1R risk in USDT
    risk_usd = round(risk * qty, 4) if (risk and qty) else 0.0
    r_multiple = None
    if risk_usd:
        r_multiple = round(float(t.get("realized_pnl") or 0) / risk_usd, 2)

    # PnL in % on the used capital (margin)
    capital = float(t.get("max_capital") or 0)
    pnl_pct_capital = None
    if capital:
        pnl_pct_capital = round(float(t.get("realized_pnl") or 0) / capital * 100, 2)

    # PnL in % of the position size (entry * qty)
    pos_size = entry * qty
    pnl_pct = None
    if pos_size:
        pnl_pct = round(float(t.get("realized_pnl") or 0) / pos_size * 100, 2)

    t["computed"] = {
        "duration_seconds": dur,
        "risk_usd": risk_usd,
        "r_multiple": r_multiple,
        "pnl_pct_capital": pnl_pct_capital,
        "pnl_pct": pnl_pct,
        "sl_distance_pct": pct_from_entry(sl),
        "initial_sl_distance_pct": pct_from_entry(init_sl),
        "tp1_distance_pct": pct_from_entry(tp1),
        "tpf_distance_pct": pct_from_entry(tpf),
        "exit_distance_pct": pct_from_entry(float(exit_price)) if exit_price else None,
        "rr_tp1": t.get("tp1_crv"),
        "rr_tpf": t.get("tp_full_crv"),
        "sl_moved": round(sl - init_sl, 6) if (sl and init_sl) else 0,
        "side": side,
    }
    return t


def _watch_job_task(task, jobs: Dict, job_id: str):
    """Ghost-Job-Schutz: Stirbt der Task, ohne den Status zu setzen,
    wird der Job als Fehler markiert (vorher: 'läuft' blockierte für immer)."""
    def _done(t):
        job = jobs.get(job_id)
        if job and job.get("status") == "running":
            job["status"] = "error"
            job["error"] = "Job-Task unerwartet beendet (automatisch zurückgesetzt)"
            job["phase"] = "Fehler"
        if t.cancelled():
            return
        exc = t.exception()
        if exc:
            logger.error(f"job task {job_id} crashed: {exc}")
    task.add_done_callback(_done)


def _job_public(job: Dict) -> Dict:
    """Job ohne Export-Rohdaten (sonst riesige Antworten) + ETA."""
    j = {k: v for k, v in job.items()
         if k not in ("export_candles", "export_trades", "_bench")}
    try:
        created = datetime.fromisoformat(job["created_at"])
        elapsed = (datetime.now(timezone.utc) - created).total_seconds()
        j["elapsed_seconds"] = int(elapsed)
        p = job.get("progress") or 0
        if job.get("status") == "running" and p >= 2:
            j["eta_seconds"] = int(elapsed / p * (100 - p))
    except Exception:
        pass
    return j


def _equity_points(rows: List[Dict]) -> List[Dict]:
    rows = [r for r in rows if r.get("closed")]
    rows.sort(key=lambda r: r["closed"])
    points = []
    eq, peak = 0.0, 0.0
    for r in rows:
        pnl = float(r.get("pnl") or 0)
        eq += pnl
        peak = max(peak, eq)
        points.append({"t": r["closed"], "equity": round(eq, 4),
                       "peak": round(peak, 4), "drawdown": round(peak - eq, 4),
                       "pnl": round(pnl, 4), "symbol": r.get("symbol"),
                       "strategy_id": r.get("strategy_id"),
                       "strategy_name": r.get("strategy_name"),
                       "side": r.get("side"), "result": r.get("result"),
                       "liquidated": bool(r.get("liquidated"))})
    return points


def _rows_to_csv(rows: List[Dict], fieldnames: List[str]) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()
