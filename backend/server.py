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

# In-memory cache of (strategy_id, symbol) -> enabled. Missing entry defaults
# to True so per-strategy behaviour is unchanged for any combo that has not
# been explicitly toggled off. Persisted in `strategy_coin_toggles`.
strategy_coin_toggles: Dict[tuple, bool] = {}


def toggle_enabled(strategy_id: str, symbol: str) -> bool:
    """Return whether (strategy, coin) auto-trade is enabled. Default True."""
    if not strategy_id or not symbol:
        return True
    return strategy_coin_toggles.get((strategy_id, symbol), True)


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
        cfg = {"mode": os.getenv("TRADING_MODE", "paper"), "coins": {}, "strategy_overrides": {}}
        await app.mongodb.settings.insert_one({"_id": "autotrade_config", **cfg})
        autotrader.set_config(cfg)

    # ---- Load persisted capital allocation (live/paper getrennt) ----
    cap_doc = await app.mongodb.settings.find_one({"_id": "capital_allocation"})
    if cap_doc:
        cap_doc.pop("_id", None)
        autotrader.config["capital_allocation"] = cap_doc

    # ---- Load strategy_coin_configs from dedicated collection ----
    # Without this, the per-strategy-per-coin paper/live mode lives only in the
    # DB and the in-memory autotrader never sees it -> it falls back to the
    # global/strategy mode and can fire REAL live orders even though the UI
    # shows the pair as "paper". Loading them here fixes that.
    try:
        scc_docs = await app.mongodb.strategy_coin_configs.find().to_list(2000)
        scc_map = {}
        for d in scc_docs:
            key = d.get("_id")
            if key:
                scc_map[key] = d.get("config", {})
        if scc_map:
            autotrader.config.setdefault("strategy_coin_configs", {}).update(scc_map)
            logger.info(f"Loaded {len(scc_map)} strategy_coin_configs from DB")
    except Exception as e:
        logger.warning(f"Loading strategy_coin_configs failed: {e}")

    # load admin control toggles (stop trades / stop signals)
    ctrl = await app.mongodb.settings.find_one({"_id": "control_state"})
    if ctrl:
        control_state["trades_paused"] = bool(ctrl.get("trades_paused", False))
        control_state["signals_paused"] = bool(ctrl.get("signals_paused", False))
    else:
        await app.mongodb.settings.insert_one({"_id": "control_state", **control_state})

    # ---- strategy_coin_toggles: index + migration + in-memory cache ----
    try:
        await app.mongodb.strategy_coin_toggles.create_index(
            [("strategy_id", 1), ("symbol", 1)], unique=True
        )
    except Exception as e:
        logger.warning(f"strategy_coin_toggles index setup: {e}")

    # Migration: seed enabled=True for every (existing strategy, symbol) combo
    # that has no record yet. Missing rows already default to enabled=True via
    # `toggle_enabled`, so this is idempotent and non-destructive.
    all_strategy_ids = [m["id"] for m in strategy_registry.list_all()]
    deleted_strats = set(scanner.settings.get("deleted_strategies", []))
    all_strategy_ids = [sid for sid in all_strategy_ids if sid not in deleted_strats]
    now_iso = datetime.now(timezone.utc).isoformat()
    if all_strategy_ids:
        migration_ops = []
        for sid in all_strategy_ids:
            for sym in ALL_SYMBOLS:
                migration_ops.append({
                    "filter": {"strategy_id": sid, "symbol": sym},
                    "update": {"$setOnInsert": {
                        "strategy_id": sid, "symbol": sym,
                        "enabled": True, "updated_at": now_iso,
                    }},
                })
        for op in migration_ops:
            try:
                await app.mongodb.strategy_coin_toggles.update_one(
                    op["filter"], op["update"], upsert=True
                )
            except Exception as e:
                logger.debug(f"toggle migration skip {op['filter']}: {e}")

    # Load toggles into cache
    async for row in app.mongodb.strategy_coin_toggles.find({}):
        strategy_coin_toggles[(row.get("strategy_id"), row.get("symbol"))] = \
            bool(row.get("enabled", True))
    logger.info(f"Loaded {len(strategy_coin_toggles)} strategy_coin_toggles")

    logger.info("Probing market data sources...")
    await feed.probe("BTCUSDT")
    # Bei höheren Timeframes mehr 1m-Historie laden, damit die Strategien
    # direkt nach dem Start genug aggregierte Kerzen haben.
    need = scanner.buffer_limit()
    if need > 900:
        from services import backtester as _bt
        import aiohttp as _aiohttp
        days_needed = min(14, need // 1440 + 1)
        async with _aiohttp.ClientSession() as _session:
            for symbol in TOP_10_COINS:
                try:
                    hist = await _bt.fetch_history(_session, symbol, days_needed)
                    scanner.bootstrap(symbol, hist[:-1] if len(hist) > 1 else hist)
                except Exception as e:
                    logger.error(f"Extended bootstrap failed for {symbol}: {e}")
                await asyncio.sleep(0.15)
    else:
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

    # BUGFIX (win-rate): re-hydrate the in-memory open_signal_evals from today's
    # still-open signals so evaluate_open_signals() can mark them as win/loss
    # after a restart. Without this any signal that emitted before the restart
    # never got its `result` filled in.
    try:
        today = scanner.berlin_date()
        cursor = app.mongodb.signals.find({
            "trade_date": today,
            "signal_class": {"$ne": "PRE_SIGNAL"},
            "$or": [{"result": {"$exists": False}}, {"result": None}],
        })
        rehydrated = 0
        async for s in cursor:
            if not s.get("tp1") or not s.get("sl"):
                continue
            open_signal_evals.append({
                "id": s.get("id"),
                "symbol": s.get("symbol"),
                "type": s.get("type"),
                "tp1": s.get("tp1"),
                "sl": s.get("sl"),
                "strategy_id": s.get("strategy_id", "unknown"),
            })
            rehydrated += 1
        if rehydrated:
            logger.info(f"Re-hydrated {rehydrated} open signal evaluations from DB")
    except Exception as e:
        logger.warning(f"open_signal_evals rehydration failed: {e}")

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
    payload = {"sub": "admin", "exp": datetime.now(timezone.utc) + timedelta(days=1)}
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


async def process_signal(signal: Dict, candles: List[Dict]):
    # Global admin kill-switch for signals -> completely suppress emission
    if control_state.get("signals_paused"):
        return
    # Per-(coin, strategy) toggle: if this combination is disabled, skip
    # BOTH signal emission and auto-trade for the pair. Other coins/strategies
    # remain unaffected.
    if not toggle_enabled(signal.get("strategy_id"), signal.get("symbol")):
        return

    strategy_id = signal.get("strategy_id")
    symbol = signal["symbol"]

    # Per-Coin-pro-Strategie Config (NEU) — VOR insert prüfen
    _coin_strat_key = f"{strategy_id}_{symbol}"
    coin_strat_cfg = autotrader.config.get("strategy_coin_configs", {}).get(_coin_strat_key)
    if coin_strat_cfg is None:
        _doc = await app.mongodb.strategy_coin_configs.find_one({"_id": _coin_strat_key})
        coin_strat_cfg = _doc.get("config", {}) if _doc else {}
        # Keep the in-memory cache in sync so on_signal()/effective_mode()
        # see the SAME paper/live mode on the next call. Prevents live orders
        # slipping through because the DB config wasn't cached yet.
        if coin_strat_cfg:
            autotrader.config.setdefault("strategy_coin_configs", {})[_coin_strat_key] = coin_strat_cfg

    # Wenn AUS → komplett überspringen, nichts speichern
    if coin_strat_cfg.get("mode", "off") == "off":
        return

    signals_enabled_for_strategy = coin_strat_cfg.get("signals_enabled", True)

    signal["id"] = str(uuid.uuid4())
    notify = scanner.is_notify_enabled(symbol)
    signal["notify"] = notify
    await app.mongodb.signals.insert_one(dict(signal))

    # BUGFIX (win-rate): track this signal in-memory so evaluate_open_signals()
    # can later mark it as win/loss based on price hitting TP1 or SL. Without
    # this the wins/losses counters (and thus win_rate) stayed 0 forever.
    if signal.get("signal_class") != "PRE_SIGNAL" and signal.get("tp1") and signal.get("sl"):
        open_signal_evals.append({
            "id": signal["id"],
            "symbol": symbol,
            "type": signal["type"],
            "tp1": signal["tp1"],
            "sl": signal["sl"],
            "strategy_id": signal.get("strategy_id", "unknown"),
        })

    # FIX 1: Telegram-Benachrichtigung senden (wenn aktiviert)
    if notify and signals_enabled_for_strategy:
        try:
            # tp1_close_percent für die Telegram-Nachricht hinzufügen
            coin_cfg = autotrader.coin_cfg(symbol)
            signal["tp1_close_percent"] = coin_cfg.get("tp1_close_percent", 50)
            await telegram.send_signal(signal)
            logger.info(f"Telegram notification sent for {symbol} {signal['type']}")
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")

    # FIX 2: Auto-Trade ausführen (wenn Auto-Trading aktiviert ist)
    try:
        trade = await autotrader.on_signal(signal, candles)
        if trade:
            await update_performance(signal, opened=True)
            logger.info(f"Auto-trade opened for {symbol}: {trade['id']}")
    except Exception as e:
        logger.error(f"Auto-trade execution failed for {symbol}: {e}")

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
    """At Berlin midnight: aggregate the day into compact analytics.
    (Raw signals & closed trades are NOT deleted anymore – auf Nutzerwunsch.)"""
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
        # NOTE: Auto-Löschung deaktiviert – Signale und geschlossene Trades
        # bleiben dauerhaft in der DB erhalten (auf Nutzerwunsch).
        # Falls du die DB manuell aufräumen willst, nutze den POST /api/clear/{scope}
        # Endpoint (siehe CLEAR_DELTAS).
        # await app.mongodb.signals.delete_many({"trade_date": {"$ne": scanner.berlin_date()}})
        # await app.mongodb.auto_trades.delete_many({"status": "closed",
        #                                            "trade_date": {"$ne": scanner.berlin_date()}})
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
    from services import candle_cache as _cc
    return {"data_feed": feed.status, "session_active": scanner.is_trading_session(),
            "enabled_strategies": scanner.enabled_strategies(),
            "candle_cache": _cc.stats(),
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
    """Return per-symbol performance.

    BUGFIX (win-rate always 0%):
      The stored `performance` collection only got wins/losses via
      evaluate_open_signals() which relied on `open_signal_evals` – a list
      that historically was never populated. As a result wins/losses/win_rate
      never left 0 even though real auto-trades were being closed with a
      proper "win"/"loss"/"breakeven" result.

      We now derive wins/losses on the fly from the `auto_trades` collection
      (which IS the source of truth for closed trades) and merge them with
      the stored signal counters (total_signals, long_signals, short_signals).
      Any wins/losses that DO exist on stored performance docs (from signal-
      level evaluation going forward) are additionally combined.
    """
    stored = {}
    for p in await app.mongodb.performance.find().to_list(500):
        stored[p["symbol"]] = _clean(p)

    # Aggregate closed auto-trades → real win/loss numbers per symbol
    trade_pipeline = [
        {"$match": {"status": "closed", "result": {"$in": ["win", "loss", "breakeven"]}}},
        {"$group": {
            "_id": "$symbol",
            "trade_wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
            "trade_losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
            "trade_breakevens": {"$sum": {"$cond": [{"$eq": ["$result", "breakeven"]}, 1, 0]}},
        }},
    ]
    trade_rows = await app.mongodb.auto_trades.aggregate(trade_pipeline).to_list(500)

    result_map: Dict[str, Dict] = {}
    # start with everything we already have stored (keeps total/long/short counts)
    for symbol, p in stored.items():
        result_map[symbol] = {
            "symbol": symbol,
            "total_signals": p.get("total_signals", 0),
            "long_signals": p.get("long_signals", 0),
            "short_signals": p.get("short_signals", 0),
            "wins": p.get("wins", 0),
            "losses": p.get("losses", 0),
            "breakevens": p.get("breakevens", 0),
            "avg_crv": p.get("avg_crv", 0.0),
            "win_rate": p.get("win_rate", 0.0),
            "by_strategy": p.get("by_strategy", {}),
            "last_signal": p.get("last_signal"),
        }

    for tr in trade_rows:
        symbol = tr["_id"]
        p = result_map.setdefault(symbol, {
            "symbol": symbol, "total_signals": 0, "long_signals": 0, "short_signals": 0,
            "wins": 0, "losses": 0, "breakevens": 0, "avg_crv": 0.0, "win_rate": 0.0,
            "by_strategy": {},
        })
        # take the MAX between stored signal-level results and real trade results
        # (avoids double counting while making sure at least the trade outcome shows)
        p["wins"] = max(p.get("wins", 0), tr.get("trade_wins", 0))
        p["losses"] = max(p.get("losses", 0), tr.get("trade_losses", 0))
        p["breakevens"] = max(p.get("breakevens", 0), tr.get("trade_breakevens", 0))

    # recompute win_rate from the merged wins/losses
    for p in result_map.values():
        decided = p.get("wins", 0) + p.get("losses", 0)
        p["win_rate"] = round(p["wins"] / decided * 100, 1) if decided else 0.0

    perf = list(result_map.values())
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

CLEAR_SCOPES = {"all", "coin", "coin_strategy"}


async def reaggregate_daily_stats() -> Dict[str, int]:
    """Re-aggregate analytics_daily & trade_stats from the remaining raw data.
    Needed after coin/strategy-scoped deletes, because those aggregated day
    collections carry no symbol/strategy fields and must not be dropped blindly."""
    removed = {"analytics_daily": 0, "trade_stats": 0}

    daily_docs = await app.mongodb.analytics_daily.find({}, {"date": 1}).to_list(10000)
    for doc in daily_docs:
        date = doc.get("date")
        if not date:
            continue
        pipeline = [
            {"$match": {"trade_date": date}},
            {"$group": {"_id": {"strategy": "$strategy_id", "type": "$type"},
                        "total": {"$sum": 1},
                        "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
                        "losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
                        "avg_crv": {"$avg": "$crv"}}},
        ]
        rows = await app.mongodb.signals.aggregate(pipeline).to_list(500)
        if not rows:
            await app.mongodb.analytics_daily.delete_one({"date": date})
            removed["analytics_daily"] += 1
            continue
        summary = {"date": date, "generated_at": datetime.now(timezone.utc).isoformat(),
                   "by_strategy_type": [{"strategy": r["_id"]["strategy"], "type": r["_id"]["type"],
                                         "total": r["total"], "wins": r["wins"], "losses": r["losses"],
                                         "avg_crv": round(r.get("avg_crv") or 0, 2)} for r in rows],
                   "total_signals": sum(r["total"] for r in rows)}
        await app.mongodb.analytics_daily.update_one({"date": date}, {"$set": summary})

    tstat_docs = await app.mongodb.trade_stats.find({}, {"date": 1}).to_list(10000)
    for doc in tstat_docs:
        date = doc.get("date")
        if not date:
            continue
        tstats = await app.mongodb.auto_trades.aggregate([
            {"$match": {"trade_date": date, "status": "closed"}},
            {"$group": {"_id": None, "trades": {"$sum": 1},
                        "pnl": {"$sum": "$realized_pnl"},
                        "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}}}}],
        ).to_list(1)
        if not tstats or not tstats[0].get("trades"):
            await app.mongodb.trade_stats.delete_one({"date": date})
            removed["trade_stats"] += 1
            continue
        ts = tstats[0]
        await app.mongodb.trade_stats.update_one({"date": date}, {"$set": {
            "date": date, "trades": ts["trades"], "pnl": round(ts.get("pnl") or 0, 4),
            "wins": ts["wins"]}})
    return removed


@app.post("/api/analytics/clear")
async def clear_analytics(body: Dict, _: bool = Depends(require_admin)):
    """Delete analysis data (signals, performance, daily analytics, trades).
    range: 'hour' | '24h' | '7d' | '4w' | 'all'
    scope: 'all' (alles) | 'coin' (nur symbol) | 'coin_strategy' (symbol + strategy_id)"""
    rng = (body.get("range") or "all").lower()
    scope = (body.get("scope") or "all").lower()
    symbol = body.get("symbol")
    strategy_id = body.get("strategy_id")

    if scope not in CLEAR_SCOPES:
        raise HTTPException(status_code=400, detail="Ungültiger Scope")
    if scope in ("coin", "coin_strategy") and not symbol:
        raise HTTPException(status_code=400, detail="symbol erforderlich für Coin-Scope")
    if scope == "coin_strategy" and not strategy_id:
        raise HTTPException(status_code=400, detail="strategy_id erforderlich für Strategie-Scope")
    if rng != "all" and rng not in CLEAR_DELTAS:
        raise HTTPException(status_code=400, detail="Ungültiger Zeitraum")

    scope_filter: Dict = {}
    if scope == "coin":
        scope_filter = {"symbol": symbol}
    elif scope == "coin_strategy":
        scope_filter = {"symbol": symbol, "strategy_id": strategy_id}

    deleted: Dict[str, int] = {}

    # Fast path: full wipe over everything (previous behaviour)
    if rng == "all" and scope == "all":
        for coll in ["signals", "performance", "analytics_daily", "trade_stats", "auto_trades"]:
            r = await app.mongodb[coll].delete_many({})
            deleted[coll] = r.deleted_count
        open_signal_evals.clear()
        for sym in list(scanner.rule_states.keys()):
            scanner.rule_states[sym] = {}
        await broadcast({"type": "analytics_cleared", "range": rng, "scope": scope})
        return {"status": "success", "range": rng, "scope": scope, "deleted": deleted}

    if rng == "all":
        sig_filter: Dict = dict(scope_filter)
        trade_filter: Dict = dict(scope_filter)
        cutoff = None
    else:
        cutoff = datetime.now(timezone.utc) - CLEAR_DELTAS[rng]
        cutoff_iso = cutoff.isoformat()
        sig_filter = {"timestamp": {"$gte": cutoff_iso}, **scope_filter}
        trade_filter = {"opened_at": {"$gte": cutoff_iso}, **scope_filter}

    r = await app.mongodb.signals.delete_many(sig_filter)
    deleted["signals"] = r.deleted_count
    r = await app.mongodb.auto_trades.delete_many(trade_filter)
    deleted["auto_trades"] = r.deleted_count

    if scope == "all":
        # time-scoped full delete: drop aggregated day docs directly (previous behaviour)
        cutoff_date = cutoff.astimezone(BERLIN).strftime("%Y-%m-%d")
        r = await app.mongodb.analytics_daily.delete_many({"date": {"$gte": cutoff_date}})
        deleted["analytics_daily"] = r.deleted_count
        r = await app.mongodb.trade_stats.delete_many({"date": {"$gte": cutoff_date}})
        deleted["trade_stats"] = r.deleted_count
    else:
        # coin/strategy scope: day aggregates have no coin/strategy fields
        # -> re-aggregate from remaining signals/auto_trades instead of deleting blindly
        removed = await reaggregate_daily_stats()
        deleted["analytics_daily"] = removed["analytics_daily"]
        deleted["trade_stats"] = removed["trade_stats"]

    await rebuild_performance()
    # drop in-memory evals whose signal was removed
    remaining_ids = {s["id"] for s in await app.mongodb.signals.find({}, {"id": 1}).to_list(200000)}
    open_signal_evals[:] = [ev for ev in open_signal_evals if ev["id"] in remaining_ids]
    await broadcast({"type": "analytics_cleared", "range": rng, "scope": scope})
    return {"status": "success", "range": rng, "scope": scope, "deleted": deleted}


# ---------------- KI-Analyse (GPT-4o via Emergent Universal Key) ----------------
async def _aggregate_ai_stats(strategy_id: str = None) -> Dict:
    """Aggregate signals, trades and strategy definitions for the AI review."""
    q = {"strategy_id": strategy_id} if strategy_id else {}
    signals = await app.mongodb.signals.find(q).sort("timestamp", -1).limit(5000).to_list(5000)
    trades = await app.mongodb.auto_trades.find({"status": "closed"}).sort("closed_at", -1).limit(500).to_list(500)

    total = len(signals)
    wins = sum(1 for s in signals if s.get("result") == "win")
    losses = sum(1 for s in signals if s.get("result") == "loss")
    decided = wins + losses
    win_rate = round(wins / decided * 100, 1) if decided else 0.0
    avg_crv = round(sum((s.get("crv") or 0) for s in signals) / total, 2) if total else 0.0

    trade_wins = sum(1 for t in trades if t.get("result") == "win")
    trade_losses = sum(1 for t in trades if t.get("result") == "loss")
    tdec = trade_wins + trade_losses
    trade_win_rate = round(trade_wins / tdec * 100, 1) if tdec else 0.0
    total_pnl = round(sum((t.get("realized_pnl") or 0) for t in trades), 2)

    # Max Drawdown aus kumulierter PnL-Kurve
    sorted_trades = sorted(trades, key=lambda t: t.get("opened_at") or "")
    equity, peak, max_dd = 0.0, 0.0, 0.0
    for t in sorted_trades:
        equity += (t.get("realized_pnl") or 0)
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    # Statistik pro Strategie
    by_strategy: Dict[str, Dict] = {}
    for s in signals:
        sid = s.get("strategy_id", "unknown")
        e = by_strategy.setdefault(sid, {"total": 0, "wins": 0, "losses": 0, "crv_sum": 0.0})
        e["total"] += 1
        if s.get("result") == "win":
            e["wins"] += 1
        elif s.get("result") == "loss":
            e["losses"] += 1
        e["crv_sum"] += (s.get("crv") or 0)
    strat_stats = []
    for sid, e in by_strategy.items():
        strat = strategy_registry.get(sid)
        d = e["wins"] + e["losses"]
        strat_stats.append({
            "id": sid,
            "name": getattr(strat, "STRATEGY_NAME", sid) if strat else sid,
            "total_signals": e["total"],
            "wins": e["wins"],
            "losses": e["losses"],
            "win_rate_prozent": round(e["wins"] / d * 100, 1) if d else 0.0,
            "avg_crv": round(e["crv_sum"] / e["total"], 2) if e["total"] else 0.0,
        })

    # Häufigste Verlust-Setups (Kombination erfüllter Regeln)
    setup_counts: Dict[str, int] = {}
    for s in signals:
        if s.get("result") != "loss":
            continue
        rules = s.get("rules_met") or {}
        met = sorted(k for k, v in rules.items() if v)
        key = " + ".join(met) if met else "(keine Regel als erfüllt geloggt)"
        setup_counts[key] = setup_counts.get(key, 0) + 1
    top_losing = [{"regeln": k, "verluste": v}
                  for k, v in sorted(setup_counts.items(), key=lambda x: -x[1])[:5]]

    # Regel-Definitionen der Strategien
    strategies_meta = strategy_registry.list_all()

    # ---- Detaillierte Einzeltrades für die KI (exakte Werte & Uhrzeiten) ----
    # Gibt der KI (und dir) pro Trade: Uhrzeit, Entry, SL, TP1, Full-TP, Exit,
    # Ergebnis, PnL, R-Vielfaches, Dauer und Modus (paper/live).
    def _berlin(iso):
        if not iso:
            return None
        try:
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return dt.astimezone(BERLIN).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            return iso

    trades_detail = []
    for t in trades[:60]:
        e = _enrich_trade(t)
        comp = e.get("computed", {})
        trades_detail.append({
            "symbol": e.get("symbol"),
            "strategie": e.get("strategy_name") or e.get("strategy_id"),
            "seite": e.get("side"),
            "modus": e.get("mode"),
            "ergebnis": e.get("result"),
            "eroeffnet": _berlin(e.get("opened_at")),
            "geschlossen": _berlin(e.get("closed_at")),
            "dauer_sekunden": comp.get("duration_seconds"),
            "entry": e.get("entry"),
            "initial_sl": e.get("initial_sl"),
            "sl_final": e.get("sl"),
            "tp1": e.get("tp1"),
            "tp_full": e.get("tpf"),
            "exit": e.get("exit_price"),
            "tp1_getroffen": e.get("tp1_hit"),
            "breakeven": e.get("breakeven_moved"),
            "pnl_usdt": e.get("realized_pnl"),
            "r_vielfaches": comp.get("r_multiple"),
            "pnl_prozent_kapital": comp.get("pnl_pct_capital"),
            "sl_abstand_prozent": comp.get("initial_sl_distance_pct"),
            "tp_full_abstand_prozent": comp.get("tpf_distance_pct"),
            "hebel": e.get("leverage"),
            "kapital_usdt": e.get("max_capital"),
            "verlauf": e.get("events", []),
        })

    # Paper vs. Live getrennt
    def _split(mode_val):
        sub = [t for t in trades if t.get("mode") == mode_val]
        w = sum(1 for t in sub if t.get("result") == "win")
        l = sum(1 for t in sub if t.get("result") == "loss")
        d = w + l
        return {
            "anzahl": len(sub), "wins": w, "losses": l,
            "win_rate_prozent": round(w / d * 100, 1) if d else 0.0,
            "pnl_gesamt_usdt": round(sum((t.get("realized_pnl") or 0) for t in sub), 2),
        }

    tp1_hits = sum(1 for t in trades if t.get("tp1_hit"))
    durations = [d.get("duration_seconds") for d in (
        [_enrich_trade(t).get("computed", {}) for t in trades]) if d.get("duration_seconds")]
    avg_dur = round(sum(durations) / len(durations)) if durations else 0

    return {
        "gefiltert_auf_strategie": strategy_id,
        "signale_gesamt": {"anzahl": total, "wins": wins, "losses": losses,
                           "win_rate_prozent": win_rate, "avg_crv": avg_crv},
        "trades_geschlossen": {"anzahl": len(trades), "wins": trade_wins, "losses": trade_losses,
                               "win_rate_prozent": trade_win_rate, "pnl_gesamt_usdt": total_pnl,
                               "max_drawdown_usdt": round(max_dd, 2),
                               "tp1_treffer": tp1_hits,
                               "durchschnittsdauer_sekunden": avg_dur},
        "trades_paper": _split("paper"),
        "trades_live": _split("live"),
        "je_strategie": sorted(strat_stats, key=lambda x: -x["total_signals"]),
        "haeufigste_verlust_setups": top_losing,
        "einzeltrades_detail": trades_detail,
        "strategien_definitionen": strategies_meta,
    }


@app.post("/api/analytics/ai-review")
async def ai_review(body: Dict = None):
    """Aggregierte Trading-Statistiken an ein KI-Modell senden und deutsche
    Coach-Auswertung zurückgeben. Provider frei konfigurierbar via .env
    (OpenAI-kompatibel). Standard: Groq (kostenlos, keine Kreditkarte)."""
    import json as _json
    body = body or {}
    strategy_id = body.get("strategy_id")

    # ---- KI-Provider-Konfiguration (OpenAI-kompatibel) ----
    # In backend/.env setzen:  AI_API_KEY, AI_BASE_URL, AI_MODEL
    #  GROQ (empfohlen, gratis):
    #     AI_BASE_URL=https://api.groq.com/openai/v1
    #     AI_MODEL=llama-3.3-70b-versatile
    #  OpenRouter (gratis, 50/Tag):
    #     AI_BASE_URL=https://openrouter.ai/api/v1
    #     AI_MODEL=deepseek/deepseek-chat-v3-0324:free
    #  Google Gemini:
    #     AI_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
    #     AI_MODEL=gemini-2.0-flash

    # Abwärtskompatibel: alte GEMINI_*-Variablen werden weiter akzeptiert.
    api_key = (
        os.getenv("AI_API_KEY")
        or os.getenv("GROQ_API_KEY")
        or os.getenv("OPENROUTER_API_KEY")
        or os.getenv("GEMINI_API_KEY")
    )
    if not api_key:
        raise HTTPException(
            status_code=500,
            detail=("Kein KI-API-Key gesetzt. Trage in backend/.env AI_API_KEY ein. "
                    "Kostenlosen Groq-Key holen: https://console.groq.com/keys, "
                    "dann Backend neu starten."),
        )

    base_url = (os.getenv("AI_BASE_URL") or "https://api.groq.com/openai/v1").strip()
    model_name = (os.getenv("AI_MODEL") or os.getenv("GEMINI_MODEL")
                  or "llama-3.3-70b-versatile").strip().strip('"').strip("'").strip()
    if model_name.startswith("models/"):
        model_name = model_name[len("models/"):]
    if not model_name:
        model_name = "llama-3.3-70b-versatile"

    stats = await _aggregate_ai_stats(strategy_id)

    system_msg = (
        "Du bist ein erfahrener Trading-Coach mit Fokus auf Krypto-Daytrading und Scalping. "
        "Analysiere die übergebenen Statistiken sachlich, prägnant und pragmatisch. "
        "Nenne konkret welche Regeln nicht funktionieren und schlage präzise, umsetzbare "
        "Änderungen vor. Antworte auf Deutsch in Markdown mit klaren Sektionen."
    )
    user_text = (
        "Hier sind die aktuellen aggregierten Trading-Statistiken und Regel-Definitionen "
        "als JSON:\n\n```json\n"
        + _json.dumps(stats, ensure_ascii=False, indent=2, default=str)
        + "\n```\n\nAufgabe:\n"
        "1) **Kurz-Fazit** (2-3 Sätze zur Gesamtlage).\n"
        "2) **Problematische Regeln / Setups** - nenne konkret welche Regeln oder "
        "Regelkombinationen unterdurchschnittlich performen und WARUM (Zahlen zitieren).\n"
        "3) **Einzeltrade-Analyse** - nutze `einzeltrades_detail` (exakte Uhrzeiten, "
        "Entry, SL, TP1, Full-TP, Exit, R-Vielfaches, Dauer, paper/live). Finde Muster: "
        "Zu welchen Uhrzeiten/Setups laufen Trades in den SL? Werden TPs zu früh/zu spät "
        "gesetzt? Ist das SL-zu-TP-Verhältnis realistisch? Vergleiche paper vs. live.\n"
        "4) **Konkrete Änderungsvorschläge** - parameterbezogen oder logikbezogen, "
        "so präzise wie möglich (z.B. RSI-Schwelle anpassen, TP/SL-Ratio ändern, "
        "Setup entfernen, Filter hinzufügen, bestimmte Handelszeiten meiden).\n"
        "Antworte auf Deutsch."
    )

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        completion = await client.chat.completions.create(
            model=model_name,
            temperature=0.4,
            messages=[
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_text},
            ],
        )
        review = (completion.choices[0].message.content or "").strip()
        if not review:
            raise RuntimeError("Leere Antwort vom KI-Modell erhalten.")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception("KI-Analyse fehlgeschlagen")
        raise HTTPException(status_code=502, detail=f"KI-Analyse fehlgeschlagen: {e}")

    return {"review": review, "stats": stats, "model": model_name}


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
    from strategies.custom_strategy import INDICATORS, OPERATORS, INDICATOR_META, PERIOD_FIELDS
    return {"indicators": INDICATORS, "operators": OPERATORS,
            "indicator_meta": INDICATOR_META, "period_fields": PERIOD_FIELDS}


# ---------------- Strategie-Vergleich ----------------
@app.get("/api/analytics/strategy-comparison")
async def strategy_comparison(mode: str = "all", days: int = 0):
    """Vergleicht alle Strategien anhand ihrer geschlossenen Trades:
    Trades, Win-Rate, PnL, Profit-Faktor, Max Drawdown, Ø Dauer, je Coin."""
    q: Dict = {"status": "closed"}
    if mode in ("paper", "live"):
        q["mode"] = mode
    if days and days > 0:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        q["opened_at"] = {"$gte": cutoff}
    trades = await app.mongodb.auto_trades.find(q).sort("opened_at", 1).to_list(10000)
    open_counts: Dict[str, int] = {}
    async for t in app.mongodb.auto_trades.find({"status": "open"}):
        sid = t.get("strategy_id") or "unknown"
        open_counts[sid] = open_counts.get(sid, 0) + 1

    def _dur_min(t):
        try:
            o = datetime.fromisoformat(t["opened_at"].replace("Z", "+00:00"))
            c = datetime.fromisoformat(t["closed_at"].replace("Z", "+00:00"))
            return (c - o).total_seconds() / 60
        except Exception:
            return None

    by_strat: Dict[str, Dict] = {}
    for t in trades:
        sid = t.get("strategy_id") or "unknown"
        e = by_strat.setdefault(sid, {
            "strategy_id": sid,
            "strategy_name": t.get("strategy_name") or sid,
            "trades": 0, "wins": 0, "losses": 0, "breakevens": 0,
            "pnl": 0.0, "fees": 0.0, "gross_win": 0.0, "gross_loss": 0.0,
            "long_trades": 0, "short_trades": 0,
            "paper_trades": 0, "live_trades": 0,
            "_durs": [], "_equity": 0.0, "_peak": 0.0, "max_drawdown": 0.0,
            "best_trade": 0.0, "worst_trade": 0.0,
            "by_symbol": {},
        })
        pnl = float(t.get("realized_pnl") or 0)
        res = t.get("result")
        e["trades"] += 1
        if res == "win":
            e["wins"] += 1
        elif res == "loss":
            e["losses"] += 1
        elif res == "breakeven":
            e["breakevens"] += 1
        e["pnl"] = round(e["pnl"] + pnl, 4)
        e["fees"] = round(e["fees"] + float(t.get("fees_paid") or 0), 4)
        if pnl > 0:
            e["gross_win"] += pnl
        else:
            e["gross_loss"] += abs(pnl)
        e["best_trade"] = round(max(e["best_trade"], pnl), 4)
        e["worst_trade"] = round(min(e["worst_trade"], pnl), 4)
        if t.get("side") == "LONG":
            e["long_trades"] += 1
        else:
            e["short_trades"] += 1
        if t.get("mode") == "live":
            e["live_trades"] += 1
        else:
            e["paper_trades"] += 1
        d = _dur_min(t)
        if d is not None:
            e["_durs"].append(d)
        e["_equity"] += pnl
        e["_peak"] = max(e["_peak"], e["_equity"])
        e["max_drawdown"] = round(max(e["max_drawdown"], e["_peak"] - e["_equity"]), 4)
        sym = t.get("symbol") or "?"
        s = e["by_symbol"].setdefault(sym, {"symbol": sym, "trades": 0, "wins": 0,
                                            "losses": 0, "pnl": 0.0})
        s["trades"] += 1
        if res == "win":
            s["wins"] += 1
        elif res == "loss":
            s["losses"] += 1
        s["pnl"] = round(s["pnl"] + pnl, 4)

    out = []
    for sid, e in by_strat.items():
        decided = e["wins"] + e["losses"]
        e["win_rate"] = round(e["wins"] / decided * 100, 1) if decided else 0.0
        e["avg_pnl"] = round(e["pnl"] / e["trades"], 4) if e["trades"] else 0.0
        gl = e.pop("gross_loss")
        gw = e.pop("gross_win")
        e["profit_factor"] = round(gw / gl, 2) if gl > 0 else (round(gw, 2) if gw else 0.0)
        durs = e.pop("_durs")
        e["avg_duration_min"] = round(sum(durs) / len(durs), 1) if durs else 0.0
        e.pop("_equity", None)
        e.pop("_peak", None)
        e["open_trades"] = open_counts.get(sid, 0)
        for s in e["by_symbol"].values():
            sd = s["wins"] + s["losses"]
            s["win_rate"] = round(s["wins"] / sd * 100, 1) if sd else 0.0
        e["by_symbol"] = sorted(e["by_symbol"].values(), key=lambda x: -x["pnl"])
        strat = strategy_registry.get(sid)
        if strat:
            e["strategy_name"] = getattr(strat, "STRATEGY_NAME", e["strategy_name"])
        out.append(e)
    out.sort(key=lambda x: -x["pnl"])
    return {"mode": mode, "days": days, "comparison": out,
            "total_trades": len(trades)}


# ---------------- Backtester ----------------
from services import backtester as bt


@app.post("/api/backtest/run")
async def start_backtest(body: Dict, _: bool = Depends(require_admin)):
    strategy_ids = body.get("strategy_ids") or []
    symbols = body.get("symbols") or []
    days = min(max(int(body.get("days") or 3), 1), 365)
    if not strategy_ids or not symbols:
        raise HTTPException(status_code=400, detail="strategy_ids und symbols erforderlich")
    valid_ids = {m["id"] for m in strategy_registry.list_all()}
    strategy_ids = [s for s in strategy_ids if s in valid_ids]
    symbols = [s for s in symbols if s in TOP_10_COINS]
    if not strategy_ids or not symbols:
        raise HTTPException(status_code=400, detail="Keine gültigen Strategien/Coins")
    running = [j for j in bt.JOBS.values() if j["status"] == "running"]
    if running:
        raise HTTPException(status_code=409, detail="Es läuft bereits ein Backtest")
    cfg = dict(DEFAULT_COIN_CFG)
    for k in ("max_capital", "leverage", "fee_percent", "tp1_crv", "tp_full_crv",
              "tp1_close_percent", "sl_mode", "sl_fixed_percent", "sl_lookback",
              "breakeven_enabled", "trail_after_tp1", "profit_secure_enabled",
              "profit_secure_trigger_pct", "profit_lock_pct",
              "be_mode", "be_trigger_crv", "be_trigger_profit_pct",
              "be_smart_lookback", "require_all_rules", "sessions"):
        if body.get(k) is not None:
            cfg[k] = body[k]
    strategy_configs = body.get("strategy_configs") or {}
    if not isinstance(strategy_configs, dict):
        strategy_configs = {}
    default_tf = body.get("timeframe")
    params = {"strategy_ids": strategy_ids, "symbols": symbols, "days": days,
              "max_capital": cfg["max_capital"], "leverage": cfg["leverage"],
              "fee_percent": cfg["fee_percent"], "timeframe": default_tf,
              "strategy_configs": strategy_configs}
    job_id = bt.create_job(params)
    asyncio.create_task(bt.run_backtest(job_id, strategy_ids, symbols, days, cfg,
                                        strategy_registry, scanner.settings,
                                        app.mongodb, strategy_configs, default_tf))
    return {"status": "started", "job_id": job_id}


def _job_public(job: Dict) -> Dict:
    """Job ohne Export-Rohdaten (sonst riesige Antworten) + ETA."""
    j = {k: v for k, v in job.items() if k not in ("export_candles", "export_trades")}
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


@app.get("/api/backtest/status/{job_id}")
async def backtest_status(job_id: str):
    job = bt.JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    return _job_public(job)


@app.post("/api/backtest/cancel/{job_id}")
async def backtest_cancel(job_id: str, _: bool = Depends(require_admin)):
    job = bt.JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    job["cancel"] = True
    if job.get("status") == "running":
        job["phase"] = "Wird abgebrochen..."
    return {"status": "cancelling", "job_id": job_id}


@app.get("/api/backtest/active")
async def backtest_active():
    """Läuft gerade ein Backtest? (für Fortschritt nach erneutem Öffnen des Popups)"""
    running = [j for j in bt.JOBS.values() if j["status"] == "running"]
    if running:
        return {"active": _job_public(running[-1])}
    done = sorted([j for j in bt.JOBS.values() if j["status"] in ("done", "error", "cancelled")],
                  key=lambda x: x["created_at"])
    return {"active": None, "last": _job_public(done[-1]) if done else None}


@app.get("/api/backtest/results")
async def backtest_results(limit: int = 5):
    rows = await app.mongodb.backtests.find().sort("created_at", -1).limit(limit).to_list(limit)
    return {"results": [_clean(r) for r in rows]}


# ---- Backtest-spezifische Strategie-Einstellungen (getrennt von Live/Paper) ----
@app.get("/api/backtest/strategy-configs")
async def get_backtest_strategy_configs():
    doc = await app.mongodb.settings.find_one({"_id": "backtest_strategy_configs"})
    return {"configs": (doc or {}).get("configs", {})}


@app.post("/api/backtest/strategy-configs")
async def set_backtest_strategy_configs(body: Dict, _: bool = Depends(require_admin)):
    configs = body.get("configs")
    if not isinstance(configs, dict):
        raise HTTPException(status_code=400, detail="configs (dict) erforderlich")
    await app.mongodb.settings.update_one({"_id": "backtest_strategy_configs"},
                                          {"$set": {"configs": configs}}, upsert=True)
    return {"status": "success", "configs": configs}


# ---- CSV-Export der Backtest-Rohdaten (Trades + Kerzen) ----
def _rows_to_csv(rows: List[Dict], fieldnames: List[str]) -> str:
    import csv
    import io
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=fieldnames, extrasaction="ignore")
    w.writeheader()
    for r in rows:
        w.writerow(r)
    return buf.getvalue()


@app.get("/api/backtest/export/{job_id}")
async def backtest_export(job_id: str, kind: str = "trades"):
    from fastapi.responses import Response
    job = bt.JOBS.get(job_id)
    if kind == "candles":
        data = (job or {}).get("export_candles")
        if not data:
            raise HTTPException(status_code=404,
                                detail="Kerzendaten sind nur für den zuletzt gelaufenen Backtest verfügbar")
        rows = []
        for key, candles in data.items():
            sym, tf = key.split("|")
            for c in candles:
                rows.append({"symbol": sym, "timeframe": tf, "timestamp": c["timestamp"],
                             "time_utc": datetime.fromtimestamp(c["timestamp"] / 1000,
                                                                tz=timezone.utc).isoformat(),
                             "open": c["open"], "high": c["high"], "low": c["low"],
                             "close": c["close"], "volume": c.get("volume", 0)})
        csv_str = _rows_to_csv(rows, ["symbol", "timeframe", "timestamp", "time_utc",
                                      "open", "high", "low", "close", "volume"])
    else:
        rows = (job or {}).get("export_trades")
        if rows is None:
            doc = await app.mongodb.backtest_trades.find_one({"job_id": job_id})
            rows = (doc or {}).get("rows")
        if rows is None:
            raise HTTPException(status_code=404, detail="Keine Trade-Daten für diesen Backtest gefunden")
        fields = ["strategy_id", "strategy_name", "symbol", "timeframe", "side",
                  "opened", "closed", "duration_min", "entry", "exit", "sl_initial",
                  "sl_final", "tp1", "tp_full", "risk", "result", "pnl", "fees", "qty",
                  "tp1_done", "breakeven_moved", "crv_signal", "rules_met", "rules_total",
                  "rsi_entry", "ema_fast_entry", "ema_slow_entry", "atr_entry",
                  "entry_candle_open", "entry_candle_high", "entry_candle_low",
                  "entry_candle_close", "entry_candle_volume"]
        csv_str = _rows_to_csv(rows, fields)
    return Response(content=csv_str, media_type="text/csv",
                    headers={"Content-Disposition":
                             f'attachment; filename="backtest_{job_id}_{kind}.csv"'})


# ---------------- Optimizer ----------------
from services import optimizer as opt


@app.post("/api/optimizer/run")
async def start_optimizer(body: Dict, _: bool = Depends(require_admin)):
    mode = body.get("mode", "params")
    if mode not in ("params", "discovery", "combo"):
        raise HTTPException(status_code=400, detail="mode muss params|discovery|combo sein")
    symbols = [s for s in (body.get("symbols") or []) if s in TOP_10_COINS]
    if not symbols:
        raise HTTPException(status_code=400, detail="Mindestens 1 gültiger Coin erforderlich")
    if mode == "params":
        sid = body.get("strategy_id")
        if not sid or not strategy_registry.get(sid):
            raise HTTPException(status_code=400, detail="Gültige strategy_id erforderlich")
    running = [j for j in opt.JOBS.values() if j["status"] == "running"]
    if running:
        raise HTTPException(status_code=409, detail="Es läuft bereits eine Optimierung")
    body["symbols"] = symbols
    if body.get("base_strategy_id"):
        base = strategy_registry.get(body["base_strategy_id"])
        if not base or not getattr(base, "IS_CUSTOM", False):
            raise HTTPException(status_code=400,
                                detail="Basis-Strategie muss eine Custom-Strategie sein")
    params = {k: body.get(k) for k in ("mode", "strategy_id", "symbols", "days",
                                       "timeframe", "objective", "iterations",
                                       "min_trades", "max_rules", "indicators",
                                       "algorithm", "base_strategy_id")}
    job_id = opt.create_job(params)
    asyncio.create_task(opt.run_optimizer(job_id, body, strategy_registry,
                                          scanner.settings, DEFAULT_COIN_CFG,
                                          app.mongodb))
    return {"status": "started", "job_id": job_id}


@app.get("/api/optimizer/status/{job_id}")
async def optimizer_status(job_id: str):
    job = opt.JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    return _job_public(job)


@app.post("/api/optimizer/cancel/{job_id}")
async def optimizer_cancel(job_id: str, _: bool = Depends(require_admin)):
    job = opt.JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job nicht gefunden")
    job["cancel"] = True
    if job.get("status") == "running":
        job["phase"] = "Wird abgebrochen..."
    return {"status": "cancelling", "job_id": job_id}


@app.get("/api/optimizer/active")
async def optimizer_active():
    """Läuft gerade eine Optimierung? (Fortschritt bleibt nach Schließen sichtbar)"""
    running = [j for j in opt.JOBS.values() if j["status"] == "running"]
    if running:
        return {"active": _job_public(running[-1])}
    done = sorted([j for j in opt.JOBS.values() if j["status"] in ("done", "error", "cancelled")],
                  key=lambda x: x["created_at"])
    return {"active": None, "last": _job_public(done[-1]) if done else None}


@app.get("/api/optimizer/results")
async def optimizer_results(limit: int = 5):
    rows = await app.mongodb.optimizer_runs.find().sort("created_at", -1).limit(limit).to_list(limit)
    return {"results": [_clean(r) for r in rows]}


@app.get("/api/optimizer/overrides/{strategy_id}")
async def optimizer_overrides(strategy_id: str):
    """Coins, die Coin-spezifische Optimizer-Einstellungen für diese Strategie haben."""
    param_syms = {s for s, v in
                  scanner.settings.get("coin_params", {}).get(strategy_id, {}).items() if v}
    trade_syms = set()
    prefix = strategy_id + "_"
    docs = await app.mongodb.strategy_coin_configs.find(
        {"_id": {"$regex": f"^{prefix}"}}).to_list(500)
    for d in docs:
        cfg = d.get("config", {})
        if cfg.get("optimizer_applied"):
            trade_syms.add((d.get("_id") or "")[len(prefix):])
    return {"strategy_id": strategy_id, "symbols": sorted(param_syms | trade_syms)}


@app.post("/api/optimizer/apply")
async def optimizer_apply(body: Dict, _: bool = Depends(require_admin)):
    """Optimierungs-Ergebnis übernehmen:
    - type=params: beste Parameter in die Live/Paper Strategie-Einstellungen schreiben
    - type=strategy: entdeckte Strategie als Custom-Strategie speichern"""
    apply_type = body.get("type")
    if apply_type == "params":
        sid = body.get("strategy_id")
        if not sid or not strategy_registry.get(sid):
            raise HTTPException(status_code=400, detail="Gültige strategy_id erforderlich")
        params = body.get("params") or {}
        trade_params = body.get("trade_params") or {}
        scope = body.get("scope", "global")

        # ---- scope=coins: Coin-spezifische Overrides nur für optimierte Coins ----
        if scope == "coins":
            symbols = [s for s in (body.get("symbols") or []) if s in ALL_SYMBOLS]
            if not symbols:
                raise HTTPException(status_code=400,
                                    detail="Mindestens 1 Coin erforderlich für scope=coins")
            now_iso = datetime.now(timezone.utc).isoformat()
            if params:
                cp = dict(scanner.settings.get("coin_params", {}))
                strat_cp = dict(cp.get(sid, {}))
                for sym in symbols:
                    strat_cp[sym] = {**strat_cp.get(sym, {}), **params}
                cp[sid] = strat_cp
                scanner.update_settings({"coin_params": cp})
                await app.mongodb.settings.update_one({"_id": "scanner_settings"},
                                                      {"$set": scanner.settings}, upsert=True)
            for sym in symbols:
                key = f"{sid}_{sym}"
                doc = await app.mongodb.strategy_coin_configs.find_one({"_id": key})
                cfg_sc = dict((doc or {}).get("config", {}))
                for k in ("tp1_crv", "tp_full_crv", "tp1_close_percent", "sl_lookback"):
                    if trade_params.get(k) is not None:
                        cfg_sc[k] = trade_params[k]
                cfg_sc["optimizer_applied"] = now_iso
                await app.mongodb.strategy_coin_configs.replace_one(
                    {"_id": key}, {"_id": key, "config": cfg_sc}, upsert=True)
                autotrader.config.setdefault("strategy_coin_configs", {})[key] = cfg_sc
            return {"status": "success", "strategy_id": sid, "scope": "coins",
                    "symbols": symbols, "params": params, "trade_params": trade_params}

        # ---- scope=global (Default): Einstellungen für alle Coins ----
        sp = dict(scanner.settings.get("strategy_params", {}))
        sp[sid] = {**sp.get(sid, {}), **params}
        scanner.update_settings({"strategy_params": sp})
        await app.mongodb.settings.update_one({"_id": "scanner_settings"},
                                              {"$set": scanner.settings}, upsert=True)
        if trade_params:
            overrides = dict(autotrader.config.get("strategy_overrides", {}))
            current = overrides.get(sid, dict(DEFAULT_STRATEGY_OVERRIDE))
            for k in ("tp1_crv", "tp_full_crv", "tp1_close_percent", "sl_lookback"):
                if trade_params.get(k) is not None:
                    current[k] = trade_params[k]
            overrides[sid] = current
            new_cfg = {"mode": autotrader.config.get("mode", "paper"),
                       "coins": autotrader.config.get("coins", {}),
                       "strategy_overrides": overrides}
            autotrader.set_config(new_cfg)
            await app.mongodb.settings.update_one(
                {"_id": "autotrade_config"},
                {"$set": {"mode": new_cfg["mode"], "coins": new_cfg["coins"],
                          "strategy_overrides": overrides}}, upsert=True)
        return {"status": "success", "strategy_id": sid, "params": sp[sid],
                "trade_params": trade_params, "scope": "global"}
    if apply_type == "backtest":
        # Beste Parameter in die Backtest-Strategie-Einstellungen übernehmen,
        # damit optimierte Strategien direkt im Backtester getestet werden können
        sid = body.get("strategy_id")
        if not sid or not strategy_registry.get(sid):
            raise HTTPException(status_code=400, detail="Gültige strategy_id erforderlich")
        doc = await app.mongodb.settings.find_one({"_id": "backtest_strategy_configs"})
        configs = (doc or {}).get("configs", {})
        c = dict(configs.get(sid, {}))
        params = body.get("params") or {}
        if params:
            c["params"] = {**c.get("params", {}), **params}
        trade_params = body.get("trade_params") or {}
        for k in ("tp1_crv", "tp_full_crv", "tp1_close_percent", "sl_lookback"):
            if trade_params.get(k) is not None:
                c[k] = trade_params[k]
        if body.get("timeframe"):
            c["timeframe"] = body["timeframe"]
        configs[sid] = c
        await app.mongodb.settings.update_one({"_id": "backtest_strategy_configs"},
                                              {"$set": {"configs": configs}}, upsert=True)
        return {"status": "success", "strategy_id": sid, "config": c}
    if apply_type == "strategy":
        definition = body.get("definition")
        if not isinstance(definition, dict) or not definition.get("long_rules"):
            raise HTTPException(status_code=400, detail="Gültige definition erforderlich")
        # bestehende Custom-Strategie aktualisieren statt neue anzulegen
        update_id = body.get("update_strategy_id")
        if update_id:
            existing = strategy_registry.get(update_id)
            if not existing or not getattr(existing, "IS_CUSTOM", False):
                raise HTTPException(status_code=400,
                                    detail="update_strategy_id muss eine Custom-Strategie sein")
            sid = update_id
            definition["name"] = body.get("name") or existing.definition.get("name") or "Optimizer Strategie"
        else:
            sid = f"custom_{uuid.uuid4().hex[:8]}"
            definition["name"] = body.get("name") or definition.get("name") or "Optimizer Strategie"
        definition["id"] = sid
        definition.setdefault("description", "Vom Optimizer entdeckte Strategie")
        definition.setdefault("timeframe", body.get("timeframe") or "1m")
        await app.mongodb.custom_strategies.update_one({"id": sid}, {"$set": definition}, upsert=True)
        strategy_registry.upsert_custom(definition)
        enabled = scanner.settings.get("enabled_strategies", [])
        if sid not in enabled:
            enabled.append(sid)
            scanner.update_settings({"enabled_strategies": enabled})
        tf = body.get("timeframe")
        if tf:
            tfs = dict(scanner.settings.get("strategy_timeframes", {}))
            tfs[sid] = tf
            scanner.update_settings({"strategy_timeframes": tfs})
        await app.mongodb.settings.update_one({"_id": "scanner_settings"},
                                              {"$set": scanner.settings}, upsert=True)
        return {"status": "success", "id": sid, "definition": definition,
                "updated": bool(body.get("update_strategy_id"))}
    raise HTTPException(status_code=400, detail="type muss params|strategy|backtest sein")


# ---- autotrade ----
@app.get("/api/autotrade/config")
async def get_autotrade_config():
    return {"config": autotrader.config, "defaults": DEFAULT_COIN_CFG,
            "bitunix_configured": trade_client.configured(),
            "strategy_overrides": autotrader.config.get("strategy_overrides", {})}


@app.post("/api/autotrade/config")
async def set_autotrade_config(config: Dict, _: bool = Depends(require_admin)):
    if "mode" not in config:
        config["mode"] = autotrader.config.get("mode", "paper")
    config.setdefault("coins", autotrader.config.get("coins", {}))
    config.setdefault("strategy_overrides", autotrader.config.get("strategy_overrides", {}))
    autotrader.set_config(config)
    await app.mongodb.settings.update_one({"_id": "autotrade_config"},
                                          {"$set": {"mode": config["mode"], "coins": config["coins"], 
                                                    "strategy_overrides": config.get("strategy_overrides", {})}},
                                          upsert=True)
    return {"status": "success", "config": autotrader.config}


@app.post("/api/autotrade/coin/{symbol}")
async def set_coin_config(symbol: str, cfg: Dict, _: bool = Depends(require_admin)):
    coins = dict(autotrader.config.get("coins", {}))
    merged = dict(DEFAULT_COIN_CFG)
    merged.update(coins.get(symbol, {}))
    merged.update(cfg)
    coins[symbol] = merged
    new_cfg = {"mode": autotrader.config.get("mode", "paper"), "coins": coins,
               "strategy_overrides": autotrader.config.get("strategy_overrides", {})}
    autotrader.set_config(new_cfg)
    await app.mongodb.settings.update_one({"_id": "autotrade_config"},
                                          {"$set": {"mode": new_cfg["mode"], "coins": coins,
                                                    "strategy_overrides": new_cfg.get("strategy_overrides", {})}}, upsert=True)
    return {"status": "success", "coin": symbol, "config": merged}


# ---- NEW: Strategy-level auto-trade override ----
# Mirrors the coin-level DEFAULT_COIN_CFG so a strategy can fully override
# the trade parameters. `mode` here is per-strategy ('live'|'paper'|'off')
# and takes precedence over the global config mode. 'off' disables the
# strategy entirely (no trades, no signal notifications override happens
# through signals_enabled).
DEFAULT_STRATEGY_OVERRIDE = {
    "enabled": False,
    "mode": "off",  # "live" | "paper" | "off"
    "signals_enabled": True,  # Bell toggle for signal notifications
    # Trade sizing
    "max_capital": 100.0,
    "leverage": 10,
    # SL config
    "sl_mode": "structure",       # structure | fixed
    "sl_fixed_percent": 1.0,
    "sl_ticks": 4,
    "sl_lookback": 10,
    # TP config
    "tp1_crv": 1.0,
    "tp1_close_percent": 50,
    "tp_full_crv": 2.0,
    "breakeven_enabled": True,
    "be_mode": "tp1",
    "be_trigger_crv": 1.0,
    "be_trigger_profit_pct": 30.0,
    "be_smart_lookback": 10,
    "require_all_rules": False,
    "fee_percent": 0.06,
    "trade_pre_signals": False,
    "profit_secure_enabled": False,
    "profit_secure_trigger_pct": 30.0,
    "profit_lock_pct": 50.0,
}


@app.post("/api/autotrade/strategy/{strategy_id}")
async def set_strategy_autotrade(strategy_id: str, cfg: Dict, _: bool = Depends(require_admin)):
    """Set auto-trade configuration for a specific strategy.
    This overrides the global mode and coin-level settings when this strategy fires."""
    overrides = dict(autotrader.config.get("strategy_overrides", {}))
    current = overrides.get(strategy_id, dict(DEFAULT_STRATEGY_OVERRIDE))
    current.update(cfg)
    overrides[strategy_id] = current
    
    new_cfg = {
        "mode": autotrader.config.get("mode", "paper"),
        "coins": autotrader.config.get("coins", {}),
        "strategy_overrides": overrides
    }
    autotrader.set_config(new_cfg)
    await app.mongodb.settings.update_one(
        {"_id": "autotrade_config"},
        {"$set": {"mode": new_cfg["mode"], "coins": new_cfg["coins"], 
                  "strategy_overrides": overrides}},
        upsert=True
    )
    return {"status": "success", "strategy_id": strategy_id, "config": current}


@app.get("/api/autotrade/strategy/{strategy_id}")
async def get_strategy_autotrade(strategy_id: str):
    """Get auto-trade configuration for a specific strategy."""
    overrides = autotrader.config.get("strategy_overrides", {})
    cfg = overrides.get(strategy_id, dict(DEFAULT_STRATEGY_OVERRIDE))
    return {"strategy_id": strategy_id, "config": cfg, "defaults": DEFAULT_STRATEGY_OVERRIDE}


# ---- NEW: per (strategy, coin) enable/disable toggle ----
@app.get("/api/strategies/{strategy_id}/coins")
async def get_strategy_coin_toggles(strategy_id: str):
    """Return {symbol: enabled} map for the given strategy across ALL_SYMBOLS.
    Missing rows default to True (kept enabled)."""
    result: Dict[str, bool] = {}
    for sym in ALL_SYMBOLS:
        result[sym] = strategy_coin_toggles.get((strategy_id, sym), True)
    return {"strategy_id": strategy_id, "coins": result}


@app.put("/api/strategies/{strategy_id}/coins/{symbol}")
async def set_strategy_coin_toggle(strategy_id: str, symbol: str,
                                    body: Dict, _: bool = Depends(require_admin)):
    """Enable/disable auto-trade + signals for ONE (strategy, coin) pair."""
    enabled = bool(body.get("enabled", True))
    now_iso = datetime.now(timezone.utc).isoformat()
    await app.mongodb.strategy_coin_toggles.update_one(
        {"strategy_id": strategy_id, "symbol": symbol},
        {"$set": {"strategy_id": strategy_id, "symbol": symbol,
                  "enabled": enabled, "updated_at": now_iso}},
        upsert=True,
    )
    strategy_coin_toggles[(strategy_id, symbol)] = enabled
    return {"status": "success", "strategy_id": strategy_id,
            "symbol": symbol, "enabled": enabled}

# ── PER-COIN-PER-STRATEGY CONFIG ─────────────────────────────────────────────

DEFAULT_STRATEGY_COIN_CFG: dict = {
    "enabled": False,
    "mode": "off",
    "signals_enabled": True,
    "max_capital": 100.0,
    "leverage": 10,
    "order_type": "MARKET",
    "sl_mode": "structure",
    "sl_fixed_percent": 1.0,
    "sl_ticks": 4,
    "sl_lookback": 10,
    "tp1_crv": 1.0,
    "tp1_close_percent": 50,
    "tp_full_crv": 2.0,
    "breakeven_enabled": True,
    "be_mode": "tp1",
    "be_trigger_crv": 1.0,
    "be_trigger_profit_pct": 30.0,
    "be_smart_lookback": 10,
    "require_all_rules": False,
    "fee_percent": 0.06,
    "trade_pre_signals": False,
    "profit_secure_enabled": False,
    "profit_secure_trigger_pct": 30.0,
    "profit_lock_pct": 50.0,
}

@app.get("/api/autotrade/strategy/{strategy_id}/coin/{symbol}")
async def get_strategy_coin_autotrade(
    strategy_id: str,
    symbol: str,
):
    doc = await app.mongodb.strategy_coin_configs.find_one({"_id": f"{strategy_id}_{symbol}"})
    saved = doc.get("config", {}) if doc else {}
    merged = {**DEFAULT_STRATEGY_COIN_CFG, **saved}
    return {"config": merged}

@app.post("/api/autotrade/strategy/{strategy_id}/coin/{symbol}")
async def set_strategy_coin_autotrade(
    strategy_id: str,
    symbol: str,
    body: dict,
    _=Depends(require_admin)
):
    key = f"{strategy_id}_{symbol}"
    await app.mongodb.strategy_coin_configs.replace_one(
        {"_id": key},
        {"_id": key, "config": body},
        upsert=True
    )
    # Sync to in-memory autotrader config
    autotrader.config.setdefault("strategy_coin_configs", {})[key] = body
    logger.info(f"[AutoTrade] Per-coin config saved: strategy={strategy_id} coin={symbol} mode={body.get('mode')}")
    return {"ok": True}


@app.get("/api/autotrade/strategy_coin_configs")
async def list_strategy_coin_autotrade(_=Depends(require_admin)):
    """Return ALL per-strategy per-coin auto-trade configs as a nested dict:
        { strategy_id: { symbol: { mode, enabled, ... } } }
    Used by the frontend to reflect the active mode on the strategy blitz icon.
    """
    docs = await app.mongodb.strategy_coin_configs.find().to_list(2000)
    out: Dict[str, Dict[str, Dict]] = {}
    for d in docs:
        key = d.get("_id") or ""
        if "_" not in key:
            continue
        # split on the LAST underscore so strategy ids with underscores still work
        strategy_id, symbol = key.rsplit("_", 1)
        out.setdefault(strategy_id, {})[symbol] = d.get("config", {})
    return {"configs": out}

@app.get("/api/autotrade/trades")
async def get_trades(status: str = None, limit: int = 50, mode: str = None):
    q = {}
    if status:
        q["status"] = status
    if mode in ("live", "paper"):
        q["mode"] = mode
    trades = await app.mongodb.auto_trades.find(q).sort("opened_at", -1).limit(limit).to_list(limit)
    return {"trades": [_enrich_trade(t) for t in trades]}


@app.get("/api/autotrade/trades/{trade_id}")
async def get_trade_detail(trade_id: str):
    t = await app.mongodb.auto_trades.find_one({"id": trade_id})
    if not t:
        raise HTTPException(status_code=404, detail="Trade not found")
    return {"trade": _enrich_trade(t)}


@app.post("/api/autotrade/close/{trade_id}")
async def close_trade(trade_id: str, _: bool = Depends(require_admin)):
    t = await app.mongodb.auto_trades.find_one({"id": trade_id})
    if not t:
        raise HTTPException(status_code=404, detail="Trade not found")
    price = scanner.current_price(t["symbol"]) or t["entry"]
    res = await autotrader.manual_close(trade_id, price)
    return {"status": "success", "result": res}


@app.get("/api/autotrade/capital")
async def get_capital_allocation():
    """Kapital-Zuweisung für Live & Paper inkl. aktuell zugewiesenem/freiem Kapital."""
    total = await autotrader._live_total_balance()
    out = {}
    for scope in ("live", "paper"):
        a = autotrader.capital_allocation(scope)
        allocated = await autotrader.allocated_capital(
            scope, total=total if scope == "live" else None)
        used = await autotrader.used_margin(scope)
        out[scope] = {
            **a,
            "allocated": round(allocated, 2) if allocated is not None else None,
            "used_margin": round(used, 2),
            "free": round(allocated - used, 2) if allocated is not None else None,
        }
    return {"allocation": out,
            "live_total_balance": round(total, 2) if total is not None else None,
            "bitunix_configured": trade_client.configured()}


@app.post("/api/autotrade/capital")
async def set_capital_allocation(body: Dict, _: bool = Depends(require_admin)):
    """Kapital-Zuweisung speichern: scope=live|paper, mode=full|fixed|percent, value."""
    scope = body.get("scope")
    if scope not in ("live", "paper"):
        raise HTTPException(status_code=400, detail="scope muss live|paper sein")
    mode = body.get("mode")
    if mode not in ("full", "fixed", "percent"):
        raise HTTPException(status_code=400, detail="mode muss full|fixed|percent sein")
    try:
        value = float(body.get("value") or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Ungültiger Wert")
    if mode == "fixed" and value <= 0:
        raise HTTPException(status_code=400, detail="Fester Betrag muss größer als 0 sein")
    if mode == "percent" and not (0 < value <= 100):
        raise HTTPException(status_code=400, detail="Prozentsatz muss zwischen 1 und 100 liegen")
    if mode == "fixed" and scope == "live":
        total = await autotrader._live_total_balance()
        if total is not None and value > total:
            raise HTTPException(
                status_code=400,
                detail=f"Fester Betrag ({value:.2f} USDT) übersteigt das Gesamtguthaben ({total:.2f} USDT)")
    alloc = dict(autotrader.config.get("capital_allocation", {}) or {})
    entry = dict(alloc.get(scope, {}))
    entry.update({"mode": mode, "value": value})
    if scope == "paper" and body.get("base_balance") is not None:
        try:
            bb = float(body["base_balance"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="Ungültiges Simulations-Guthaben")
        if bb <= 0:
            raise HTTPException(status_code=400, detail="Simulations-Guthaben muss größer als 0 sein")
        entry["base_balance"] = bb
    alloc[scope] = entry
    autotrader.config["capital_allocation"] = alloc
    await app.mongodb.settings.update_one({"_id": "capital_allocation"},
                                          {"$set": alloc}, upsert=True)
    logger.info(f"[Capital] Allocation saved: {scope} -> {entry}")
    return {"status": "success", "allocation": alloc}


@app.get("/api/autotrade/balance")
async def get_balance():
    # Current mode (live or paper)
    mode = autotrader.config.get("mode", "paper")

    # ---- Primary mode stats (live or paper) ----
    open_ct = await app.mongodb.auto_trades.count_documents({"status": "open"})
    closed = await app.mongodb.auto_trades.find({"status": "closed"}).to_list(1000)
    pnl = round(sum(t.get("realized_pnl", 0) for t in closed), 4)

    result = {
        "mode": mode,
        "realized_pnl": pnl,
        "open_trades": open_ct,
        "closed_trades": len(closed),
        "bitunix_configured": trade_client.configured(),
    }

    # ---- Live mode: fetch Bitunix balance ----
    if trade_client.configured():

        try:
            bal = await trade_client.get_balance()
            data = bal.get("data") if isinstance(bal, dict) else None
            if isinstance(data, list) and data:
                data = data[0]
            if isinstance(data, dict):
                def _num(v):
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return 0.0
                available = _num(data.get("available") or data.get("availableBalance"))
                frozen = _num(data.get("frozen"))
                used_margin = _num(data.get("margin"))
                upnl = _num(data.get("crossUnrealizedPNL")) + _num(data.get("isolationUnrealizedPNL"))
                # Wallet balance = frei verfügbar + in Orders geblockt + in Positionen gebundene Margin
                wallet_balance = available + frozen + used_margin
                # Bitunix liefert kein marginBalance/equity-Feld → Equity selbst berechnen:
                # Margin Balance (Equity) = Wallet Balance + unrealisierter PnL
                mb = data.get("marginBalance") or data.get("equity")
                margin_balance = _num(mb) if mb is not None else wallet_balance + upnl
                result["available"] = round(available, 2)
                result["margin_balance"] = round(margin_balance, 2)
                result["wallet_balance"] = round(wallet_balance, 2)
                result["unrealized_pnl"] = round(upnl, 2)
            result["bitunix_code"] = bal.get("code") if isinstance(bal, dict) else None
        except Exception as e:
            result["bitunix_error"] = str(e)[:120]

    # ---- Paper overlay: paper trade stats alongside live ----
    # Only add paper stats if mode is live AND there are paper trades in DB
    if mode == "live":
        try:
            paper_open = await app.mongodb.auto_trades.count_documents(
                {"status": "open", "mode": "paper"}
            )
            paper_closed = await app.mongodb.auto_trades.find(
                {"status": "closed", "mode": "paper"}
            ).to_list(500)
            paper_pnl = round(sum(t.get("realized_pnl", 0) for t in paper_closed), 4)
            # Only include if there's actual paper activity
            if paper_open > 0 or paper_pnl != 0 or len(paper_closed) > 0:
                result["paper_pnl"] = paper_pnl
                result["paper_open_trades"] = paper_open
                result["paper_closed_trades"] = len(paper_closed)
        except Exception:
            pass  # Don't break the main balance if paper query fails

    # ---- Kapital-Zuweisung (für Balance-Widget) ----
    try:
        live_total = result.get("wallet_balance")
        alloc_out = {}
        for scope in ("live", "paper"):
            a = autotrader.capital_allocation(scope)
            allocated = await autotrader.allocated_capital(
                scope, total=live_total if scope == "live" else None)
            used = await autotrader.used_margin(scope)
            alloc_out[scope] = {
                "mode": a.get("mode", "full"),
                "value": a.get("value", 0),
                "base_balance": a.get("base_balance"),
                "allocated": round(allocated, 2) if allocated is not None else None,
                "used_margin": round(used, 2),
                "free": round(allocated - used, 2) if allocated is not None else None,
            }
        result["allocation"] = alloc_out
    except Exception as e:
        logger.warning(f"balance allocation info failed: {e}")

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
