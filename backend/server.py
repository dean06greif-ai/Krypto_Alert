from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from contextlib import asynccontextmanager
from dotenv import load_dotenv
import os
import logging
import asyncio
import uuid
from typing import List, Dict
from datetime import datetime, timezone

load_dotenv()

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


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting Crypto Scalping Scanner...")
    app.mongodb_client = AsyncIOMotorClient(os.getenv("MONGO_URL"))
    app.mongodb = app.mongodb_client[os.getenv("DB_NAME", "crypto_scanner")]
    autotrader.set_db(app.mongodb)
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


# ---------------- signal processing ----------------
async def process_signal(signal: Dict, candles: List[Dict]):
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
async def update_settings(settings: Dict):
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
    for meta in strategy_registry.list_all():
        strat = strategy_registry.get(meta["id"])
        out.append({**meta, "current_params": strat.get_params(scanner.settings)})
    return {"strategies": out,
            "active": scanner.settings.get("active_strategy", "scalping_4_rules"),
            "enabled": scanner.enabled_strategies(),
            "signals_enabled": scanner.settings.get("strategy_signals_enabled", {})}


# ---- custom strategy CRUD ----
@app.post("/api/strategies/custom")
async def create_custom_strategy(definition: Dict):
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
async def delete_custom_strategy(strategy_id: str):
    await app.mongodb.custom_strategies.delete_one({"id": strategy_id})
    strategy_registry.remove_custom(strategy_id)
    enabled = [s for s in scanner.settings.get("enabled_strategies", []) if s != strategy_id]
    scanner.update_settings({"enabled_strategies": enabled})
    await app.mongodb.settings.update_one({"_id": "scanner_settings"}, {"$set": scanner.settings}, upsert=True)
    return {"status": "success"}


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
async def set_autotrade_config(config: Dict):
    if "mode" not in config:
        config["mode"] = autotrader.config.get("mode", "paper")
    config.setdefault("coins", autotrader.config.get("coins", {}))
    autotrader.set_config(config)
    await app.mongodb.settings.update_one({"_id": "autotrade_config"},
                                          {"$set": {"mode": config["mode"], "coins": config["coins"]}},
                                          upsert=True)
    return {"status": "success", "config": autotrader.config}


@app.post("/api/autotrade/coin/{symbol}")
async def set_coin_config(symbol: str, cfg: Dict):
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
async def close_trade(trade_id: str):
    t = await app.mongodb.auto_trades.find_one({"id": trade_id})
    if not t:
        raise HTTPException(status_code=404, detail="Trade not found")
    price = scanner.current_price(t["symbol"]) or t["entry"]
    res = await autotrader.manual_close(trade_id, price)
    return {"status": "success", "result": res}


@app.get("/api/autotrade/balance")
async def get_balance():
    if autotrader.config.get("mode") == "live" and trade_client.configured():
        return await trade_client.get_balance()
    trades = await app.mongodb.auto_trades.find({"status": "closed"}).to_list(1000)
    pnl = round(sum(t.get("realized_pnl", 0) for t in trades), 4)
    open_ct = await app.mongodb.auto_trades.count_documents({"status": "open"})
    return {"mode": "paper", "realized_pnl": pnl, "open_trades": open_ct, "closed_trades": len(trades)}


@app.post("/api/telegram/test")
async def test_telegram():
    if not telegram.bot:
        raise HTTPException(status_code=400, detail="Telegram not configured")
    if await telegram.send_test_message():
        return {"status": "success"}
    raise HTTPException(status_code=500, detail="Failed")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
