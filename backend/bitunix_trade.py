"""
Bitunix futures trading: request signer, live REST client, paper broker,
and an AutoTradeManager that opens/manages auto-trades with dynamic SL/TP,
partial TP1 and break-even logic.

Fix summary (Bitunix Live-Trading):
- Root cause #1: The app used the internal short names GOLD / SILVER / OIL as
  order symbols. Bitunix does not know these symbols and rejected the order
  with code 300105 "System error".
      GOLD   -> XAUUSDT
      SILVER -> XAGUSDT
      OIL    -> CLUSDT
- Root cause #2 (code 30027 "TP price must be greater than mark price"):
  Bitunix returns `basePrecision` / `quotePrecision` as the NUMBER OF DECIMAL
  PLACES (e.g. AVAXUSDT quotePrecision = 3 means tick 0.001). The old code
  treated those integers as the tick STEP itself, so 6.716963 was rounded
  DOWN to a multiple of 3.0 -> 6.0, which is below the mark price and
  produces code 30027. Fix: convert decimals to step via 10^(-decimals).
- Root cause #3: For MARKET orders SL/TP were calculated from the stale
  `signal.entry_price`. If the market moved between signal generation and
  order submit, TP could end up on the wrong side of the current mark.
  Fix: fetch the live mark price right before submitting a MARKET order,
  re-derive SL/TP against the mark, and enforce a minimum distance buffer
  so Bitunix cannot reject with code 30027 / 30028.
- `on_signal` never persists a local trade if the live order was rejected.
"""
import os
import time
import json
import hashlib
import logging
import aiohttp
from decimal import Decimal, ROUND_DOWN
from datetime import datetime, timezone
from typing import Dict, List, Optional
from services.technical_indicators import TechnicalIndicators

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Symbol mapping: internal display name -> real Bitunix contract symbol.
# Crypto symbols already match (e.g. BTCUSDT, XRPUSDT) and pass through
# unchanged. Only commodities need a translation.
# ---------------------------------------------------------------------------
SYMBOL_MAP: Dict[str, str] = {
    "GOLD": "XAUUSDT",
    "SILVER": "XAGUSDT",
    "OIL": "CLUSDT",
}


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


def _round_step(value: float, step: float) -> str:
    """Round `value` DOWN to a multiple of `step` and return a plain string
    (no scientific notation, no trailing zeros beyond the step precision)."""
    if step <= 0:
        return f"{value}"
    d_val = Decimal(str(value))
    d_step = Decimal(str(step))
    quant = (d_val / d_step).to_integral_value(rounding=ROUND_DOWN) * d_step
    # Force the string to carry the same number of fractional digits as `step`
    # so tiny values (e.g. 6.717) don't get normalized to "6" and land below
    # the mark price -> Bitunix code 30027. Whole-unit steps (>=1) keep no
    # trailing zeros so integer quantities look like "149" not "149.0".
    step_exp = -d_step.as_tuple().exponent
    if d_step >= 1:
        s = format(quant.to_integral_value(), "f")
    elif step_exp > 0:
        q = quant.quantize(Decimal(10) ** -step_exp)
        s = format(q, "f")
    else:
        s = format(quant, "f")
    return s if s else "0"


def _round_step_up(value: float, step: float) -> str:
    """Same as `_round_step` but rounds UP (used for SHORT TP so the TP still
    ends up strictly below the mark price after rounding)."""
    from decimal import ROUND_UP
    if step <= 0:
        return f"{value}"
    d_val = Decimal(str(value))
    d_step = Decimal(str(step))
    quant = (d_val / d_step).to_integral_value(rounding=ROUND_UP) * d_step
    step_exp = -d_step.as_tuple().exponent
    if d_step >= 1:
        s = format(quant.to_integral_value(), "f")
    elif step_exp > 0:
        q = quant.quantize(Decimal(10) ** -step_exp)
        s = format(q, "f")
    else:
        s = format(quant, "f")
    return s if s else "0"


def _precision_to_step(precision) -> float:
    """Bitunix returns basePrecision / quotePrecision as the NUMBER OF
    DECIMAL PLACES (integer), not as a tick step. Convert defensively:
      3          -> 0.001
      "0.001"    -> 0.001   (already a step)
      0          -> 1       (whole units)
    """
    if precision is None:
        return 0.0
    try:
        p = float(precision)
    except (TypeError, ValueError):
        return 0.0
    if p == 0:
        return 1.0
    # A value <= 0 or already smaller than 1 is treated as a real step size.
    if 0 < p < 1:
        return p
    # Positive integer -> decimals count -> 10^-p
    if p == int(p) and 0 < p <= 12:
        return 10 ** (-int(p))
    return p


