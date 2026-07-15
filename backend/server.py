from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from contextlib import asynccontextmanager
from dotenv import load_dotenv
import os
import logging
import asyncio
import uuid
import jwt
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from typing import List, Dict

load_dotenv()

BERLIN = ZoneInfo("Europe/Berlin")

from services.market_data import MarketDataFeed
from services.strategy_scanner import StrategyScanner
from services.telegram_bot import TelegramNotifier
from services.bitunix_trade import BitunixTradeClient, AutoTradeManager, DEFAULT_COIN_CFG
from strategies.registry import registry as strategy_registry

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOP_10_COINS = ["BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
                "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "POLUSDT"]
OTHER_INSTRUMENTS = [
    {"symbol": "GOLD", "yahoo": "GC=F", "name": "Gold"},
    {"symbol": "SILVER", "yahoo": "SI=F", "name": "Silver"},
    {"symbol": "OIL", "yahoo": "CL=F", "name": "Oil"},
]
OTHER_YAHOO = {i["symbol"]: i["yahoo"] for i in OTHER_INSTRUMENTS}
ALL_SYMBOLS = TOP_10_COINS + [i["symbol"] for i in OTHER_INSTRUMENTS]

scanner = StrategyScanner()
telegram = TelegramNotifier()
feed = MarketDataFeed()
trade_client = BitunixTradeClient()
autotrader = AutoTradeManager(trade_client)

websocket_clients: List[WebSocket] = []
open_signal_evals: List[Dict] = []   # in-memory outcome tracking for today's signals
POLL_INTERVAL = 12
scanner_running = asyncio.Event()
_last_reset_date = None

# global admin "kill-switches" (toggles). When ON they act as a regulator that
# temporarily stops the bot from opening new trades / emitting new signals
# without disabling per-coin/strategy configuration.
control_state: Dict[str, bool] = {"trades_paused": False, "signals_paused": False}


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Crypto Scalping Scanner...")
    app.mongodb_client = AsyncIOMotorClient(os.getenv("MONGO_URL"))
    app.mongodb = app.mongodb_client[os.getenv("DB_NAME", "crypto_scanner")]
    autotrader.set_db(app.mongodb)
    autotrader.set_telegram(telegram)
    # Load Bitunix contract catalogue so the symbol mapping is validated
    # against the real contract list and qty/price step sizes are known.
    try:
        await trade_client.load_trading_pairs()
    except Exception as e:
        logger.error(f"Bitunix trading_pairs load failed: {e}")
    logger.info("Connected to MongoDB")

    saved = await app.mongodb.settings.find_one({"_id": "scanner_settings"})
    if saved:
        saved.pop("_id", None)
        scanner.update_settings(saved)
    else:
        await app.mongodb.settings.insert_one({"_id": "scanner_settings", **scanner.settings})

    # load custom strategies
    customs = await app.mongodb.custom_strategies.find().to_list(100)
    for c in customs:
        c.pop("_id", None)
    strategy_registry.load_custom(customs)

    # load autotrade config
    at_cfg = await app.mongodb.settings.find_one({"_id": "autotrade_config"})
    if at_cfg:
        at_cfg.pop("_id", None)
        autotrader.set_config(at_cfg)
    else:
        cfg = {"mode": os.getenv("TRADING_MODE", "paper"), "coins": {}}
        await app.mongodb.settings.insert_one({"_id": "autotrade_config", **cfg})
        autotrader.set_config(cfg)

    # load admin control toggles (stop trades / stop signals)
    ctrl = await app.mongodb.settings.find_one({"_id": "control_state"})
    if ctrl:
        control_state["trades_paused"] = bool(ctrl.get("trades_paused", False))
        control_state["signals_paused"] = bool(ctrl.get("signals_paused", False))
    else:
        await app.mongodb.settings.insert_one({"_id": "control_state", **control_state})

    logger.info("Probing market data sources...")
    await feed.probe("BTCUSDT")
    for symbol in TOP_10_COINS:
        try:
            hist = await feed.fetch(symbol, 200)
            scanner.bootstrap(symbol, hist[:-1] if len(hist) > 1 else hist)
        except Exception as e:
            logger.error(f"Bootstrap failed for {symbol}: {e}")
        await asyncio.sleep(0.15)
    for inst in OTHER_INSTRUMENTS:
        try:
            hist = await feed.fetch_commodity(inst["yahoo"], "5d")
            closed = hist[:-1] if len(hist) > 1 else hist
            scanner.bootstrap(inst["symbol"], closed[-200:])
        except Exception as e:
            logger.error(f"Bootstrap failed for {inst['symbol']}: {e}")
        await asyncio.sleep(0.15)

    asyncio.create_task(start_scanner())
    asyncio.create_task(daily_reset_loop())
    # initial analyze so rule-states are populated immediately
    for symbol in ALL_SYMBOLS:
        try:
            scanner.analyze_symbol(symbol)
        except Exception:
            pass
    if telegram.bot:
        await telegram.send_test_message()
    yield
    logger.info("Shutting down...")
    scanner_running.clear()
    await feed.close()
    app.mongodb_client.close()


app = FastAPI(title="Crypto Scalping Scanner", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_credentials=True,
                   allow_methods=["*"], allow_headers=["*"])


# ---------------- admin auth (protects WRITE actions; GET + health stay public) ----------------
JWT_SECRET = os.getenv("JWT_SECRET", "change-me")
ADMIN_USER = os.getenv("ADMIN_USER", "Admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin")


def create_admin_token() -> str:
    payload = {"sub": "admin", "exp": datetime.now(timezone.utc) + timedelta(days=30)}
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


async def require_admin(request: Request):
    auth = request.headers.get("Authorization", "")
    token = auth[7:] if auth.startswith("Bearer ") else None
    if not token:
        raise HTTPException(status_code=401, detail="Admin-Login erforderlich")
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        if payload.get("sub") != "admin":
            raise HTTPException(status_code=401, detail="Ungültiges Token")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token abgelaufen")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Ungültiges Token")
    return True


