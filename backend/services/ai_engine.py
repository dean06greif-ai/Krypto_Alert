"""
AI Trading Engine ("KI Trader")
- Periodically sends multi-timeframe market snapshots + crypto news + user chat
  directives to Google Gemini (via the public `google-genai` SDK).
- The LLM returns structured trade decisions (LONG/SHORT/HOLD + confidence +
  SL/TP suggestions + reasoning). Actionable decisions are emitted as signals
  through the normal signal/auto-trade pipeline (strategy_id "ai_trader").
- Provides a multi-turn chat so the user can give the AI instructions
  ("achte auf BTC-Support bei 60k") that flow into the next analysis.

LLM: Google Gemini 2.5 (Pro als Default, automatischer Fallback auf Flash bei
Rate-Limit / Quota). Kein internes Emergent-Package mehr -> deploybar auf Render.
"""
import os
import json
import re
import time
import uuid
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Callable

from dotenv import load_dotenv
load_dotenv()

from services.timeframes import aggregate_candles
from services.technical_indicators import TechnicalIndicators
from services.news_feed import news_feed

logger = logging.getLogger(__name__)

DEFAULT_AI_CONFIG = {
    "enabled": False,
    "interval_min": 10,
    "min_confidence": 65,
    "provider": "gemini",
    "model": "gemini-2.5-pro",
    "news_enabled": True,
    "cooldown_min": 45,
}

# Nur noch Gemini-Modelle. Pro = beste Qualität, Flash = automatischer Fallback
# bei Rate-Limit / 429. Flash-Lite kann als extra günstige Option gewählt werden.
ALLOWED_MODELS = {
    "gemini": [
        "gemini-2.5-pro",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    ],
}

# Reihenfolge der Fallbacks bei Rate-Limit/Quota. Sobald ein Modell 429 liefert,
# wird das nächste probiert. Dadurch bleibt der KI Trader auch nach dem
# Pro-Tageslimit lauffähig.
FALLBACK_ORDER = ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-2.5-flash-lite"]

ANALYSIS_SYSTEM = (
    "Du bist ein erfahrener Krypto-Daytrading-Analyst und triffst eigenständige "
    "Trading-Entscheidungen für ein automatisiertes System. Du bekommst Multi-Timeframe-"
    "Marktdaten, aktuelle News-Schlagzeilen, offene Positionen und Anweisungen des Traders. "
    "Sei diszipliniert: Trade NUR bei klarer Edge, sonst HOLD. Sei ehrlich mit der Konfidenz. "
    "Berücksichtige Anweisungen des Traders IMMER mit höchster Priorität. "
    "Antworte AUSSCHLIESSLICH mit validem JSON ohne Markdown, exakt in diesem Schema:\n"
    '{"market_overview": "2-4 Sätze Marktlage auf Deutsch", '
    '"decisions": [{"symbol": "BTCUSDT", "action": "LONG|SHORT|HOLD", '
    '"confidence": 0-100, "sl_pct": 0.2-3.0, "tp1_pct": 0.3-4.0, "tpf_pct": 0.5-8.0, '
    '"news_impact": "positive|negative|neutral", "reasoning": "1-2 Sätze auf Deutsch"}]}\n'
    "Regeln: sl_pct/tp1_pct/tpf_pct sind Prozent-Abstände vom aktuellen Preis. "
    "tp1_pct > sl_pct (CRV mind. 1.2), tpf_pct > tp1_pct. Für JEDES übergebene Symbol genau eine Entscheidung."
)

