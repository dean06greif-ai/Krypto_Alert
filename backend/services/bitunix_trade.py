"""
Bitunix futures trading: request signer, live REST client, paper broker,
and an AutoTradeManager that opens/manages auto-trades with dynamic SL/TP,
partial TP1 and break-even logic.

Fix summary (Bitunix Live-Trading):
- Root cause: The app used the internal short names GOLD / SILVER / OIL as
  order symbols. Bitunix does not know these symbols and rejected the order
  with code 300105 "System error".
  The real Bitunix USDT-M futures contracts are:
      GOLD   -> XAUUSDT
      SILVER -> XAGUSDT
      OIL    -> CLUSDT
  Crypto symbols like BTCUSDT / XRPUSDT already match.
- Every private call to Bitunix (place_order, flash_close, set_leverage,
  get_positions) now translates the internal symbol via `to_bitunix_symbol()`.
- The mapping is validated at startup against
  GET /api/v1/futures/market/trading_pairs so future contract-name changes
  on Bitunix don't break us silently.
- Second fix: `on_signal` no longer stores the trade locally when the live
  order was rejected. Instead a Telegram alert is emitted and the trade is
  dropped. That prevents "ghost positions" that never existed on Bitunix.
- qty/price are sent as strings, rounded to the contract's step/tick size
  when the metadata is available.
"""
import os
import time
import json
import hashlib
import logging
import aiohttp
from decimal import Decimal, ROUND_DOWN, ROUND_UP, ROUND_HALF_UP
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


def _round_step(value: float, step: float, rounding=ROUND_DOWN) -> str:
    """Round `value` to a multiple of `step` and return a plain string
    (no scientific notation, no trailing zeros beyond the step precision).
    Default rounding is DOWN (used for quantities). For prices we sometimes
    need HALF_EVEN / to-tick alignment; pass a different `rounding` in that
    case."""
    if step <= 0:
        return f"{value}"
    d_val = Decimal(str(value))
    d_step = Decimal(str(step))
    quant = (d_val / d_step).to_integral_value(rounding=rounding) * d_step
    # Preserve step precision explicitly – .normalize() drops trailing zeros
    # which Bitunix sometimes rejects (e.g. "1.11" instead of "1.1100").
    step_exp = d_step.normalize().as_tuple().exponent
    if step_exp < 0:
        quant = quant.quantize(Decimal(10) ** step_exp)
    s = format(quant, "f")
    return s if s else "0"


