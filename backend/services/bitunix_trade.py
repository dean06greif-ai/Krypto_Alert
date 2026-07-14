"""
Bitunix futures trading: request signer, live REST client, paper broker,
and an AutoTradeManager that opens/manages auto-trades with dynamic SL/TP,
partial TP1 and break-even logic.
"""
import os
import time
import json
import hashlib
import logging
import aiohttp
from datetime import datetime, timezone
from typing import Dict, List, Optional
from services.technical_indicators import TechnicalIndicators

logger = logging.getLogger(__name__)


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _nonce() -> str:
    return os.urandom(16).hex()


def _millis() -> str:
    return str(int(time.time() * 1000))


def sign_request(api_key: str, secret: str, query: Optional[Dict], body_str: str,
                 nonce: str, ts: str) -> str:
    qp = ""
    if query:
        qp = "".join(f"{k}{query[k]}" for k in sorted(query.keys()))
    digest = _sha256(nonce + ts + api_key + qp + body_str)
    return _sha256(digest + secret)


class BitunixTradeClient:
    """Live Bitunix USDT-M futures client (signed private endpoints)."""

    def __init__(self):
        self.api_key = os.getenv("BITUNIX_API_KEY", "")
        self.secret = os.getenv("BITUNIX_API_SECRET", "")
        self.base = os.getenv("BITUNIX_BASE_URL", "https://fapi.bitunix.com").rstrip("/")

    def configured(self) -> bool:
        return bool(self.api_key and self.secret)

    async def _post(self, path: str, body: Dict) -> Dict:
        body_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
        nonce, ts = _nonce(), _millis()
        sign = sign_request(self.api_key, self.secret, None, body_str, nonce, ts)
        headers = {"api-key": self.api_key, "nonce": nonce, "timestamp": ts,
                   "sign": sign, "language": "en-US", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as s:
            async with s.post(self.base + path, data=body_str, headers=headers,
                              timeout=aiohttp.ClientTimeout(total=15)) as r:
                txt = await r.text()
                try:
                    return json.loads(txt)
                except Exception:
                    return {"code": r.status, "msg": txt[:200]}

    async def _get(self, path: str, query: Dict = None) -> Dict:
        query = query or {}
        nonce, ts = _nonce(), _millis()
        sign = sign_request(self.api_key, self.secret, query, "", nonce, ts)
        headers = {"api-key": self.api_key, "nonce": nonce, "timestamp": ts,
                   "sign": sign, "language": "en-US"}
        async with aiohttp.ClientSession() as s:
            async with s.get(self.base + path, params=query, headers=headers,
                             timeout=aiohttp.ClientTimeout(total=15)) as r:
                txt = await r.text()
                try:
                    return json.loads(txt)
                except Exception:
                    return {"code": r.status, "msg": txt[:200]}

    async def place_order(self, symbol, side, qty, order_type="MARKET", price=None,
                          tp_price=None, sl_price=None, reduce_only=False):
        body = {"symbol": symbol, "qty": str(qty), "side": side,
                "tradeSide": "OPEN", "orderType": order_type}
        if order_type == "LIMIT" and price:
            body["price"] = str(price)
        if tp_price:
            body.update({"tpPrice": str(tp_price), "tpStopType": "MARK_PRICE",
                         "tpOrderType": "MARKET"})
        if sl_price:
            body.update({"slPrice": str(sl_price), "slStopType": "MARK_PRICE",
                         "slOrderType": "MARKET"})
        if reduce_only:
            body["reduceOnly"] = True
        return await self._post("/api/v1/futures/trade/place_order", body)

    async def flash_close(self, symbol, position_id, side, qty):
        order_side = "SELL" if side == "LONG" else "BUY"
        body = {"symbol": symbol, "qty": str(qty), "side": order_side,
                "tradeSide": "CLOSE", "orderType": "MARKET",
                "positionId": position_id, "reduceOnly": True}
        return await self._post("/api/v1/futures/trade/place_order", body)

    async def set_leverage(self, symbol, leverage, margin_mode="ISOLATION"):
        return await self._post("/api/v1/futures/account/change_leverage",
                                {"symbol": symbol, "leverage": int(leverage),
                                 "marginCoin": "USDT"})

    async def get_positions(self, symbol=None):
        q = {"symbol": symbol} if symbol else {}
        return await self._get("/api/v1/futures/position/get_pending_positions", q)

    async def get_balance(self):
        return await self._get("/api/v1/futures/account", {"marginCoin": "USDT"})


DEFAULT_COIN_CFG = {
    "enabled": False,
    "max_capital": 100.0,
    "leverage": 10,
    "margin_mode": "ISOLATION",
    "order_type": "MARKET",
    "sl_mode": "structure",       # structure | fixed | atr
    "sl_fixed_percent": 1.0,
    "sl_ticks": 4,
    "sl_lookback": 10,
    "atr_period": 14,
    "atr_sl_multiplier": 1.2,     # ATR buffer beyond structure (anti stop-hunt)
    "tp1_crv": 1.0,
    "tp1_close_percent": 50,
    "tp_full_crv": 2.0,
    "breakeven_enabled": True,
    "trail_after_tp1": True,      # ATR trailing stop after TP1 -> let winners run
    "trail_atr_mult": 1.5,
    "fee_percent": 0.06,
    "trade_pre_signals": False,
}


class AutoTradeManager:
    """
    Opens & manages auto-trades. In paper mode everything is simulated in Mongo.
    In live mode it calls Bitunix. Dynamic SL/TP, partial TP1 + break-even.
    """

    def __init__(self, client: BitunixTradeClient):
        self.client = client
        self.db = None
        self.config = {"mode": "paper", "coins": {}}

    def set_db(self, db):
        self.db = db

    def set_config(self, config: Dict):
        self.config = {"mode": config.get("mode", "paper"),
                       "coins": config.get("coins", {})}

    def coin_cfg(self, symbol: str) -> Dict:
        c = dict(DEFAULT_COIN_CFG)
        c.update(self.config.get("coins", {}).get(symbol, {}))
        return c

    def is_enabled(self, symbol: str) -> bool:
        return self.coin_cfg(symbol).get("enabled", False)

    def _levels(self, cfg, side, entry, candles, indicators):
        # Volatility (ATR) drives a dynamic, noise-aware stop.
        atr = 0.0
        if candles and len(candles) > int(cfg.get("atr_period", 14)) + 1:
            atr_arr = TechnicalIndicators.calculate_atr(candles, int(cfg.get("atr_period", 14)))
            atr = atr_arr[-1] or 0.0
        atr_mult = float(cfg.get("atr_sl_multiplier", 1.2))
        buffer = atr * atr_mult

        mode = cfg.get("sl_mode", "structure")
        if mode == "atr" and atr > 0:
            # pure ATR stop from entry
            sl = entry - buffer if side == "LONG" else entry + buffer
        elif mode == "structure" and candles:
            lookback = int(cfg["sl_lookback"])
            tick = entry * 0.0001
            ticks = int(cfg["sl_ticks"])
            # structure level + ATR buffer beyond it (protects vs. liquidity sweeps)
            struct_buffer = (buffer if buffer > 0 else ticks * tick)
            if side == "LONG":
                low = min(c["low"] for c in candles[-lookback:])
                sl = low - struct_buffer
            else:
                high = max(c["high"] for c in candles[-lookback:])
                sl = high + struct_buffer
        else:
            pct = float(cfg["sl_fixed_percent"]) / 100
            sl = entry * (1 - pct) if side == "LONG" else entry * (1 + pct)
        risk = abs(entry - sl)
        if risk <= 0:
            risk = entry * 0.003
            sl = entry - risk if side == "LONG" else entry + risk
        if side == "LONG":
            tp1 = entry + risk * cfg["tp1_crv"]
            tpf = entry + risk * cfg["tp_full_crv"]
        else:
            tp1 = entry - risk * cfg["tp1_crv"]
            tpf = entry - risk * cfg["tp_full_crv"]
        return round(sl, 6), round(tp1, 6), round(tpf, 6), risk, round(atr, 6)

    async def on_signal(self, signal: Dict, candles: List[Dict]) -> Optional[Dict]:
        symbol = signal["symbol"]
        cfg = self.coin_cfg(symbol)
        if not cfg["enabled"]:
            return None
        if signal.get("signal_class") == "PRE_SIGNAL" and not cfg["trade_pre_signals"]:
            return None
        # only one open trade per symbol
        existing = await self.db.auto_trades.find_one({"symbol": symbol, "status": "open"})
        if existing:
            return None

        side = signal["type"]
        entry = float(signal.get("entry_price") or 0)
        if entry <= 0:
            return None
        sl, tp1, tpf, risk, atr = self._levels(cfg, side, entry, candles, signal)
        qty = round((float(cfg["max_capital"]) * float(cfg["leverage"])) / entry, 6)

        trade = {
            "id": f"{symbol}-{int(time.time()*1000)}",
            "symbol": symbol, "side": side, "mode": self.config.get("mode", "paper"),
            "entry": entry, "sl": sl, "tp1": tp1, "tpf": tpf, "initial_sl": sl,
            "atr": atr,
            "qty": qty, "qty_remaining": qty, "risk": round(risk, 6),
            "tp1_crv": cfg["tp1_crv"], "tp_full_crv": cfg["tp_full_crv"],
            "tp1_close_percent": cfg["tp1_close_percent"],
            "breakeven_enabled": cfg["breakeven_enabled"], "fee_percent": cfg["fee_percent"],
            "leverage": cfg["leverage"], "max_capital": cfg["max_capital"],
            "status": "open", "tp1_hit": False, "breakeven_moved": False,
            "realized_pnl": 0.0, "strategy_id": signal.get("strategy_id"),
            "strategy_name": signal.get("strategy_name"),
            "opened_at": datetime.now(timezone.utc).isoformat(),
            "trade_date": signal.get("trade_date"),
            "bitunix_order_id": None, "events": [f"OPEN {side} @ {entry}"],
        }

        if self.config.get("mode") == "live" and self.client.configured():
            try:
                await self.client.set_leverage(symbol, cfg["leverage"], cfg["margin_mode"])
                side_order = "BUY" if side == "LONG" else "SELL"
                res = await self.client.place_order(symbol, side_order, qty,
                                                    order_type=cfg["order_type"],
                                                    tp_price=tpf, sl_price=sl)
                trade["bitunix_response"] = res
                if isinstance(res, dict) and res.get("code") == 0:
                    trade["bitunix_order_id"] = (res.get("data") or {}).get("orderId")
                else:
                    trade["events"].append(f"LIVE ERROR: {str(res)[:120]}")
            except Exception as e:
                trade["events"].append(f"LIVE EXCEPTION: {str(e)[:120]}")
                logger.error(f"Live order failed {symbol}: {e}")

        await self.db.auto_trades.insert_one(dict(trade))
        logger.info(f"AutoTrade OPEN {side} {symbol} qty={qty} entry={entry} mode={trade['mode']}")
        trade.pop("_id", None)
        return trade

    async def monitor(self, prices: Dict[str, float]):
        """Called periodically. Manage open trades against live prices."""
        if self.db is None:
            return
        cursor = self.db.auto_trades.find({"status": "open"})
        async for t in cursor:
            symbol = t["symbol"]
            price = prices.get(symbol)
            if not price:
                continue
            await self._manage_trade(t, price)

    async def _manage_trade(self, t: Dict, price: float):
        side = t["side"]
        updates = {}
        events = list(t.get("events", []))
        realized = t.get("realized_pnl", 0.0)
        qty_rem = t.get("qty_remaining", t["qty"])
        closed = False
        exit_price = None
        result = None

        def pnl(qty, exit_p):
            return (exit_p - t["entry"]) * qty if side == "LONG" else (t["entry"] - exit_p) * qty

        hit_tp1 = (price >= t["tp1"]) if side == "LONG" else (price <= t["tp1"])
        hit_tpf = (price >= t["tpf"]) if side == "LONG" else (price <= t["tpf"])
        hit_sl = (price <= t["sl"]) if side == "LONG" else (price >= t["sl"])

        # TP1 partial + break-even
        if not t.get("tp1_hit") and hit_tp1 and not hit_tpf:
            close_qty = round(t["qty"] * t["tp1_close_percent"] / 100, 6)
            realized += pnl(close_qty, t["tp1"])
            qty_rem = round(qty_rem - close_qty, 6)
            events.append(f"TP1 hit @ {t['tp1']} closed {t['tp1_close_percent']}%")
            updates["tp1_hit"] = True
            if t.get("breakeven_enabled"):
                fee = t.get("fee_percent", 0.06) / 100
                be = t["entry"] * (1 + 2 * fee) if side == "LONG" else t["entry"] * (1 - 2 * fee)
                updates["sl"] = round(be, 6)
                updates["breakeven_moved"] = True
                events.append(f"SL -> Break-Even @ {round(be,6)}")
            if await self._live_partial_close(t, close_qty):
                pass

        # ATR trailing stop after TP1 -> lock profit while letting the runner breathe
        if (t.get("tp1_hit") or updates.get("tp1_hit")):
            cfg2 = self.coin_cfg(t["symbol"])
            atr = t.get("atr") or 0
            if cfg2.get("trail_after_tp1", True) and atr > 0:
                cur_sl = updates.get("sl", t["sl"])
                mult = float(cfg2.get("trail_atr_mult", 1.5))
                if side == "LONG":
                    new_sl = round(price - atr * mult, 6)
                    if new_sl > cur_sl:
                        updates["sl"] = new_sl
                        events.append(f"TRAIL SL -> {new_sl}")
                else:
                    new_sl = round(price + atr * mult, 6)
                    if new_sl < cur_sl:
                        updates["sl"] = new_sl
                        events.append(f"TRAIL SL -> {new_sl}")

        # Full TP
        if hit_tpf and qty_rem > 0:
            realized += pnl(qty_rem, t["tpf"])
            events.append(f"TP FULL hit @ {t['tpf']}")
            closed, exit_price, result, qty_rem = True, t["tpf"], "win", 0

        # Stop loss (re-read possibly moved SL)
        cur_sl = updates.get("sl", t["sl"])
        hit_sl = (price <= cur_sl) if side == "LONG" else (price >= cur_sl)
        if not closed and hit_sl and qty_rem > 0:
            realized += pnl(qty_rem, cur_sl)
            is_be = t.get("breakeven_moved") or updates.get("breakeven_moved")
            result = "breakeven" if is_be else ("win" if t.get("tp1_hit") or updates.get("tp1_hit") else "loss")
            events.append(f"{'BREAK-EVEN' if is_be else 'STOP'} hit @ {cur_sl}")
            closed, exit_price, qty_rem = True, cur_sl, 0

        updates["realized_pnl"] = round(realized, 6)
        updates["qty_remaining"] = qty_rem
        updates["events"] = events[-20:]
        if closed:
            updates["status"] = "closed"
            updates["exit_price"] = exit_price
            updates["result"] = result
            updates["closed_at"] = datetime.now(timezone.utc).isoformat()
            if t["mode"] == "live" and self.client.configured():
                await self._live_flash_close(t, qty_rem)
            logger.info(f"AutoTrade CLOSE {t['symbol']} {result} pnl={updates['realized_pnl']}")

        await self.db.auto_trades.update_one({"id": t["id"]}, {"$set": updates})

    async def _live_partial_close(self, t, qty):
        if t["mode"] != "live" or not self.client.configured():
            return False
        try:
            await self.client.flash_close(t["symbol"], t.get("bitunix_order_id"), t["side"], qty)
            return True
        except Exception as e:
            logger.error(f"partial close failed: {e}")
            return False

    async def _live_flash_close(self, t, qty):
        try:
            await self.client.flash_close(t["symbol"], t.get("bitunix_order_id"), t["side"], qty or t["qty_remaining"])
        except Exception as e:
            logger.error(f"flash close failed: {e}")

    async def manual_close(self, trade_id: str, price: float):
        t = await self.db.auto_trades.find_one({"id": trade_id, "status": "open"})
        if not t:
            return None
        side = t["side"]
        qty_rem = t.get("qty_remaining", t["qty"])
        pnl = (price - t["entry"]) * qty_rem if side == "LONG" else (t["entry"] - price) * qty_rem
        realized = round(t.get("realized_pnl", 0.0) + pnl, 6)
        result = "win" if realized > 0 else ("breakeven" if realized == 0 else "loss")
        if t["mode"] == "live" and self.client.configured():
            await self._live_flash_close(t, qty_rem)
        await self.db.auto_trades.update_one({"id": trade_id}, {"$set": {
            "status": "closed", "exit_price": price, "result": result,
            "realized_pnl": realized, "qty_remaining": 0,
            "closed_at": datetime.now(timezone.utc).isoformat(),
            "events": (t.get("events", []) + [f"MANUAL CLOSE @ {price}"])[-20:]}})
        return {"result": result, "realized_pnl": realized}
