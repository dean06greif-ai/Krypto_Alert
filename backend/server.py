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
import json

# Load environment variables FIRST
load_dotenv()

from services.bitunix_client import BitunixWebSocketClient
from services.market_data import MarketDataFeed
from services.strategy_scanner import StrategyScanner
from services.telegram_bot import TelegramNotifier
from strategies.registry import registry as strategy_registry
from models.signal import Signal, CoinPerformance

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Top 10 coins to track
TOP_10_COINS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "POLUSDT"
]

# "Other" instruments (commodities/metals) via Yahoo Finance - free 1m data
OTHER_INSTRUMENTS = [
    {"symbol": "GOLD", "yahoo": "GC=F", "name": "Gold"},
    {"symbol": "SILVER", "yahoo": "SI=F", "name": "Silver"},
    {"symbol": "OIL", "yahoo": "CL=F", "name": "Oil"},
]
OTHER_YAHOO = {i["symbol"]: i["yahoo"] for i in OTHER_INSTRUMENTS}
ALL_SYMBOLS = TOP_10_COINS + [i["symbol"] for i in OTHER_INSTRUMENTS]

# Global state
scanner = StrategyScanner()
telegram = TelegramNotifier()
bitunix_client = BitunixWebSocketClient()
feed = MarketDataFeed()
active_signals = []
websocket_clients = []

# How often to poll the market data source (seconds)
POLL_INTERVAL = 12
scanner_running = asyncio.Event()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Starting Crypto Scalping Scanner...")
    
    # Connect to MongoDB
    app.mongodb_client = AsyncIOMotorClient(os.getenv("MONGO_URL"))
    app.mongodb = app.mongodb_client[os.getenv("DB_NAME", "crypto_scanner")]
    logger.info("Connected to MongoDB")
    
    # Load persisted settings from MongoDB
    saved_settings = await app.mongodb.settings.find_one({"_id": "scanner_settings"})
    if saved_settings:
        saved_settings.pop("_id", None)
        scanner.update_settings(saved_settings)
        logger.info(f"Loaded settings from MongoDB: {scanner.settings}")
    else:
        # Save defaults to MongoDB
        await app.mongodb.settings.insert_one({
            "_id": "scanner_settings",
            **scanner.settings
        })
        logger.info("Initialized default settings in MongoDB")
    
    # Select a reachable market data source (Bitunix -> Binance -> OKX)
    logger.info("Probing market data sources...")
    await feed.probe("BTCUSDT")

    # Bootstrap historical candles so indicators work immediately
    logger.info(f"Bootstrapping historical candles from '{feed.active_source}'...")
    for symbol in TOP_10_COINS:
        try:
            hist = await feed.fetch(symbol, 200)
            # last candle is the forming minute -> keep only closed candles
            closed = hist[:-1] if len(hist) > 1 else hist
            scanner.bootstrap(symbol, closed)
        except Exception as e:
            logger.error(f"Bootstrap failed for {symbol}: {e}")
        await asyncio.sleep(0.2)  # gentle pacing

    # Bootstrap "Other" instruments (Gold/Silver/Oil) via Yahoo (range=5d for history)
    for inst in OTHER_INSTRUMENTS:
        try:
            hist = await feed.fetch_commodity(inst["yahoo"], "5d")
            closed = hist[:-1] if len(hist) > 1 else hist
            scanner.bootstrap(inst["symbol"], closed[-200:])
        except Exception as e:
            logger.error(f"Bootstrap failed for {inst['symbol']}: {e}")
        await asyncio.sleep(0.2)

    # Start polling scanner
    asyncio.create_task(start_scanner())

    # Send test telegram message
    if telegram.bot:
        await telegram.send_test_message()

    yield

    # Shutdown
    logger.info("Shutting down...")
    scanner_running.clear()
    await feed.close()
    app.mongodb_client.close()