def _precision_to_step(prec) -> float:
    """Bitunix returns basePrecision / quotePrecision as *decimal places*
    (e.g. 3 -> 0.001). If the value already looks like a step (e.g. 0.001)
    we pass it through. Handles ints, floats and strings safely."""
    if prec is None or prec == "":
        return 0.0
    try:
        f = float(prec)
    except (TypeError, ValueError):
        return 0.0
    if f <= 0:
        return 0.0
    # Heuristic: an integer >= 1 is a decimal-place count, anything < 1 is
    # already a step size.
    if f >= 1 and float(int(f)) == f:
        return float(Decimal(10) ** Decimal(-int(f)))
    return f


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
                meta[sym] = {
                    # basePrecision / quotePrecision are DECIMAL PLACES on
                    # Bitunix, not step sizes. Convert them properly.
                    "qty_step": _precision_to_step(row.get("basePrecision")),
                    "price_tick": _precision_to_step(
                        row.get("quotePrecision") or row.get("pricePrecision")
                    ),
                    "min_qty": float(row.get("minTradeVolume") or 0) or 0.0,
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
        min_qty = m.get("min_qty") or 0.0
        # If the raw qty is already below the exchange minimum, bump it up to
        # the minimum so we don't get code 30016 for a rounded-to-zero amount.
        if min_qty > 0 and qty < min_qty:
            qty = min_qty
        if step > 0:
            rounded = _round_step(qty, step, ROUND_DOWN)
            # After rounding down we might dip below min_qty again; round up
            # to the nearest step in that case.
            try:
                if min_qty > 0 and float(rounded) < min_qty:
                    rounded = _round_step(min_qty, step, ROUND_UP)
            except ValueError:
                pass
            return rounded
        return f"{qty}"

    def _fmt_price(self, bitunix_symbol: str, price: float,
                   direction: str = "nearest") -> str:
        """direction:
            "nearest" -> half-up (default, LIMIT entry)
            "up"      -> ROUND_UP  (LONG TP / SHORT SL – keep away from mark)
            "down"    -> ROUND_DOWN (LONG SL / SHORT TP – keep away from mark)
        """
        m = self._pairs_meta.get(bitunix_symbol) or {}
        tick = m.get("price_tick") or 0.0
        if tick <= 0:
            return f"{price}"
        mode = {"up": ROUND_UP, "down": ROUND_DOWN}.get(direction, ROUND_HALF_UP)
        return _round_step(price, tick, mode)

    async def place_order(self, symbol, side, qty, order_type="MARKET", price=None,
                          tp_price=None, sl_price=None, reduce_only=False):
        b_symbol = self.to_bitunix_symbol(symbol)
        # Direction-aware rounding for TP/SL. For LONG (BUY):
        #   * TP must be ABOVE mark, so round UP to the next tick.
        #   * SL must be BELOW mark, so round DOWN.
        # For SHORT (SELL) it's the opposite. This prevents Bitunix code
        # 30027 ("TP price must be greater than mark price") caused by
        # rounding a marginal TP down onto/below the mark.
        is_long = str(side).upper() in ("BUY", "LONG")
        tp_dir = "up" if is_long else "down"
        sl_dir = "down" if is_long else "up"
        body = {"symbol": b_symbol, "qty": self._fmt_qty(b_symbol, qty), "side": side,
                "tradeSide": "OPEN", "orderType": order_type}
        if order_type == "LIMIT" and price:
            body["price"] = self._fmt_price(b_symbol, price)
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

    async def place_position_tp_sl(self, symbol, position_id, side,
                                    tp_price=None, tp_qty=None,
                                    sl_price=None, sl_qty=None):
        """Attach a (partial) TP and/or SL to an existing position via
        Bitunix `/api/v1/futures/tpsl/place_order`. Used for:
          * placing TP1 as a real reduce-only partial order right after entry
          * moving SL to break-even when TP1 fills
        `side` is the POSITION side ("LONG"/"SHORT") – rounding direction is
        derived from it so ticks never push TP under or SL above the mark.
        Returns the raw exchange response."""
        b_symbol = self.to_bitunix_symbol(symbol)
        is_long = str(side).upper() == "LONG"
        body: Dict = {"symbol": b_symbol, "positionId": position_id}
        if tp_price:
            body["tpPrice"] = self._fmt_price(b_symbol, tp_price,
                                              "up" if is_long else "down")
            body["tpStopType"] = "MARK_PRICE"
            body["tpOrderType"] = "MARKET"
            if tp_qty is not None:
                body["tpQty"] = self._fmt_qty(b_symbol, tp_qty)
        if sl_price:
            body["slPrice"] = self._fmt_price(b_symbol, sl_price,
                                              "down" if is_long else "up")
            body["slStopType"] = "MARK_PRICE"
            body["slOrderType"] = "MARKET"
            if sl_qty is not None:
                body["slQty"] = self._fmt_qty(b_symbol, sl_qty)
        return await self._post("/api/v1/futures/tpsl/place_order", body)

    async def modify_position_tp_sl(self, symbol, tpsl_order_id,
                                     tp_price=None, sl_price=None, side="LONG"):
        """Modify an existing TP/SL order (e.g. move SL to break-even)."""
        b_symbol = self.to_bitunix_symbol(symbol)
        is_long = str(side).upper() == "LONG"
        body: Dict = {"symbol": b_symbol, "orderId": tpsl_order_id}
        if tp_price:
            body["tpPrice"] = self._fmt_price(b_symbol, tp_price,
                                              "up" if is_long else "down")
        if sl_price:
            body["slPrice"] = self._fmt_price(b_symbol, sl_price,
                                              "down" if is_long else "up")
        return await self._post(
            "/api/v1/futures/tpsl/modify_position_tp_sl_order", body)

    async def set_leverage(self, symbol, leverage, margin_mode="ISOLATION"):
        b_symbol = self.to_bitunix_symbol(symbol)
        return await self._post("/api/v1/futures/account/change_leverage",
                                {"symbol": b_symbol, "leverage": int(leverage),
                                 "marginCoin": "USDT"})

    async def get_positions(self, symbol=None):
        q = {"symbol": self.to_bitunix_symbol(symbol)} if symbol else {}
        return await self._get("/api/v1/futures/position/get_pending_positions", q)

    async def resolve_position_id(self, symbol: str, side: str) -> Optional[str]:
        """Poll get_positions to find the positionId matching an open position.
        Bitunix's place_order response only returns orderId, not positionId,
        so we fetch it separately to attach TP1 / modify SL later.
        Returns None if the position cannot be found."""
        try:
            res = await self.get_positions(symbol)
        except Exception as e:
            logger.warning(f"resolve_position_id({symbol}) failed: {e}")
            return None
        data = res.get("data") if isinstance(res, dict) else None
        rows = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        want = str(side).upper()
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_side = str(row.get("side") or row.get("positionSide") or "").upper()
            # Bitunix returns "BUY"/"SELL" for side and/or LONG/SHORT for positionSide
            if row_side in ("BUY", "LONG") and want != "LONG":
                continue
            if row_side in ("SELL", "SHORT") and want != "SHORT":
                continue
            pid = row.get("positionId") or row.get("id")
            if pid:
                return str(pid)
        return None

    async def get_balance(self):
        return await self._get("/api/v1/futures/account", {"marginCoin": "USDT"})

    async def get_mark_price(self, symbol: str) -> Optional[float]:
        """Public endpoint: latest mark price for a Bitunix futures symbol.
        Returns None on any failure – caller should degrade gracefully."""
        b_symbol = self.to_bitunix_symbol(symbol)
        url = f"{self.base}/api/v1/futures/market/tickers"
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(url, params={"symbols": b_symbol},
                                 timeout=aiohttp.ClientTimeout(total=8)) as r:
                    payload = await r.json()
        except Exception as e:
            logger.warning(f"get_mark_price failed for {b_symbol}: {e}")
            return None
        data = payload.get("data") if isinstance(payload, dict) else None
        row = None
        if isinstance(data, list) and data:
            row = data[0]
        elif isinstance(data, dict):
            row = data
        if not isinstance(row, dict):
            return None
        for key in ("markPrice", "mark_price", "lastPrice", "last", "close"):
            v = row.get(key)
            if v is None:
                continue
            try:
                f = float(v)
                if f > 0:
                    return f
            except (TypeError, ValueError):
                continue
        return None


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
    # --- Bitunix live-order safety (fix for codes 30016 / 30027) ---
    # Minimum absolute distance (percent of mark price) that TP/SL must keep
    # away from the current mark price when the order hits the exchange.
    "min_tp_distance_percent": 0.15,
    # Floor for the risk-per-trade so TP/SL never end up microscopic
    # (percent of entry price). Prevents the classic "0.07%" reject case.
    "min_risk_percent": 0.25,
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
                       "coins": config.get("coins", {}),
                       "strategy_overrides": config.get("strategy_overrides", {})}

    def coin_cfg(self, symbol: str) -> Dict:
        c = dict(DEFAULT_COIN_CFG)
        c.update(self.config.get("coins", {}).get(symbol, {}))
        return c

    def strategy_override(self, strategy_id: Optional[str]) -> Dict:
        if not strategy_id:
            return {}
        return dict(self.config.get("strategy_overrides", {}).get(strategy_id, {}))

    def effective_cfg(self, symbol: str, strategy_id: Optional[str]) -> Dict:
        """
        Merge coin defaults with any strategy-level override. Strategy override
        values (max_capital, leverage, sl_*, tp_*, breakeven, fee, pre_signals)
        take precedence when set. Reserved keys ('mode', 'enabled',
        'signals_enabled') are handled separately by the caller.
        """
        cfg = self.coin_cfg(symbol)
        so = self.strategy_override(strategy_id)
        RESERVED = {"mode", "enabled", "signals_enabled"}
        for k, v in so.items():
            if k in RESERVED or v is None:
                continue
            cfg[k] = v
        return cfg

    def effective_mode(self, strategy_id: Optional[str]) -> str:
        """Return effective trading mode. Strategy override wins if set to
        'live' or 'paper'. 'off' means the strategy is disabled and no trade
        should be opened. Falls back to global config mode."""
        so = self.strategy_override(strategy_id)
        sm = so.get("mode")
        if sm in ("live", "paper", "off"):
            return sm
        return self.config.get("mode", "paper")

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
        # ------------------------------------------------------------------
        # Enforce a MINIMUM TP/SL distance from entry. If risk is too small
        # (e.g. 0.07%), the market moves past TP between signal generation
        # and order placement and Bitunix rejects with code 30027
        # ("TP price must be greater than mark price"). The floor is the
        # bigger of `min_risk_percent` (default 0.25%) and 3x the ATR-driven
        # buffer if ATR is available.
        # ------------------------------------------------------------------
        min_risk_pct = float(cfg.get("min_risk_percent", 0.25)) / 100
        min_risk_abs = entry * min_risk_pct
        if risk < min_risk_abs:
            risk = min_risk_abs
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

    async def _current_mark(self, symbol: str) -> Optional[float]:
        """Try to get the freshest mark price. Falls back to None."""
        if self.client and self.client.configured():
            try:
                return await self.client.get_mark_price(symbol)
            except Exception as e:
                logger.warning(f"_current_mark failed: {e}")
        return None

    async def on_signal(self, signal: Dict, candles: List[Dict]) -> Optional[Dict]:
        symbol = signal["symbol"]
        strategy_id = signal.get("strategy_id")
        # Effective mode: strategy override wins over global mode.
        # 'off' means this strategy is disabled -> no trade.
        eff_mode = self.effective_mode(strategy_id)
        if eff_mode == "off":
            return None
        cfg = self.effective_cfg(symbol, strategy_id)
        # Coin-level enable flag is still the master switch.
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

        mode = eff_mode

        # ---- LIVE MODE: hit the exchange FIRST; only persist on success ----
        if mode == "live" and self.client.configured():
            # Guard: if the calculated qty is below the exchange minimum and
            # we don't have enough capital to bump it up, skip the trade and
            # notify instead of letting Bitunix reject with code 30016.
            b_sym = self.client.to_bitunix_symbol(symbol)
            meta = self.client.contract_meta(b_sym) or {}
            min_qty = float(meta.get("min_qty") or 0)
            if min_qty > 0 and qty < min_qty:
                needed_capital = (min_qty * entry) / float(cfg["leverage"])
                logger.warning(
                    f"{symbol}: qty {qty} < min {min_qty}. Needs "
                    f"~{needed_capital:.2f} USDT capital @ {cfg['leverage']}x."
                )
                await self._notify_reject(
                    symbol, side,
                    f"Menge {qty} unter Bitunix-Minimum {min_qty}. "
                    f"Erhoehe max_capital auf mind. {needed_capital:.2f} USDT."
                )
                return None

            # Re-align TP/SL to the CURRENT mark price so they can't be on
            # the wrong side by the time the order arrives (code 30027).
            try:
                mark = await self._current_mark(symbol)
            except Exception:
                mark = None
            if mark and mark > 0:
                # Minimum absolute distance TP/SL must keep from the mark
                # price. Configurable via `min_tp_distance_percent` (default
                # 0.15%). This eats a bit of edge but eliminates 30027.
                min_dist_pct = float(cfg.get("min_tp_distance_percent", 0.15)) / 100
                min_dist = mark * min_dist_pct
                if side == "LONG":
                    tpf = max(tpf, mark + min_dist)
                    tp1 = max(tp1, mark + min_dist / 2)
                    sl = min(sl, mark - min_dist)
                else:
                    tpf = min(tpf, mark - min_dist)
                    tp1 = min(tp1, mark - min_dist / 2)
                    sl = max(sl, mark + min_dist)
                sl, tp1, tpf = round(sl, 6), round(tp1, 6), round(tpf, 6)

            try:
                await self.client.set_leverage(symbol, cfg["leverage"], cfg["margin_mode"])
                side_order = "BUY" if side == "LONG" else "SELL"
                res = await self.client.place_order(symbol, side_order, qty,
                                                    order_type=cfg["order_type"],
                                                    tp_price=tpf, sl_price=sl)
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

            # ----------------------------------------------------------------
            # Entry filled. Now put TP1 (partial, reduce-only) directly on the
            # exchange – previously TP1 was only enforced by our local monitor
            # via flash_close, so if the backend was lagging or offline the
            # partial TP never fired.
            # ----------------------------------------------------------------
            position_id: Optional[str] = None
            tp1_placed = False
            try:
                # small delay so the position is picked up by the position API
                position_id = await self.client.resolve_position_id(symbol, side)
                if position_id:
                    tp1_close_qty = round(qty * float(cfg["tp1_close_percent"]) / 100, 6)
                    tp1_res = await self.client.place_position_tp_sl(
                        symbol, position_id, side,
                        tp_price=tp1, tp_qty=tp1_close_qty)
                    tp1_ok = isinstance(tp1_res, dict) and tp1_res.get("code") == 0
                    if tp1_ok:
                        tp1_placed = True
                        logger.info(f"TP1 partial placed on Bitunix {symbol} "
                                    f"@ {tp1} qty={tp1_close_qty}")
                    else:
                        logger.warning(f"TP1 partial place failed {symbol}: {tp1_res}")
                else:
                    logger.warning(f"Could not resolve positionId for {symbol}; "
                                   "TP1 partial NOT placed (local monitor will "
                                   "handle it as fallback).")
            except Exception as e:
                logger.error(f"TP1 partial exception {symbol}: {e}")

            trade_extra = {"bitunix_order_id": order_id, "bitunix_response": res,
                           "bitunix_position_id": position_id,
                           "tp1_exchange_placed": tp1_placed}
        else:
            trade_extra = {"bitunix_order_id": None,
                           "bitunix_position_id": None,
                           "tp1_exchange_placed": False}

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
            be_price = None
            if t.get("breakeven_enabled"):
                fee = t.get("fee_percent", 0.06) / 100
                be = t["entry"] * (1 + 2 * fee) if side == "LONG" else t["entry"] * (1 - 2 * fee)
                be_price = round(be, 6)
                updates["sl"] = be_price
                updates["breakeven_moved"] = True
                events.append(f"SL -> Break-Even @ {be_price}")
            # Live sync: only flash-close if the exchange TP1 was NOT placed
            # (otherwise Bitunix already closed 50%, no need to close again).
            # Then push the break-even SL to the exchange so it survives even
            # if our backend restarts.
            if t.get("mode") == "live" and self.client.configured():
                if not t.get("tp1_exchange_placed"):
                    await self._live_partial_close(t, close_qty)
                if be_price is not None and t.get("bitunix_position_id"):
                    ok_be = await self._live_move_sl(t, be_price, qty_rem)
                    if ok_be:
                        events.append("Exchange SL -> BE synced")
                    else:
                        events.append("Exchange SL move FAILED (local only)")

        # ATR trailing stop after TP1 -> lock profit while letting the runner breathe
        if (t.get("tp1_hit") or updates.get("tp1_hit")):
            cfg2 = self.coin_cfg(t["symbol"])
            atr = t.get("atr") or 0
            if cfg2.get("trail_after_tp1", True) and atr > 0:
                cur_sl = updates.get("sl", t["sl"])
                mult = float(cfg2.get("trail_atr_mult", 1.5))
                trailed = None
                if side == "LONG":
                    new_sl = round(price - atr * mult, 6)
                    if new_sl > cur_sl:
                        updates["sl"] = new_sl
                        trailed = new_sl
                        events.append(f"TRAIL SL -> {new_sl}")
                else:
                    new_sl = round(price + atr * mult, 6)
                    if new_sl < cur_sl:
                        updates["sl"] = new_sl
                        trailed = new_sl
                        events.append(f"TRAIL SL -> {new_sl}")
                # Sync trailed SL to the exchange too so a backend restart
                # can't leave us protected only by the original SL.
                if trailed is not None and t.get("mode") == "live":
                    await self._live_move_sl(t, trailed, qty_rem)

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
            pos_id = t.get("bitunix_position_id") or t.get("bitunix_order_id")
            await self.client.flash_close(t["symbol"], pos_id, t["side"], qty)
            return True
        except Exception as e:
            logger.error(f"partial close failed: {e}")
            return False

    async def _live_move_sl(self, t, new_sl_price: float, qty_rem: float) -> bool:
        """Push a new SL price for the remaining position to Bitunix (used for
        break-even after TP1 and for the ATR trailing stop). Best-effort:
        returns False on failure but never raises to the caller."""
        if t.get("mode") != "live" or not self.client.configured():
            return False
        pid = t.get("bitunix_position_id")
        if not pid:
            # No positionId available (e.g. resolve_position_id failed at
            # open) – can't target the SL server-side. Log and give up.
            logger.warning(f"_live_move_sl: no positionId for {t.get('symbol')}")
            return False
        try:
            res = await self.client.place_position_tp_sl(
                t["symbol"], pid, t["side"],
                sl_price=new_sl_price, sl_qty=qty_rem)
            ok = isinstance(res, dict) and res.get("code") == 0
            if not ok:
                logger.warning(f"_live_move_sl {t['symbol']} -> {new_sl_price} "
                               f"rejected: {res}")
            return ok
        except Exception as e:
            logger.error(f"_live_move_sl exception: {e}")
            return False

    async def _live_flash_close(self, t, qty):
        try:
            # Use bitunix_position_id (not order_id) so only the bot's
            # specific position is closed – manual user positions stay open.
            pos_id = t.get("bitunix_position_id") or t.get("bitunix_order_id")
            await self.client.flash_close(t["symbol"], pos_id, t["side"], qty or t["qty_remaining"])
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