@app.post("/api/auth/login")
async def admin_login(body: Dict):
    user = (body.get("username") or "").strip()
    pw = body.get("password") or ""
    if pw == ADMIN_PASSWORD and (not ADMIN_USER or user == ADMIN_USER or not user):
        return {"token": create_admin_token(), "user": ADMIN_USER}
    raise HTTPException(status_code=401, detail="Falsche Zugangsdaten")


@app.get("/api/auth/verify")
async def admin_verify(_: bool = Depends(require_admin)):
    return {"valid": True}


# ---------------- signal processing ----------------
async def process_signal(signal: Dict, candles: List[Dict]):
    # Global admin kill-switch for signals -> completely suppress emission
    if control_state.get("signals_paused"):
        return
    signal["id"] = str(uuid.uuid4())
    symbol = signal["symbol"]
    notify = scanner.is_notify_enabled(symbol)
    signal["notify"] = notify
    await app.mongodb.signals.insert_one(dict(signal))
    if signal.get("signal_class") != "PRE_SIGNAL":
        open_signal_evals.append({
            "id": signal["id"], "symbol": symbol, "type": signal["type"],
            "sl": signal["stop_loss"], "tp1": signal["take_profit_1"],
            "strategy_id": signal.get("strategy_id"),
        })
    if notify:
        await telegram.send_signal(signal)
    await update_performance(signal, opened=True)
    try:
        # Global admin kill-switch for auto-trades -> do not open new trades
        if not control_state.get("trades_paused"):
            await autotrader.on_signal(signal, candles)
    except Exception as e:
        logger.error(f"autotrade on_signal error: {e}")
    await broadcast({"type": "signal", "data": _clean(signal)})


def _clean(d: Dict) -> Dict:
    d = dict(d)
    d.pop("_id", None)
    return d