app = FastAPI(title="Crypto Scalping Scanner", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def process_signal(symbol: str, signal: Dict):
    """Persist + notify for a new signal."""
    signal["id"] = str(uuid.uuid4())
    notify = scanner.is_notify_enabled(symbol)
    signal["notify"] = notify
    await app.mongodb.signals.insert_one(dict(signal))  # insert a copy (keeps ObjectId out of broadcast)
    active_signals.append(signal)
    if notify:
        await telegram.send_signal(signal)
    await update_coin_performance(symbol, signal)
    await broadcast_signal(signal)
    logger.info(f"New signal: {signal['type']} for {symbol} ({signal.get('signal_class')}) notify={notify}")


async def start_scanner():
    """
    Poll the active market data source (Bitunix -> Binance -> OKX) for all coins.
    Evaluates the strategy once per newly CLOSED 1-minute candle (max 1/min/coin).
    """
    logger.info(f"Starting polling scanner for {len(TOP_10_COINS)} coins (every {POLL_INTERVAL}s)")
    scanner_running.set()

    while scanner_running.is_set():
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

                closed_candles = klines[:-1]   # last one is still forming
                forming = klines[-1]

                # Feed any newly closed candles; evaluate strategy on each new close
                for candle in closed_candles[-3:]:
                    if scanner.add_closed_candle(symbol, candle):
                        signal = scanner.check_signal(symbol)
                        if signal:
                            await process_signal(symbol, signal)

                # Track forming candle + push live price to chart clients
                scanner.forming[symbol] = forming
                await broadcast_candle(symbol, forming)

            except Exception as e:
                logger.error(f"Scan error for {symbol}: {e}")

            await asyncio.sleep(0.1)  # small gap between instruments

        await asyncio.sleep(POLL_INTERVAL)

async def update_coin_performance(symbol: str, signal: Dict):
    """Update performance stats for a coin"""
    perf = await app.mongodb.performance.find_one({"symbol": symbol})
    
    if not perf:
        perf = {
            "symbol": symbol,
            "total_signals": 0,
            "long_signals": 0,
            "short_signals": 0,
            "wins": 0,
            "losses": 0,
            "breakevens": 0,
            "avg_crv": 0.0,
            "win_rate": 0.0
        }
    
    perf["total_signals"] += 1
    if signal["type"] == "LONG":
        perf["long_signals"] += 1
    else:
        perf["short_signals"] += 1
    
    perf["last_signal"] = signal["timestamp"]
    
    # Update average CRV
    total_crv = perf.get("avg_crv", 0) * (perf["total_signals"] - 1) + signal["crv"]
    perf["avg_crv"] = total_crv / perf["total_signals"]
    
    await app.mongodb.performance.update_one(
        {"symbol": symbol},
        {"$set": perf},
        upsert=True
    )

async def broadcast_signal(signal: Dict):
    """Broadcast new signal to all connected WebSocket clients"""
    message = {
        "type": "signal",
        "data": signal
    }
    
    disconnected = []
    for client in websocket_clients:
        try:
            await client.send_json(message)
        except Exception as e:
            logger.error(f"Error broadcasting to client: {e}")
            disconnected.append(client)
    
    # Remove disconnected clients
    for client in disconnected:
        websocket_clients.remove(client)

async def broadcast_candle(symbol: str, candle: Dict):
    """Broadcast candle update to connected clients"""
    message = {
        "type": "candle",
        "symbol": symbol,
        "data": candle
    }
    
    disconnected = []
    for client in websocket_clients:
        try:
            await client.send_json(message)
        except Exception as e:
            disconnected.append(client)
    
    for client in disconnected:
        websocket_clients.remove(client)

# WebSocket endpoint
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket connection for real-time updates"""
    await websocket.accept()
    websocket_clients.append(websocket)
    logger.info(f"WebSocket client connected. Total clients: {len(websocket_clients)}")
    
    try:
        # Send initial data
        await websocket.send_json({
            "type": "connected",
            "message": "Connected to Crypto Scalping Scanner"
        })
        
        # Keep connection alive
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                # Echo back or handle commands
                await websocket.send_json({"type": "pong"})
            except asyncio.TimeoutError:
                # Send ping
                await websocket.send_json({"type": "ping"})
    
    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
    finally:
        if websocket in websocket_clients:
            websocket_clients.remove(websocket)

# REST API Endpoints

@app.get("/")
async def root():
    return {
        "app": "Crypto Scalping Scanner",
        "status": "running",
        "coins_tracked": len(TOP_10_COINS),
        "active_signals": len(active_signals)
    }

@app.get("/api/health")
async def health_check():
    """Lightweight health check endpoint for keepalive pings"""
    return {
        "status": "alive",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "websocket_clients": len(websocket_clients),
        "session_active": scanner.is_trading_session()
    }

@app.get("/api/debug/status")
async def debug_status():
    """
    Diagnostic endpoint: shows whether live market data is flowing from Bitunix
    and the current indicator state per coin. Use this to verify data feed health
    (e.g. on your production host) without needing a signal.
    """
    return {
        "data_feed": feed.status,
        "session_active": scanner.is_trading_session(),
        "current_session": scanner.get_current_session(),
        "active_strategy": scanner.settings.get("active_strategy"),
        "coins": [scanner.debug_snapshot(s) for s in ALL_SYMBOLS],
    }

def _build_source_zip() -> str:
    """Zip the project source (excluding secrets and heavy/generated dirs)."""
    import zipfile
    root = "/app"
    zip_path = "/tmp/krypto_alert_source.zip"
    exclude_dirs = {".git", ".emergent", "node_modules", "__pycache__",
                    "build", "venv", ".venv", "test_reports", ".pytest_cache"}
    exclude_files = {".env"}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for base, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in exclude_dirs]
            for f in files:
                if f in exclude_files or f.endswith((".pyc", ".zip", ".log")):
                    continue
                full = os.path.join(base, f)
                arc = os.path.relpath(full, root)
                try:
                    zf.write(full, arcname=os.path.join("Krypto_Alert", arc))
                except OSError:
                    pass
    return zip_path


@app.get("/api/download-source")
async def download_source():
    """Download the full project source as a ZIP (no .env/secrets, no node_modules)."""
    from fastapi.responses import FileResponse
    path = _build_source_zip()
    return FileResponse(path, media_type="application/zip", filename="Krypto_Alert.zip")


@app.get("/api/coins")
async def get_coins():
    """Get list of tracked instruments, grouped."""
    return {
        "coins": TOP_10_COINS,
        "groups": [
            {"name": "TOP 10 COINS", "symbols": TOP_10_COINS},
            {"name": "OTHER", "symbols": [
                {"symbol": i["symbol"], "name": i["name"]} for i in OTHER_INSTRUMENTS
            ]},
        ],
    }

@app.get("/api/signals")
async def get_signals(limit: int = 50):
    """Get recent signals"""
    signals = await app.mongodb.signals.find().sort("timestamp", -1).limit(limit).to_list(limit)
    
    # Convert ObjectId to string
    for signal in signals:
        signal["_id"] = str(signal["_id"])
    
    return {"signals": signals}

@app.get("/api/signals/{symbol}")
async def get_symbol_signals(symbol: str, limit: int = 20):
    """Get signals for a specific symbol"""
    signals = await app.mongodb.signals.find({"symbol": symbol}).sort("timestamp", -1).limit(limit).to_list(limit)
    
    for signal in signals:
        signal["_id"] = str(signal["_id"])
    
    return {"symbol": symbol, "signals": signals}

@app.get("/api/performance")
async def get_performance():
    """Get performance stats for all coins"""
    performance = await app.mongodb.performance.find().to_list(100)
    
    for perf in performance:
        perf["_id"] = str(perf["_id"])
    
    # Sort by total signals
    performance.sort(key=lambda x: x.get("total_signals", 0), reverse=True)
    
    return {"performance": performance}

@app.get("/api/performance/{symbol}")
async def get_symbol_performance(symbol: str):
    """Get performance for a specific symbol"""
    perf = await app.mongodb.performance.find_one({"symbol": symbol})
    
    if not perf:
        raise HTTPException(status_code=404, detail="Symbol not found")
    
    perf["_id"] = str(perf["_id"])
    return perf

@app.post("/api/telegram/test")
async def test_telegram():
    """Test Telegram bot connection"""
    if not telegram.bot:
        raise HTTPException(status_code=400, detail="Telegram not configured")
    
    result = await telegram.send_test_message()
    
    if result:
        return {"status": "success", "message": "Test message sent"}
    else:
        raise HTTPException(status_code=500, detail="Failed to send test message")

@app.get("/api/session/status")
async def get_session_status():
    """Get current trading session status"""
    is_active = scanner.is_trading_session()
    now = datetime.now(timezone.utc)
    
    return {
        "is_active": is_active,
        "current_session": scanner.get_current_session(),
        "custom_sessions": scanner.settings.get("custom_sessions", []),
        "pre_signal_enabled": scanner.settings.get("pre_signal_enabled", True),
        "current_time_utc": now.isoformat(),
    }

@app.get("/api/settings")
async def get_settings():
    """Get scanner settings"""
    return scanner.settings

@app.get("/api/strategies")
async def get_strategies():
    """List all available strategies with their parameters"""
    strategies_with_current_params = []
    for strategy_meta in strategy_registry.list_all():
        strategy_id = strategy_meta["id"]
        strategy = strategy_registry.get(strategy_id)
        current_params = strategy.get_params(scanner.settings)
        strategies_with_current_params.append({
            **strategy_meta,
            "current_params": current_params
        })
    
    return {
        "strategies": strategies_with_current_params,
        "active": scanner.settings.get("active_strategy", "scalping_4_rules")
    }

@app.post("/api/settings")
async def update_settings(settings: Dict):
    """Update scanner settings (custom sessions, pre-signals, active strategy)"""
    scanner.update_settings(settings)
    
    # Persist to MongoDB
    await app.mongodb.settings.update_one(
        {"_id": "scanner_settings"},
        {"$set": scanner.settings},
        upsert=True
    )
    
    return {"status": "success", "settings": scanner.settings}

@app.get("/api/analytics/time-based/{symbol}")
async def get_time_based_analytics(symbol: str):
    """Get time-based performance for a specific coin (best hours/days)"""
    # Aggregate signals by hour and weekday
    pipeline = [
        {"$match": {"symbol": symbol}},
        {
            "$group": {
                "_id": {"hour": "$hour", "weekday": "$weekday"},
                "total": {"$sum": 1},
                "long_signals": {"$sum": {"$cond": [{"$eq": ["$type", "LONG"]}, 1, 0]}},
                "short_signals": {"$sum": {"$cond": [{"$eq": ["$type", "SHORT"]}, 1, 0]}},
                "wins": {"$sum": {"$cond": [{"$eq": ["$result", "win"]}, 1, 0]}},
                "losses": {"$sum": {"$cond": [{"$eq": ["$result", "loss"]}, 1, 0]}},
                "avg_crv": {"$avg": "$crv"}
            }
        },
        {"$sort": {"total": -1}}
    ]
    
    results = await app.mongodb.signals.aggregate(pipeline).to_list(1000)
    
    weekday_names = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    
    time_stats = []
    for r in results:
        hour = r["_id"]["hour"]
        weekday = r["_id"]["weekday"]
        total = r["total"]
        wins = r.get("wins", 0)
        win_rate = (wins / total * 100) if total > 0 else 0
        
        time_stats.append({
            "hour": hour,
            "weekday": weekday_names[weekday],
            "total_signals": total,
            "long_signals": r.get("long_signals", 0),
            "short_signals": r.get("short_signals", 0),
            "wins": wins,
            "losses": r.get("losses", 0),
            "win_rate": round(win_rate, 1),
            "avg_crv": round(r.get("avg_crv", 0) or 0, 2)
        })
    
    return {
        "symbol": symbol,
        "time_analytics": time_stats,
        "best_hours": sorted(time_stats, key=lambda x: x["win_rate"], reverse=True)[:5],
        "worst_hours": sorted(time_stats, key=lambda x: x["win_rate"])[:5]
    }

@app.get("/api/analytics/heatmap")
async def get_heatmap_analytics():
    """Get overall heatmap of signals by hour and weekday for all coins"""
    pipeline = [
        {
            "$group": {
                "_id": {"hour": "$hour", "weekday": "$weekday"},
                "total": {"$sum": 1},
                "symbols": {"$addToSet": "$symbol"}
            }
        }
    ]
    
    results = await app.mongodb.signals.aggregate(pipeline).to_list(1000)
    
    heatmap = []
    for r in results:
        heatmap.append({
            "hour": r["_id"]["hour"],
            "weekday": r["_id"]["weekday"],
            "count": r["total"],
            "symbols_count": len(r["symbols"])
        })
    
    return {"heatmap": heatmap}

@app.post("/api/signal/mark-result")
async def mark_signal_result(data: Dict):
    """Mark a signal as win/loss/breakeven for performance tracking"""
    signal_id = data.get("signal_id")
    result = data.get("result")  # win, loss, breakeven
    
    if not signal_id or result not in ["win", "loss", "breakeven"]:
        raise HTTPException(status_code=400, detail="Invalid request")
    
    from bson import ObjectId
    await app.mongodb.signals.update_one(
        {"_id": ObjectId(signal_id)},
        {"$set": {"result": result}}
    )
    
    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