class BitunixTradeClient:
    """Live Bitunix USDT-M futures client (signed private endpoints).

    Owns the symbol translation layer + a cache of contract metadata
    (step size, tick size, min qty) loaded from the public
    /api/v1/futures/market/trading_pairs endpoint.
    """

    def __init__(self):
        # Support both naming conventions (Render uses BITUNIX_SECRET_KEY)
        self.api_key = os.getenv("BITUNIX_API_KEY") or os.getenv("BITUNIX_KEY", "")
        self.secret = (os.getenv("BITUNIX_API_SECRET")
                       or os.getenv("BITUNIX_SECRET_KEY")
                       or os.getenv("BITUNIX_SECRET", ""))
        self.base = os.getenv("BITUNIX_BASE_URL", "https://fapi.bitunix.com").rstrip("/")

        # contract metadata: bitunix_symbol -> {"qty_step", "price_tick", "min_qty"}
        self._pairs_meta: Dict[str, Dict[str, float]] = {}
        self._valid_bitunix_symbols: set = set()

    def configured(self) -> bool:
        return bool(self.api_key and self.secret)

    # --------------------- symbol translation ----------------------------
    def to_bitunix_symbol(self, internal: str) -> str:
        """Translate the app-internal symbol (e.g. GOLD) to the real Bitunix
        contract symbol (e.g. XAUUSDT). Crypto symbols pass through unchanged."""
        if not internal:
            return internal
        s = internal.upper()
        mapped = SYMBOL_MAP.get(s, s)
        # If we already know the pair catalogue and the mapped symbol isn't in
        # it, log a warning so the mismatch shows up in the logs instead of
        # silently ending up as code 300105 "System error".
        if self._valid_bitunix_symbols and mapped not in self._valid_bitunix_symbols:
            logger.warning(
                f"Bitunix symbol '{mapped}' (from internal '{internal}') is not "
                "listed in trading_pairs; order will likely be rejected."
            )
        return mapped

    def contract_meta(self, bitunix_symbol: str) -> Dict[str, float]:
        return self._pairs_meta.get(bitunix_symbol, {})

    async def load_trading_pairs(self) -> None:
        """Load the public trading-pair catalogue and cache step/tick/min-qty.
        Called once at startup; safe to re-call. Never raises to the caller."""
        url = f"{self.base}/api/v1/futures/market/trading_pairs"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, timeout=aiohttp.ClientTimeout(total=15)) as r:
                    payload = await r.json()
        except Exception as e:
            logger.error(f"load_trading_pairs failed: {e}")
            return

        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            logger.warning(f"trading_pairs unexpected payload: {str(payload)[:200]}")
            return

        meta: Dict[str, Dict[str, float]] = {}
        valid: set = set()
        for row in data:
            sym = row.get("symbol")
            if not sym:
                continue
            valid.add(sym)
            try:
                # Bitunix returns basePrecision / quotePrecision as the NUMBER
                # OF DECIMAL PLACES (integer), NOT as a tick step. Convert.
                qty_step = _precision_to_step(row.get("basePrecision"))
                price_tick = _precision_to_step(
                    row.get("quotePrecision") or row.get("pricePrecision")
                )
                min_qty = float(row.get("minTradeVolume") or 0) or 0.0
                # If minTradeVolume is provided and is bigger than the derived
                # step (e.g. AVAX minTradeVolume=1 but basePrecision=0 -> step
                # would be 1 too), keep whichever is stricter.
                if min_qty and (not qty_step or min_qty > qty_step):
                    qty_step = min_qty
                meta[sym] = {
                    "qty_step": qty_step,
                    "price_tick": price_tick,
                    "min_qty": min_qty,
                }
            except (TypeError, ValueError):
                meta[sym] = {}
        self._pairs_meta = meta
        self._valid_bitunix_symbols = valid
        logger.info(f"Bitunix trading_pairs cached: {len(valid)} symbols")

        # Sanity check the internal -> bitunix mapping now that we have data.
        for internal, mapped in SYMBOL_MAP.items():
            if mapped not in valid:
                logger.error(
                    f"Symbol mapping mismatch: internal '{internal}' -> '{mapped}' "
                    "is NOT a valid Bitunix contract. Live orders will fail."
                )

    # --------------------- signed transport ------------------------------
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

    # --------------------- public API ------------------------------------
    def _fmt_qty(self, bitunix_symbol: str, qty: float) -> str:
        m = self._pairs_meta.get(bitunix_symbol) or {}
        step = m.get("qty_step") or 0.0
        if step > 0:
            return _round_step(qty, step)
        return f"{qty}"

    def _fmt_price(self, bitunix_symbol: str, price: float, direction: str = "down") -> str:
        """Round price to tick. `direction`:
           - "down": floor to tick (safe for LONG SL, SHORT TP)
           - "up":   ceil to tick  (safe for LONG TP, SHORT SL)
        """
        m = self._pairs_meta.get(bitunix_symbol) or {}
        tick = m.get("price_tick") or 0.0
        if tick > 0:
            if direction == "up":
                return _round_step_up(price, tick)
            return _round_step(price, tick)
        return f"{price}"

    def price_tick(self, bitunix_symbol: str) -> float:
        m = self._pairs_meta.get(bitunix_symbol) or {}
        return float(m.get("price_tick") or 0.0)

    async def get_mark_price(self, symbol: str) -> Optional[float]:
        """Fetch current mark price (public endpoint, no auth). Returns None on
        failure so the caller can decide to abort/adjust."""
        b_symbol = self.to_bitunix_symbol(symbol)
        url = f"{self.base}/api/v1/futures/market/tickers"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, params={"symbols": b_symbol},
                                 timeout=aiohttp.ClientTimeout(total=10)) as r:
                    payload = await r.json()
        except Exception as e:
            logger.error(f"get_mark_price({symbol}) failed: {e}")
            return None
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or not data:
            return None
        try:
            return float(data[0].get("markPrice") or data[0].get("lastPrice") or 0) or None
        except (TypeError, ValueError):
            return None

    async def place_order(self, symbol, side, qty, order_type="MARKET", price=None,
                          tp_price=None, sl_price=None, reduce_only=False,
                          position_side: str = "LONG"):
        b_symbol = self.to_bitunix_symbol(symbol)
        body = {"symbol": b_symbol, "qty": self._fmt_qty(b_symbol, qty), "side": side,
                "tradeSide": "OPEN", "orderType": order_type}
        if order_type == "LIMIT" and price:
            body["price"] = self._fmt_price(b_symbol, price, "down")
        # For LONG (BUY): TP must be > mark, SL must be < mark
        # For SHORT (SELL): TP must be < mark, SL must be > mark
        # -> round in the SAFE direction so tick rounding never flips the side.
        is_long = (position_side or "").upper() == "LONG" or side == "BUY"
        tp_dir = "up" if is_long else "down"
        sl_dir = "down" if is_long else "up"
        if tp_price:
            body.update({"tpPrice": self._fmt_price(b_symbol, tp_price, tp_dir),
                         "tpStopType": "MARK_PRICE", "tpOrderType": "MARKET"})
        if sl_price:
            body.update({"slPrice": self._fmt_price(b_symbol, sl_price, sl_dir),
                         "slStopType": "MARK_PRICE", "slOrderType": "MARKET"})
        if reduce_only:
            body["reduceOnly"] = True
        return await self._post("/api/v1/futures/trade/place_order", body)

    async def flash_close(self, symbol, position_id, side, qty):
        b_symbol = self.to_bitunix_symbol(symbol)
        order_side = "SELL" if side == "LONG" else "BUY"
        body = {"symbol": b_symbol, "qty": self._fmt_qty(b_symbol, qty),
                "side": order_side, "tradeSide": "CLOSE", "orderType": "MARKET",
                "positionId": position_id, "reduceOnly": True}
        return await self._post("/api/v1/futures/trade/place_order", body)

    async def set_leverage(self, symbol, leverage, margin_mode="ISOLATION"):
        b_symbol = self.to_bitunix_symbol(symbol)
        return await self._post("/api/v1/futures/account/change_leverage",
                                {"symbol": b_symbol, "leverage": int(leverage),
                                 "marginCoin": "USDT"})

    async def get_positions(self, symbol=None):
        q = {"symbol": self.to_bitunix_symbol(symbol)} if symbol else {}
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
        self.telegram = None  # optional TelegramNotifier for reject alerts
        self.config = {"mode": "paper", "coins": {}}

    def set_db(self, db):
        self.db = db

    def set_telegram(self, telegram):
        self.telegram = telegram

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
            sl = entry - buffer if side == "LONG" else entry + buffer
        elif mode == "structure" and candles:
            lookback = int(cfg["sl_lookback"])
            tick = entry * 0.0001
            ticks = int(cfg["sl_ticks"])
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

    async def _notify_reject(self, symbol: str, side: str, reason: str) -> None:
        if not self.telegram:
            return
        try:
            await self.telegram.send_rejection(symbol, side, reason)
        except Exception as e:
            logger.error(f"telegram reject notify failed: {e}")

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

        mode = self.config.get("mode", "paper")

        # ---- LIVE MODE: hit the exchange FIRST; only persist on success ----
        if mode == "live" and self.client.configured():
            try:
                await self.client.set_leverage(symbol, cfg["leverage"], cfg["margin_mode"])

                # ---- CRITICAL: re-derive SL/TP against the LIVE mark price ----
                # For MARKET orders Bitunix validates tpPrice/slPrice against
                # the current mark price at submit time. A stale signal entry
                # can put TP on the wrong side of the mark -> code 30027.
                mark = await self.client.get_mark_price(symbol)
                b_sym = self.client.to_bitunix_symbol(symbol)
                tick = self.client.price_tick(b_sym) or (entry * 0.0001)
                # Minimum safety distance between TP/SL and mark. Two ticks or
                # 0.05 % of the mark, whichever is bigger.
                min_gap_pct = 0.0005  # 0.05 %
                if mark and mark > 0:
                    # Re-anchor to the live mark for MARKET orders (paper stays on entry).
                    order_type = str(cfg.get("order_type", "MARKET")).upper()
                    if order_type == "MARKET":
                        entry_live = mark
                        # Preserve the original risk shape (SL/TP distance vs entry)
                        # but shift the anchor onto the live mark price.
                        sl_dist = abs(entry - sl)
                        tp1_dist = abs(tp1 - entry)
                        tpf_dist = abs(tpf - entry)
                        if side == "LONG":
                            sl = entry_live - sl_dist
                            tp1 = entry_live + tp1_dist
                            tpf = entry_live + tpf_dist
                        else:
                            sl = entry_live + sl_dist
                            tp1 = entry_live - tp1_dist
                            tpf = entry_live - tpf_dist
                        entry = entry_live

                    min_gap = max(tick * 2, mark * min_gap_pct)
                    if side == "LONG":
                        # TP must be strictly > mark, SL strictly < mark.
                        if tpf <= mark + min_gap:
                            tpf = mark + min_gap
                        if tp1 <= mark + min_gap:
                            tp1 = mark + min_gap
                        if sl >= mark - min_gap:
                            sl = mark - min_gap
                    else:  # SHORT
                        if tpf >= mark - min_gap:
                            tpf = mark - min_gap
                        if tp1 >= mark - min_gap:
                            tp1 = mark - min_gap
                        if sl <= mark + min_gap:
                            sl = mark + min_gap
                    logger.info(
                        f"Live SL/TP adjusted for {symbol} side={side} mark={mark} "
                        f"-> entry={entry} sl={sl} tp1={tp1} tpf={tpf}"
                    )
                else:
                    logger.warning(f"No mark price for {symbol}; sending original SL/TP")

                side_order = "BUY" if side == "LONG" else "SELL"
                res = await self.client.place_order(symbol, side_order, qty,
                                                    order_type=cfg["order_type"],
                                                    tp_price=tpf, sl_price=sl,
                                                    position_side=side)
            except Exception as e:
                reason = f"exception: {str(e)[:160]}"
                logger.error(f"Live order EXCEPTION {symbol}: {e}")
                await self._notify_reject(symbol, side, reason)
                return None

            ok = isinstance(res, dict) and res.get("code") == 0
            order_id = (res.get("data") or {}).get("orderId") if isinstance(res, dict) else None
            if not ok or not order_id:
                reason = (isinstance(res, dict) and (res.get("msg") or str(res))) or "unknown error"
                code = isinstance(res, dict) and res.get("code")
                logger.error(f"Live order REJECTED {symbol} side={side} qty={qty} "
                             f"code={code} msg={reason}")
                await self._notify_reject(symbol, side, f"code {code}: {reason}")
                # No local persistence -> no ghost position.
                return None

            trade_extra = {"bitunix_order_id": order_id, "bitunix_response": res}
        else:
            trade_extra = {"bitunix_order_id": None}

        trade = {
            "id": f"{symbol}-{int(time.time()*1000)}",
            "symbol": symbol, "side": side, "mode": mode,
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
            "events": [f"OPEN {side} @ {entry}"],
            **trade_extra,
        }

        await self.db.auto_trades.insert_one(dict(trade))
        logger.info(f"AutoTrade OPEN {side} {symbol} qty={qty} entry={entry} mode={mode}")
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