async def update_performance(signal: Dict, opened=False, result=None):
    symbol = signal["symbol"]
    perf = await app.mongodb.performance.find_one({"symbol": symbol}) or {
        "symbol": symbol, "total_signals": 0, "long_signals": 0, "short_signals": 0,
        "wins": 0, "losses": 0, "breakevens": 0, "avg_crv": 0.0, "win_rate": 0.0,
        "by_strategy": {},
    }
    sid = signal.get("strategy_id", "unknown")
    bs = perf.get("by_strategy", {})
    st = bs.get(sid, {"total": 0, "wins": 0, "losses": 0, "breakevens": 0})
    if opened:
        perf["total_signals"] += 1
        if signal["type"] == "LONG":
            perf["long_signals"] += 1
        else:
            perf["short_signals"] += 1
        perf["last_signal"] = signal["timestamp"]
        n = perf["total_signals"]
        perf["avg_crv"] = round((perf.get("avg_crv", 0) * (n - 1) + signal.get("crv", 0)) / n, 3)
        st["total"] += 1
    if result:
        perf[{"win": "wins", "loss": "losses", "breakeven": "breakevens"}[result]] += 1
        st[{"win": "wins", "loss": "losses", "breakeven": "breakevens"}[result]] += 1
    decided = perf["wins"] + perf["losses"]
    perf["win_rate"] = round(perf["wins"] / decided * 100, 1) if decided else 0.0
    bs[sid] = st
    perf["by_strategy"] = bs
    perf.pop("_id", None)
    await app.mongodb.performance.update_one({"symbol": symbol}, {"$set": perf}, upsert=True)


async def evaluate_open_signals(symbol: str, price: float):
    """Mark today's signals win/loss based on which level price reaches first."""
    remaining = []
    for ev in open_signal_evals:
        if ev["symbol"] != symbol:
            remaining.append(ev)
            continue
        result = None
        if ev["type"] == "LONG":
            if price >= ev["tp1"]:
                result = "win"
            elif price <= ev["sl"]:
                result = "loss"
        else:
            if price <= ev["tp1"]:
                result = "win"
            elif price >= ev["sl"]:
                result = "loss"
        if result:
            await app.mongodb.signals.update_one({"id": ev["id"]}, {"$set": {"result": result, "status": "closed"}})
            await update_performance({"symbol": symbol, "strategy_id": ev["strategy_id"], "type": ev["type"]}, result=result)
        else:
            remaining.append(ev)
    open_signal_evals[:] = remaining


async def start_scanner():
    logger.info(f"Scanner started for {len(ALL_SYMBOLS)} instruments (every {POLL_INTERVAL}s)")
    scanner_running.set()
    while scanner_running.is_set():
        prices = {}
        for symbol in ALL_SYMBOLS:
            if not scanner_running.is_set():
                break
            try:
                if symbol in OTHER_YAHOO:
                    klines = await feed.fetch_commodity(OTHER_YAHOO[symbol], "1d")
                else:
                    klines = await feed.fetch(symbol, 5)
                if len(klines) < 2:
                    continue
                closed_candles = klines[:-1]
                forming = klines[-1]
                new_candle = False
                for candle in closed_candles[-3:]:
                    if scanner.add_closed_candle(symbol, candle):
                        new_candle = True
                scanner.forming[symbol] = forming
                price = forming["close"]
                prices[symbol] = price

                signals = scanner.analyze_symbol(symbol)
                if new_candle:
                    for sig in signals:
                        await process_signal(sig, scanner.candle_buffer.get(symbol, []))

                await evaluate_open_signals(symbol, price)
                await broadcast({"type": "candle", "symbol": symbol, "data": forming})
                states = scanner.rule_states.get(symbol)
                if states:
                    await broadcast({"type": "rule_states", "symbol": symbol, "data": states})
            except Exception as e:
                logger.error(f"Scan error for {symbol}: {e}")
            await asyncio.sleep(0.1)
        try:
            await autotrader.monitor(prices)
        except Exception as e:
            logger.error(f"autotrade monitor error: {e}")
        await asyncio.sleep(POLL_INTERVAL)


async def daily_reset_loop():
    """At Berlin midnight: aggregate the day into compact analytics, delete raw signals."""
    global _last_reset_date
    _last_reset_date = scanner.berlin_date()
    while True:
        await asyncio.sleep(60)
        today = scanner.berlin_date()
        if today != _last_reset_date:
            await perform_daily_reset(_last_reset_date)
            _last_reset_date = today