CHAT_SYSTEM_TEMPLATE = (
    "Du bist der 'KI Trader' – die integrierte Trading-KI einer Krypto-Daytrading-Plattform. "
    "Du analysierst periodisch alle Coins (Multi-Timeframe + News) und kannst automatisch Trades auslösen. "
    "Der Nutzer chattet hier mit dir, um dir Anweisungen zu geben (z.B. 'achte auf BTC-Support bei 60k', "
    "'sei heute defensiv', 'keine Shorts auf SOL'). Alle Nutzer-Nachrichten fließen automatisch als "
    "Direktiven in deine nächste Analyse ein – bestätige das, wenn dir jemand eine Anweisung gibt. "
    "Antworte kompakt, präzise und auf Deutsch. Nutze die Live-Daten unten für fundierte Antworten. "
    "Erfinde keine Zahlen.\n\n"
    "=== AKTUELLER KONTEXT ===\n{context}\n\n"
    "=== BISHERIGER CHAT-VERLAUF ===\n{history}"
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _is_rate_limit_error(err: Exception) -> bool:
    """True wenn Gemini 429 / RESOURCE_EXHAUSTED / Quota-Fehler wirft."""
    s = str(err).lower()
    return any(k in s for k in ("429", "resource_exhausted", "quota", "rate limit", "ratelimit"))


class AIEngine:
    def __init__(self):
        self.config = dict(DEFAULT_AI_CONFIG)
        self.db = None
        self.scanner = None
        self.signal_cb: Optional[Callable] = None
        self.toggle_check: Optional[Callable] = None
        self.symbols: List[str] = []
        self.decisions: Dict[str, Dict] = {}
        self.last_run: Optional[str] = None
        self.next_run: Optional[str] = None
        self.last_error: Optional[str] = None
        self.running = False
        self._analyzing = False
        self._next_due = 0.0
        self._last_signal_ts: Dict[str, float] = {}
        self._client = None  # lazy-initialisierter google-genai Client
        self._client_key: Optional[str] = None
        # Modell, das aktuell benutzt wird (kann nach 429 vom Fallback überschrieben werden)
        self._effective_model: Optional[str] = None

    @property
    def key(self) -> Optional[str]:
        # Primär GEMINI_API_KEY, GOOGLE_API_KEY als Alias (Google-SDK-Konvention).
        return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")

    def _get_client(self):
        """Google-GenAI-Client cachen – bei Key-Wechsel neu bauen."""
        key = self.key
        if not key:
            return None
        if self._client is None or self._client_key != key:
            from google import genai  # lokaler Import -> Server startet auch ohne Key
            self._client = genai.Client(api_key=key)
            self._client_key = key
        return self._client

    def setup(self, db, scanner, signal_cb, toggle_check, symbols: List[str]):
        self.db = db
        self.scanner = scanner
        self.signal_cb = signal_cb
        self.toggle_check = toggle_check
        self.symbols = symbols

    # ---------------- config ----------------
    async def load_config(self):
        doc = await self.db.settings.find_one({"_id": "ai_trader_config"})
        if doc:
            doc.pop("_id", None)
            for k in DEFAULT_AI_CONFIG:
                if k in doc:
                    self.config[k] = doc[k]
            # Migration von alten Providern (openai/anthropic) -> Gemini
            if self.config.get("provider") != "gemini" or self.config.get("model") not in ALLOWED_MODELS["gemini"]:
                self.config["provider"] = "gemini"
                self.config["model"] = "gemini-2.5-pro"
                await self.db.settings.update_one(
                    {"_id": "ai_trader_config"},
                    {"$set": {"provider": "gemini", "model": "gemini-2.5-pro"}},
                    upsert=True,
                )
        else:
            await self.db.settings.insert_one({"_id": "ai_trader_config", **self.config})
        # load last decisions for continuity after restart
        try:
            rows = await self.db.ai_decisions.find().sort("ts", -1).limit(60).to_list(60)
            for r in rows:
                sym = r.get("symbol")
                if sym and sym not in self.decisions:
                    r.pop("_id", None)
                    self.decisions[sym] = r
        except Exception:
            pass

    async def update_config(self, updates: Dict) -> Dict:
        was_enabled = self.config.get("enabled")
        if "enabled" in updates:
            self.config["enabled"] = bool(updates["enabled"])
        if "interval_min" in updates:
            self.config["interval_min"] = max(2, min(120, int(updates["interval_min"])))
        if "min_confidence" in updates:
            self.config["min_confidence"] = max(0, min(100, int(updates["min_confidence"])))
        if "cooldown_min" in updates:
            self.config["cooldown_min"] = max(0, min(720, int(updates["cooldown_min"])))
        if "news_enabled" in updates:
            self.config["news_enabled"] = bool(updates["news_enabled"])
        if "provider" in updates and "model" in updates:
            prov, mod = updates["provider"], updates["model"]
            if prov in ALLOWED_MODELS and mod in ALLOWED_MODELS[prov]:
                self.config["provider"], self.config["model"] = prov, mod
                # Wechselt der Nutzer das Modell manuell, reset des Fallback-States.
                self._effective_model = None
        elif "model" in updates:
            mod = updates["model"]
            if mod in ALLOWED_MODELS["gemini"]:
                self.config["model"] = mod
                self.config["provider"] = "gemini"
                self._effective_model = None
        await self.db.settings.update_one({"_id": "ai_trader_config"},
                                          {"$set": dict(self.config)}, upsert=True)
        if self.config.get("enabled") and not was_enabled:
            self._next_due = 0  # run analysis immediately after enabling
        return dict(self.config)

    # ---------------- market context ----------------
    def _snapshot(self, symbol: str) -> Optional[Dict]:
        candles = self.scanner.candle_buffer.get(symbol, [])
        if len(candles) < 60:
            return None
        ti = TechnicalIndicators
        price = candles[-1]["close"]
        lines = []
        rsi_1m = 0
        for tf in ("1m", "15m", "1h"):
            agg = candles if tf == "1m" else aggregate_candles(candles, tf, drop_partial=True)
            if len(agg) < 20:
                continue
            cl = [c["close"] for c in agg][-120:]
            rsi_arr = ti.calculate_rsi(cl, 14)
            rsi = rsi_arr[-1] if rsi_arr and rsi_arr[-1] is not None else 50
            if tf == "1m":
                rsi_1m = rsi
            ema20 = ti.calculate_ema(cl, 20)[-1]
            ema50 = ti.calculate_ema(cl, 50)[-1] if len(cl) >= 50 else None
            trend = "aufwärts" if (ema50 and ema20 > ema50) else ("abwärts" if ema50 else "unklar")
            chg = (cl[-1] - cl[0]) / cl[0] * 100 if cl[0] else 0
            hi = max(c["high"] for c in agg[-60:])
            lo = min(c["low"] for c in agg[-60:])
            lines.append(f"{tf}: RSI {rsi:.0f}, Trend {trend}, Δ{chg:+.2f}%, Range {lo:g}-{hi:g}")
        try:
            atr = ti.calculate_atr(candles, 14)[-1] or 0
            vols = [c.get("volume", 0) for c in candles]
            v_recent = sum(vols[-5:]) / 5
            v_base = (sum(vols[-60:]) / 60) or 1
            lines.append(f"ATR(1m) {atr / price * 100:.3f}% | Volumen x{v_recent / v_base:.2f}")
        except Exception:
            pass
        return {"symbol": symbol, "price": price, "rsi": round(rsi_1m, 1),
                "text": f"{symbol}: Preis {price:g} | " + " | ".join(lines)}

    async def _user_directives(self, limit: int = 15) -> str:
        rows = await self.db.ai_chat.find({"role": "user"}).sort("ts", -1).limit(limit).to_list(limit)
        rows.reverse()
        if not rows:
            return "(keine)"
        return "\n".join(f"- [{r.get('ts', '')[:16]}] {r.get('text', '')}" for r in rows)

    async def _open_trades_text(self) -> str:
        rows = await self.db.auto_trades.find({"status": "open"}).to_list(50)
        if not rows:
            return "(keine offenen Positionen)"
        out = []
        for t in rows:
            out.append(f"- {t.get('symbol')} {t.get('side')} @ {t.get('entry')} "
                       f"(SL {t.get('sl')}, TP1 {t.get('tp1')}, Modus {t.get('mode')})")
        return "\n".join(out)

    async def _context_brief(self) -> str:
        parts = []
        snaps = []
        for s in self.symbols:
            snap = self._snapshot(s)
            if snap:
                snaps.append(snap["text"])
        parts.append("MARKTDATEN:\n" + ("\n".join(snaps) if snaps else "(noch keine Daten)"))
        if self.config.get("news_enabled"):
            news = await news_feed.get_headlines(8)
            if news:
                parts.append("NEWS:\n" + "\n".join(f"- {n['title']} ({n['source']})" for n in news))
        if self.decisions:
            dec = [f"- {s}: {d.get('action')} ({d.get('confidence')}%) – {d.get('reasoning', '')[:120]}"
                   for s, d in self.decisions.items()]
            parts.append("LETZTE KI-ENTSCHEIDUNGEN:\n" + "\n".join(dec))
        parts.append("OFFENE POSITIONEN:\n" + await self._open_trades_text())
        cfg = self.config
        parts.append(f"ENGINE: {'AKTIV' if cfg['enabled'] else 'AUS'} | Analyse alle {cfg['interval_min']} min | "
                     f"Min. Konfidenz {cfg['min_confidence']}% | Modell {cfg['provider']}/{cfg['model']} | "
                     f"Letzte Analyse: {self.last_run or 'noch keine'}")
        return "\n\n".join(parts)

    # ---------------- analysis ----------------
    @staticmethod
    def _parse_json(text: str) -> Dict:
        text = re.sub(r"```(json)?", "", text).strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("Keine JSON-Antwort der KI")
        return json.loads(text[start:end + 1])

    def is_fresh(self, decision: Optional[Dict]) -> bool:
        if not decision or not decision.get("ts"):
            return False
        try:
            ts = datetime.fromisoformat(decision["ts"].replace("Z", "+00:00"))
            max_age = max(self.config.get("interval_min", 10) * 2.5, 20)
            return (datetime.now(timezone.utc) - ts) < timedelta(minutes=max_age)
        except Exception:
            return False

    def _fallback_chain(self) -> List[str]:
        """Reihenfolge der Modelle: bevorzugtes Modell zuerst, dann Rest."""
        preferred = self.config.get("model") or "gemini-2.5-pro"
        chain = [preferred] + [m for m in FALLBACK_ORDER if m != preferred]
        return chain

    async def _generate_json(self, prompt: str, system: str) -> tuple[str, str]:
        """Ruft Gemini mit JSON-Response auf. Bei 429 wird auf Flash / Flash-Lite
        umgeschaltet. Gibt (raw_text, effektives_model) zurück."""
        from google.genai import types  # local import
        client = self._get_client()
        if client is None:
            raise RuntimeError("GEMINI_API_KEY fehlt")

        last_err: Optional[Exception] = None
        for model in self._fallback_chain():
            try:
                resp = await client.aio.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        response_mime_type="application/json",
                        temperature=0.4,
                    ),
                )
                text = (resp.text or "").strip()
                if not text:
                    raise RuntimeError("Leere Antwort von Gemini")
                self._effective_model = model
                if model != self.config.get("model"):
                    logger.warning(f"AI analysis: Fallback auf {model} (Pref war {self.config.get('model')})")
                return text, model
            except Exception as e:
                last_err = e
                if _is_rate_limit_error(e):
                    logger.warning(f"Gemini {model} rate-limited, versuche nächstes Modell…")
                    continue
                # Nicht-Rate-Limit -> nicht weiter probieren, hochreichen.
                raise
        # Alle Modelle rate-limited
        raise last_err or RuntimeError("Alle Gemini-Modelle rate-limited")

    async def run_analysis(self, manual: bool = False) -> Dict:
        if self._analyzing:
            return {"status": "busy", "detail": "Analyse läuft bereits"}
        if not self.key:
            self.last_error = "GEMINI_API_KEY fehlt (Render EnvVars setzen)"
            return {"status": "error", "detail": self.last_error}
        self._analyzing = True
        try:
            symbols = [s for s in self.symbols
                       if (not self.toggle_check or self.toggle_check("ai_trader", s))
                       and len(self.scanner.candle_buffer.get(s, [])) >= 60]
            if not symbols:
                return {"status": "error", "detail": "Keine Coins mit ausreichend Kursdaten"}
            snaps = {s: self._snapshot(s) for s in symbols}
            snaps = {s: v for s, v in snaps.items() if v}

            news_block = "(News deaktiviert)"
            if self.config.get("news_enabled"):
                news = await news_feed.get_headlines(18)
                news_block = "\n".join(f"- {n['title']} ({n['source']})" for n in news) or "(keine News verfügbar)"

            directives = await self._user_directives()
            open_trades = await self._open_trades_text()
            berlin = self.scanner.berlin_now().strftime("%d.%m.%Y %H:%M")

            prompt = (
                f"Zeit (Berlin): {berlin}\n\n"
                f"=== MARKTDATEN (Multi-Timeframe) ===\n" +
                "\n".join(v["text"] for v in snaps.values()) +
                f"\n\n=== AKTUELLE NEWS ===\n{news_block}\n\n"
                f"=== ANWEISUNGEN DES TRADERS (höchste Priorität) ===\n{directives}\n\n"
                f"=== OFFENE POSITIONEN ===\n{open_trades}\n\n"
                f"Analysiere jedes Symbol ({', '.join(snaps.keys())}) und gib deine Entscheidungen als JSON zurück."
            )

            raw, model_used = await self._generate_json(prompt, ANALYSIS_SYSTEM)
            data = self._parse_json(raw)

            now = _now_iso()
            emitted = []
            stored = []
            for d in data.get("decisions", []):
                sym = d.get("symbol")
                if sym not in snaps:
                    continue
                action = str(d.get("action", "HOLD")).upper()
                if action not in ("LONG", "SHORT", "HOLD"):
                    action = "HOLD"
                dec = {
                    "id": str(uuid.uuid4()),
                    "symbol": sym,
                    "action": action,
                    "confidence": max(0, min(100, int(d.get("confidence", 0) or 0))),
                    "sl_pct": float(d.get("sl_pct", 0.6) or 0.6),
                    "tp1_pct": float(d.get("tp1_pct", 0.9) or 0.9),
                    "tpf_pct": float(d.get("tpf_pct", 1.8) or 1.8),
                    "news_impact": d.get("news_impact", "neutral"),
                    "reasoning": str(d.get("reasoning", ""))[:500],
                    "price": snaps[sym]["price"],
                    "rsi": snaps[sym]["rsi"],
                    "ts": now,
                    "signaled": False,
                    "model": model_used,
                }
                self.decisions[sym] = dec
                stored.append(dec)
                if (action in ("LONG", "SHORT")
                        and dec["confidence"] >= self.config["min_confidence"]
                        and self.scanner.is_trading_session("ai_trader")):
                    ok = await self._emit_signal(dec)
                    if ok:
                        dec["signaled"] = True
                        emitted.append(f"{sym} {action}")
            if stored:
                await self.db.ai_decisions.insert_many([dict(x) for x in stored])

            feed_entry = {
                "id": str(uuid.uuid4()),
                "role": "analysis",
                "text": str(data.get("market_overview", ""))[:1200],
                "decisions": [{"symbol": x["symbol"], "action": x["action"],
                               "confidence": x["confidence"], "reasoning": x["reasoning"],
                               "signaled": x["signaled"]} for x in stored],
                "emitted": emitted,
                "manual": manual,
                "model": model_used,
                "ts": now,
            }
            await self.db.ai_chat.insert_one(dict(feed_entry))
            self.last_run = now
            self.last_error = None
            logger.info(f"AI analysis done ({model_used}): {len(stored)} decisions, {len(emitted)} signals ({emitted})")
            return {"status": "ok", "decisions": len(stored), "signals": emitted,
                    "overview": feed_entry["text"], "model": model_used}
        except Exception as e:
            self.last_error = str(e)[:300]
            logger.error(f"AI analysis failed: {e}")
            return {"status": "error", "detail": self.last_error}
        finally:
            self._analyzing = False

    async def _emit_signal(self, dec: Dict) -> bool:
        sym = dec["symbol"]
        cooldown = self.config.get("cooldown_min", 45) * 60
        if cooldown and (time.time() - self._last_signal_ts.get(sym, 0)) < cooldown:
            return False
        entry = float(dec["price"])
        if entry <= 0:
            return False
        sl_pct = max(0.15, min(5.0, dec["sl_pct"])) / 100
        tp1_pct = max(sl_pct * 1.2, min(0.08, dec["tp1_pct"] / 100))
        tpf_pct = max(tp1_pct, min(0.15, dec["tpf_pct"] / 100))
        sign = 1 if dec["action"] == "LONG" else -1
        sl = entry * (1 - sign * sl_pct)
        tp1 = entry * (1 + sign * tp1_pct)
        tpf = entry * (1 + sign * tpf_pct)
        crv = round(abs(tp1 - entry) / abs(entry - sl), 2) if entry != sl else 0
        now = self.scanner.berlin_now()
        rules_met = {"ai_active": True, "ai_direction": True, "ai_confidence": True, "ai_news": True}
        signal = {
            "symbol": sym,
            "type": dec["action"],
            "signal_class": "SIGNAL",
            "entry_price": round(entry, 6),
            "stop_loss": round(sl, 6),
            "take_profit_1": round(tp1, 6),
            "take_profit_full": round(tpf, 6),
            "crv": crv,
            "rsi": dec.get("rsi", 0),
            "ema_fast": 0,
            "ema_slow": 0,
            "rules_met": rules_met,
            "rules_met_count": 4,
            "rules_total": 4,
            "timestamp": _now_iso(),
            "trade_date": self.scanner.berlin_date(),
            "hour": now.hour,
            "weekday": now.weekday(),
            "session": self.scanner.get_current_session(),
            "strategy_id": "ai_trader",
            "strategy_name": "KI Trader",
            "status": "active",
            "ai_confidence": dec["confidence"],
            "ai_reasoning": dec["reasoning"],
        }
        try:
            ok = await self.signal_cb(signal)
            if ok:
                self._last_signal_ts[sym] = time.time()
            return bool(ok)
        except Exception as e:
            logger.error(f"AI signal emit failed for {sym}: {e}")
            return False

    # ---------------- background loop ----------------
    async def run_loop(self):
        self.running = True
        logger.info("AI Trader engine loop started (Gemini)")
        while self.running:
            await asyncio.sleep(5)
            try:
                if not self.config.get("enabled") or not self.key:
                    self.next_run = None
                    continue
                now = time.time()
                if now >= self._next_due:
                    interval = max(2, int(self.config.get("interval_min", 10))) * 60
                    self._next_due = now + interval
                    self.next_run = (datetime.now(timezone.utc)
                                     + timedelta(seconds=interval)).isoformat()
                    await self.run_analysis()
            except Exception as e:
                logger.error(f"AI loop error: {e}")

    # ---------------- chat ----------------
    async def chat_history(self, limit: int = 80) -> List[Dict]:
        rows = await self.db.ai_chat.find().sort("ts", -1).limit(limit).to_list(limit)
        rows.reverse()
        for r in rows:
            r.pop("_id", None)
        return rows

    async def chat_stream(self, text: str):
        """SSE-Streaming der Gemini-Antwort. Wechselt bei 429 automatisch das Modell."""
        if not self.key:
            yield "⚠️ GEMINI_API_KEY fehlt – bitte in Render EnvVars setzen."
            return

        from google.genai import types  # local import
        client = self._get_client()

        hist_rows = await self.db.ai_chat.find({"role": {"$in": ["user", "assistant"]}}) \
            .sort("ts", -1).limit(14).to_list(14)
        hist_rows.reverse()
        history = "\n".join(
            f"{'Nutzer' if r['role'] == 'user' else 'KI'}: {r.get('text', '')}" for r in hist_rows
        ) or "(noch keine Nachrichten)"
        context = await self._context_brief()
        system = CHAT_SYSTEM_TEMPLATE.format(context=context, history=history)

        await self.db.ai_chat.insert_one({
            "id": str(uuid.uuid4()), "role": "user", "text": text, "ts": _now_iso(),
        })

        acc = ""
        last_err: Optional[Exception] = None
        streamed_any = False
        for model in self._fallback_chain():
            try:
                stream = await client.aio.models.generate_content_stream(
                    model=model,
                    contents=text,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        temperature=0.6,
                    ),
                )
                async for chunk in stream:
                    part = getattr(chunk, "text", None)
                    if part:
                        acc += part
                        streamed_any = True
                        yield part
                self._effective_model = model
                if model != self.config.get("model"):
                    logger.warning(f"AI chat: Fallback auf {model}")
                last_err = None
                break
            except Exception as e:
                last_err = e
                if _is_rate_limit_error(e) and not streamed_any:
                    logger.warning(f"Gemini chat {model} rate-limited, versuche nächstes Modell…")
                    continue
                err = f"\n⚠️ KI-Fehler: {str(e)[:200]}"
                acc += err
                yield err
                last_err = None
                break

        if last_err is not None:
            err = f"\n⚠️ KI-Fehler: Alle Gemini-Modelle rate-limited. {str(last_err)[:150]}"
            acc += err
            yield err

        if acc:
            await self.db.ai_chat.insert_one({
                "id": str(uuid.uuid4()), "role": "assistant", "text": acc, "ts": _now_iso(),
            })

    async def clear_chat(self):
        await self.db.ai_chat.delete_many({})

    def status(self) -> Dict:
        return {
            "config": dict(self.config),
            "has_key": bool(self.key),
            "analyzing": self._analyzing,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "last_error": self.last_error,
            "decisions": self.decisions,
            "allowed_models": ALLOWED_MODELS,
            "effective_model": self._effective_model,
        }


ai_engine = AIEngine()