async def perform_daily_reset(prev_date: str):
    logger.info(f"Daily reset for {prev_date}")
    try:
        pipeline = [
            {"$match": {"trade_date": prev_date}},
            {"$group": {"_id": {"strategy": "$strategy_id", "type": "$type"},
                        "total": {"$sum": 1},
                        "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
                        "losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
                        "avg_crv": {"$avg": "$crv"}}},
        ]
        rows = await app.mongodb.signals.aggregate(pipeline).to_list(500)
        summary = {"date": prev_date, "generated_at": datetime.now(timezone.utc).isoformat(),
                   "by_strategy_type": [{"strategy": r["_id"]["strategy"], "type": r["_id"]["type"],
                                         "total": r["total"], "wins": r["wins"], "losses": r["losses"],
                                         "avg_crv": round(r.get("avg_crv") or 0, 2)} for r in rows]}
        total = sum(r["total"] for r in rows)
        summary["total_signals"] = total
        await app.mongodb.analytics_daily.update_one({"date": prev_date}, {"$set": summary}, upsert=True)
        # trade stats aggregate
        tstats = await app.mongodb.auto_trades.aggregate([
            {"$match": {"trade_date": prev_date, "status": "closed"}},
            {"$group": {"_id": None, "trades": {"$sum": 1},
                        "pnl": {"$sum": "$realized_pnl"},
                        "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}}}}],
        ).to_list(1)
        if tstats:
            ts = tstats[0]
            await app.mongodb.trade_stats.update_one({"date": prev_date}, {"$set": {
                "date": prev_date, "trades": ts["trades"], "pnl": round(ts.get("pnl") or 0, 4),
                "wins": ts["wins"]}}, upsert=True)
        # delete raw signals + closed trades from previous days (keep DB small)
        await app.mongodb.signals.delete_many({"trade_date": {"$ne": scanner.berlin_date()}})
        await app.mongodb.auto_trades.delete_many({"status": "closed",
                                                   "trade_date": {"$ne": scanner.berlin_date()}})
        open_signal_evals.clear()
        await broadcast({"type": "daily_reset", "date": prev_date})
    except Exception as e:
        logger.error(f"daily reset error: {e}")


async def broadcast(message: Dict):
    dead = []
    for c in websocket_clients:
        try:
            await c.send_json(message)
        except Exception:
            dead.append(c)
    for c in dead:
        if c in websocket_clients:
            websocket_clients.remove(c)


@app.websocket("/api/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    websocket_clients.append(websocket)
    try:
        await websocket.send_json({"type": "connected"})
        # send current rule states snapshot
        for sym, states in scanner.rule_states.items():
            await websocket.send_json({"type": "rule_states", "symbol": sym, "data": states})
        while True:
            try:
                await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                await websocket.send_json({"type": "ping"})
    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        if websocket in websocket_clients:
            websocket_clients.remove(websocket)


# ---------------- REST ----------------
@app.get("/")
async def root():
    return {"app": "Crypto Scalping Scanner", "status": "running"}


@app.get("/api/health")
async def health_check():
    return {"status": "alive"}


@app.get("/api/debug/status")
async def debug_status():
    return {"data_feed": feed.status, "session_active": scanner.is_trading_session(),
            "enabled_strategies": scanner.enabled_strategies(),
            "coins": [scanner.debug_snapshot(s) for s in ALL_SYMBOLS]}


@app.get("/api/coins")
async def get_coins():
    return {"coins": TOP_10_COINS,
            "groups": [{"name": "TOP 10 COINS", "symbols": TOP_10_COINS},
                       {"name": "OTHER", "symbols": [{"symbol": i["symbol"], "name": i["name"]} for i in OTHER_INSTRUMENTS]}]}


@app.get("/api/klines/{symbol}")
async def get_klines(symbol: str, limit: int = 200):
    """Historical candles for the chart (fixes empty/black chart)."""
    try:
        if symbol in OTHER_YAHOO:
            candles = await feed.fetch_commodity(OTHER_YAHOO[symbol], "1d")
        else:
            candles = await feed.fetch(symbol, limit)
        return {"symbol": symbol, "candles": candles[-limit:]}
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))


@app.get("/api/signals")
async def get_signals(limit: int = 50, strategy_id: str = None):
    q = {"trade_date": scanner.berlin_date()}
    if strategy_id:
        q["strategy_id"] = strategy_id
    signals = await app.mongodb.signals.find(q).sort("timestamp", -1).limit(limit).to_list(limit)
    return {"signals": [_clean(s) for s in signals]}


@app.get("/api/rule-states")
async def get_rule_states(symbol: str = None):
    if symbol:
        return {"symbol": symbol, "states": scanner.rule_states.get(symbol, {})}
    return {"states": scanner.rule_states}


@app.get("/api/performance")
async def get_performance():
    perf = await app.mongodb.performance.find().to_list(100)
    perf = [_clean(p) for p in perf]
    perf.sort(key=lambda x: x.get("total_signals", 0), reverse=True)
    return {"performance": perf}


@app.get("/api/analytics/daily")
async def get_daily_analytics(days: int = 30):
    rows = await app.mongodb.analytics_daily.find().sort("date", -1).limit(days).to_list(days)
    trades = await app.mongodb.trade_stats.find().sort("date", -1).limit(days).to_list(days)
    return {"daily": [_clean(r) for r in rows], "trade_stats": [_clean(t) for t in trades]}


async def rebuild_performance():
    """Recompute the cumulative performance collection from remaining signals."""
    await app.mongodb.performance.delete_many({})
    signals = await app.mongodb.signals.find({}).to_list(200000)
    perf_map: Dict[str, Dict] = {}
    for s in signals:
        if s.get("signal_class") == "PRE_SIGNAL":
            continue
        symbol = s.get("symbol")
        if not symbol:
            continue
        p = perf_map.setdefault(symbol, {
            "symbol": symbol, "total_signals": 0, "long_signals": 0, "short_signals": 0,
            "wins": 0, "losses": 0, "breakevens": 0, "avg_crv": 0.0, "win_rate": 0.0,
            "by_strategy": {}, "_crv_sum": 0.0,
        })
        p["total_signals"] += 1
        if s.get("type") == "LONG":
            p["long_signals"] += 1
        else:
            p["short_signals"] += 1
        p["_crv_sum"] += s.get("crv", 0) or 0
        sid = s.get("strategy_id", "unknown")
        st = p["by_strategy"].setdefault(sid, {"total": 0, "wins": 0, "losses": 0, "breakevens": 0})
        st["total"] += 1
        res = s.get("result")
        if res in ("win", "loss", "breakeven"):
            key = {"win": "wins", "loss": "losses", "breakeven": "breakevens"}[res]
            p[key] += 1
            st[key] += 1
    for symbol, p in perf_map.items():
        n = p["total_signals"]
        p["avg_crv"] = round(p.pop("_crv_sum", 0) / n, 3) if n else 0.0
        decided = p["wins"] + p["losses"]
        p["win_rate"] = round(p["wins"] / decided * 100, 1) if decided else 0.0
        await app.mongodb.performance.insert_one(p)


CLEAR_DELTAS = {"hour": timedelta(hours=1), "24h": timedelta(days=1),
                "7d": timedelta(days=7), "4w": timedelta(weeks=4)}


@app.post("/api/analytics/clear")
async def clear_analytics(body: Dict, _: bool = Depends(require_admin)):
    """Delete analysis data (signals, performance, daily analytics, trades) for a time range.
    range: 'hour' | '24h' | '7d' | '4w' | 'all'."""
    rng = (body.get("range") or "all").lower()
    deleted: Dict[str, int] = {}

    if rng == "all":
        for coll in ["signals", "performance", "analytics_daily", "trade_stats", "auto_trades"]:
            r = await app.mongodb[coll].delete_many({})
            deleted[coll] = r.deleted_count
        open_signal_evals.clear()
        for sym in list(scanner.rule_states.keys()):
            scanner.rule_states[sym] = {}
        await broadcast({"type": "analytics_cleared", "range": rng})
        return {"status": "success", "range": rng, "deleted": deleted}

    if rng not in CLEAR_DELTAS:
        raise HTTPException(status_code=400, detail="Ungültiger Zeitraum")

    now = datetime.now(timezone.utc)
    cutoff = now - CLEAR_DELTAS[rng]
    cutoff_iso = cutoff.isoformat()
    cutoff_date = cutoff.astimezone(BERLIN).strftime("%Y-%m-%d")

    r = await app.mongodb.signals.delete_many({"timestamp": {"$gte": cutoff_iso}})
    deleted["signals"] = r.deleted_count
    r = await app.mongodb.auto_trades.delete_many({"opened_at": {"$gte": cutoff_iso}})
    deleted["auto_trades"] = r.deleted_count
    r = await app.mongodb.analytics_daily.delete_many({"date": {"$gte": cutoff_date}})
    deleted["analytics_daily"] = r.deleted_count
    r = await app.mongodb.trade_stats.delete_many({"date": {"$gte": cutoff_date}})
    deleted["trade_stats"] = r.deleted_count

    await rebuild_performance()
    # drop in-memory evals whose signal was removed
    remaining_ids = {s["id"] for s in await app.mongodb.signals.find({}, {"id": 1}).to_list(200000)}
    open_signal_evals[:] = [ev for ev in open_signal_evals if ev["id"] in remaining_ids]
    await broadcast({"type": "analytics_cleared", "range": rng})
    return {"status": "success", "range": rng, "deleted": deleted}


@app.get("/api/analytics/time-based/{symbol}")
async def get_time_based(symbol: str):
    pipeline = [
        {"$match": {"symbol": symbol}},
        {"$group": {"_id": {"hour": "$hour", "weekday": "$weekday"},
                    "total": {"$sum": 1},
                    "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
                    "losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
                    "avg_crv": {"$avg": "$crv"}}},
        {"$sort": {"total": -1}},
    ]
    results = await app.mongodb.signals.aggregate(pipeline).to_list(1000)
    weekdays = ["Mo", "Di", "Mi", "Do", "Fr", "Sa", "So"]
    stats = []
    for r in results:
        total = r["total"]
        wins = r.get("wins", 0)
        stats.append({"hour": r["_id"]["hour"], "weekday": weekdays[r["_id"]["weekday"]],
                      "total_signals": total, "wins": wins, "losses": r.get("losses", 0),
                      "win_rate": round(wins / total * 100, 1) if total else 0,
                      "avg_crv": round(r.get("avg_crv") or 0, 2)})
    return {"symbol": symbol, "time_analytics": stats,
            "best_hours": sorted(stats, key=lambda x: x["win_rate"], reverse=True)[:5]}


@app.get("/api/settings")
async def get_settings():
    return scanner.settings


@app.post("/api/settings")
async def update_settings(settings: Dict, _: bool = Depends(require_admin)):
    scanner.update_settings(settings)
    await app.mongodb.settings.update_one({"_id": "scanner_settings"},
                                          {"$set": scanner.settings}, upsert=True)
    return {"status": "success", "settings": scanner.settings}


@app.get("/api/session/status")
async def session_status():
    now = scanner.berlin_now()
    return {"is_active": scanner.is_trading_session(),
            "current_session": scanner.get_current_session(),
            "custom_sessions": scanner.settings.get("custom_sessions", []),
            "pre_signal_enabled": scanner.settings.get("pre_signal_enabled", True),
            "berlin_time": now.strftime("%H:%M:%S"), "berlin_date": scanner.berlin_date()}


@app.get("/api/strategies")
async def get_strategies():
    out = []
    deleted = set(scanner.settings.get("deleted_strategies", []))
    for meta in strategy_registry.list_all():
        if meta["id"] in deleted:
            continue
        strat = strategy_registry.get(meta["id"])
        item = {**meta, "current_params": strat.get_params(scanner.settings)}
        if getattr(strat, "IS_CUSTOM", False):
            item["definition"] = strat.definition
        out.append(item)
    return {"strategies": out,
            "active": scanner.settings.get("active_strategy", "scalping_4_rules"),
            "enabled": scanner.enabled_strategies(),
            "signals_enabled": scanner.settings.get("strategy_signals_enabled", {})}


# ---- custom strategy CRUD ----
@app.post("/api/strategies/custom")
async def create_custom_strategy(definition: Dict, _: bool = Depends(require_admin)):
    sid = definition.get("id") or f"custom_{uuid.uuid4().hex[:8]}"
    definition["id"] = sid
    definition.setdefault("timeframe", "1m")
    await app.mongodb.custom_strategies.update_one({"id": sid}, {"$set": definition}, upsert=True)
    strategy_registry.upsert_custom(definition)
    # auto-enable in tabs
    enabled = scanner.settings.get("enabled_strategies", [])
    if sid not in enabled:
        enabled.append(sid)
        scanner.update_settings({"enabled_strategies": enabled})
        await app.mongodb.settings.update_one({"_id": "scanner_settings"}, {"$set": scanner.settings}, upsert=True)
    return {"status": "success", "id": sid, "definition": definition}


@app.delete("/api/strategies/custom/{strategy_id}")
async def delete_custom_strategy(strategy_id: str, _: bool = Depends(require_admin)):
    await app.mongodb.custom_strategies.delete_one({"id": strategy_id})
    strategy_registry.remove_custom(strategy_id)
    enabled = [s for s in scanner.settings.get("enabled_strategies", []) if s != strategy_id]
    scanner.update_settings({"enabled_strategies": enabled})
    await app.mongodb.settings.update_one({"_id": "scanner_settings"}, {"$set": scanner.settings}, upsert=True)
    return {"status": "success"}


@app.delete("/api/strategies/{strategy_id}")
async def delete_strategy(strategy_id: str, _: bool = Depends(require_admin)):
    """Delete ANY strategy permanently. Custom => removed from DB.
    Built-in (predefined) => added to deleted_strategies so it never shows/runs."""
    is_custom = strategy_id in strategy_registry._custom_ids
    if is_custom:
        await app.mongodb.custom_strategies.delete_one({"id": strategy_id})
        strategy_registry.remove_custom(strategy_id)
    else:
        deleted = list(scanner.settings.get("deleted_strategies", []))
        if strategy_id not in deleted:
            deleted.append(strategy_id)
        scanner.update_settings({"deleted_strategies": deleted})
    enabled = [s for s in scanner.settings.get("enabled_strategies", []) if s != strategy_id]
    scanner.update_settings({"enabled_strategies": enabled})
    await app.mongodb.settings.update_one({"_id": "scanner_settings"}, {"$set": scanner.settings}, upsert=True)
    return {"status": "success", "id": strategy_id, "was_custom": is_custom}


@app.post("/api/strategies/restore-defaults")
async def restore_default_strategies(_: bool = Depends(require_admin)):
    """Un-delete all previously deleted built-in strategies."""
    scanner.update_settings({"deleted_strategies": []})
    await app.mongodb.settings.update_one({"_id": "scanner_settings"}, {"$set": scanner.settings}, upsert=True)
    return {"status": "success", "restored": True}


@app.get("/api/strategies/builder-options")
async def builder_options():
    from strategies.custom_strategy import INDICATORS, OPERATORS
    return {"indicators": INDICATORS, "operators": OPERATORS}


# ---- autotrade ----
@app.get("/api/autotrade/config")
async def get_autotrade_config():
    return {"config": autotrader.config, "defaults": DEFAULT_COIN_CFG,
            "bitunix_configured": trade_client.configured()}


@app.post("/api/autotrade/config")
async def set_autotrade_config(config: Dict, _: bool = Depends(require_admin)):
    if "mode" not in config:
        config["mode"] = autotrader.config.get("mode", "paper")
    config.setdefault("coins", autotrader.config.get("coins", {}))
    autotrader.set_config(config)
    await app.mongodb.settings.update_one({"_id": "autotrade_config"},
                                          {"$set": {"mode": config["mode"], "coins": config["coins"]}},
                                          upsert=True)
    return {"status": "success", "config": autotrader.config}


@app.post("/api/autotrade/coin/{symbol}")
async def set_coin_config(symbol: str, cfg: Dict, _: bool = Depends(require_admin)):
    coins = dict(autotrader.config.get("coins", {}))
    merged = dict(DEFAULT_COIN_CFG)
    merged.update(coins.get(symbol, {}))
    merged.update(cfg)
    coins[symbol] = merged
    new_cfg = {"mode": autotrader.config.get("mode", "paper"), "coins": coins}
    autotrader.set_config(new_cfg)
    await app.mongodb.settings.update_one({"_id": "autotrade_config"},
                                          {"$set": {"mode": new_cfg["mode"], "coins": coins}}, upsert=True)
    return {"status": "success", "coin": symbol, "config": merged}


@app.get("/api/autotrade/trades")
async def get_trades(status: str = None, limit: int = 50):
    q = {}
    if status:
        q["status"] = status
    trades = await app.mongodb.auto_trades.find(q).sort("opened_at", -1).limit(limit).to_list(limit)
    return {"trades": [_clean(t) for t in trades]}


@app.post("/api/autotrade/close/{trade_id}")
async def close_trade(trade_id: str, _: bool = Depends(require_admin)):
    t = await app.mongodb.auto_trades.find_one({"id": trade_id})
    if not t:
        raise HTTPException(status_code=404, detail="Trade not found")
    price = scanner.current_price(t["symbol"]) or t["entry"]
    res = await autotrader.manual_close(trade_id, price)
    return {"status": "success", "result": res}


@app.get("/api/autotrade/balance")
async def get_balance():
    open_ct = await app.mongodb.auto_trades.count_documents({"status": "open"})
    closed = await app.mongodb.auto_trades.find({"status": "closed"}).to_list(1000)
    pnl = round(sum(t.get("realized_pnl", 0) for t in closed), 4)
    mode = autotrader.config.get("mode", "paper")
    result = {"mode": mode, "realized_pnl": pnl, "open_trades": open_ct,
              "closed_trades": len(closed), "bitunix_configured": trade_client.configured()}
    if mode == "live" and trade_client.configured():
        try:
            bal = await trade_client.get_balance()
            data = bal.get("data") if isinstance(bal, dict) else None
            if isinstance(data, list) and data:
                data = data[0]
            if isinstance(data, dict):
                result["available"] = data.get("available") or data.get("availableBalance")
                result["margin_balance"] = data.get("marginBalance") or data.get("equity")
            result["bitunix_code"] = bal.get("code") if isinstance(bal, dict) else None
        except Exception as e:
            result["bitunix_error"] = str(e)[:120]
    return result


@app.post("/api/telegram/test")
async def test_telegram(_: bool = Depends(require_admin)):
    if not telegram.bot:
        raise HTTPException(status_code=400, detail="Telegram not configured")
    if await telegram.send_test_message():
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="Failed")


# ---------------- admin control toggles (Stop All Trades / Stop All Signals) ----------------
async def _persist_control_state():
    await app.mongodb.settings.update_one(
        {"_id": "control_state"},
        {"$set": {"trades_paused": control_state["trades_paused"],
                  "signals_paused": control_state["signals_paused"]}},
        upsert=True,
    )


async def _close_all_open_auto_trades() -> int:
    """Close every bot-opened auto-trade currently 'open' in our DB.
    Manual/user-placed positions on the Bitunix account are NOT touched
    because they are not tracked in the auto_trades collection."""
    closed = 0
    async for t in app.mongodb.auto_trades.find({"status": "open"}):
        price = scanner.current_price(t["symbol"]) or t.get("entry")
        try:
            res = await autotrader.manual_close(t["id"], price)
            if res:
                closed += 1
        except Exception as e:
            logger.error(f"auto-close {t.get('id')} failed: {e}")
    return closed


@app.get("/api/control/state")
async def get_control_state():
    return {"trades_paused": control_state["trades_paused"],
            "signals_paused": control_state["signals_paused"]}


@app.post("/api/control/stop-trades")
async def toggle_stop_trades(_: bool = Depends(require_admin)):
    """Toggle 'Stop All Trades'. When switched ON, closes every bot-opened
    auto-trade in our DB and blocks the bot from opening new ones.
    Manual trades placed by the user directly on Bitunix are not affected.
    When switched OFF, the bot resumes with the previous per-coin config."""
    new_val = not control_state["trades_paused"]
    control_state["trades_paused"] = new_val
    closed = 0
    if new_val:
        closed = await _close_all_open_auto_trades()
    await _persist_control_state()
    await broadcast({"type": "control_state", "data": dict(control_state)})
    return {"status": "success", "trades_paused": new_val, "closed_trades": closed}


@app.post("/api/control/stop-signals")
async def toggle_stop_signals(_: bool = Depends(require_admin)):
    """Toggle 'Stop All Signals'. When ON, signals are not emitted, saved or
    broadcast. When OFF, signal emission resumes exactly with the previously
    enabled strategies (no config touched)."""
    new_val = not control_state["signals_paused"]
    control_state["signals_paused"] = new_val
    await _persist_control_state()
    await broadcast({"type": "control_state", "data": dict(control_state)})
    return {"status": "success", "signals_paused": new_val}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
