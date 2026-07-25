"""
AI Trading Engine ("KI Trader")
- Periodically sends multi-timeframe market snapshots + crypto news + user chat
  directives to a configurable LLM (Gemini, Groq, OpenRouter/Grok, Mistral).
- The LLM returns structured trade decisions (LONG/SHORT/HOLD + confidence +
  SL/TP suggestions + reasoning). Actionable decisions are emitted as signals
  through the normal signal/auto-trade pipeline (strategy_id "ai_trader").
- Provides a multi-turn chat so the user can give the AI instructions
  ("achte auf BTC-Support bei 60k") that flow into the next analysis.

Provider (alle kostenlos in ihren Free-Tiers, deploybar auf Render):
  - Google Gemini      -> GEMINI_API_KEY  (google-genai SDK)
  - Groq (Llama, Qwen) -> GROQ_API_KEY    (OpenAI-kompatibel)
  - OpenRouter (Grok, DeepSeek, Llama Free) -> OPENROUTER_API_KEY
  - Mistral            -> MISTRAL_API_KEY (OpenAI-kompatibel)

Der Fallback bei Rate-Limit bleibt innerhalb des ausgewählten Providers.
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
    "model": "gemini-3.5-flash",
    "news_enabled": True,
    "cooldown_min": 45,
}

# Erlaubte Modelle je Provider. Alle folgenden Provider bieten großzügige
# kostenlose Free-Tiers, die für den KI-Trader ausreichen.
ALLOWED_MODELS = {
    "gemini": [
        "gemini-3.1-pro-preview",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
    ],
    "groq": [
        # Groq Free Tier – extrem schnelle Inferenz
        "llama-3.3-70b-versatile",
        "llama-3.1-8b-instant",
        "qwen/qwen3-32b",
    ],
    "openrouter": [
        # OpenRouter Free Tier – hier lebt u.a. Grok kostenlos
        "x-ai/grok-4-fast:free",
        "deepseek/deepseek-r1:free",
        "deepseek/deepseek-chat-v3.1:free",
        "meta-llama/llama-3.3-70b-instruct:free",
    ],
    "mistral": [
        # Mistral Free Tier (La Plateforme)
        "mistral-small-latest",
        "open-mistral-7b",
    ],
}

# Provider-Metadaten für OpenAI-kompatible Backends (Groq, OpenRouter, Mistral).
# base_url + Env-Variable, die den API-Key enthält.
OPENAI_COMPAT_PROVIDERS = {
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "env_keys": ["GROQ_API_KEY"],
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "env_keys": ["OPENROUTER_API_KEY"],
    },
    "mistral": {
        "base_url": "https://api.mistral.ai/v1",
        "env_keys": ["MISTRAL_API_KEY"],
    },
}

# Fallback-Reihenfolge je Provider (bei 429/Rate-Limit wird das nächste Modell
# desselben Providers probiert).
FALLBACK_ORDER = {
    "gemini": ["gemini-3.1-pro-preview", "gemini-3.5-flash", "gemini-3.1-flash-lite"],
    "groq": ["llama-3.3-70b-versatile", "llama-3.1-8b-instant", "qwen/qwen3-32b"],
    "openrouter": [
        "x-ai/grok-4-fast:free",
        "deepseek/deepseek-chat-v3.1:free",
        "deepseek/deepseek-r1:free",
        "meta-llama/llama-3.3-70b-instruct:free",
    ],
    "mistral": ["mistral-small-latest", "open-mistral-7b"],
}

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
        # Gemini
        self._client = None
        self._client_key: Optional[str] = None
        # OpenAI-kompatible Clients (Groq / OpenRouter / Mistral) – pro Provider gecached.
        self._oai_clients: Dict[str, tuple] = {}  # provider -> (client, key)
        # Modell, das aktuell benutzt wird (nach Fallback ggf. abweichend von cfg.model)
        self._effective_model: Optional[str] = None

    @property
    def key(self) -> Optional[str]:
        """API-Key des aktuell konfigurierten Providers."""
        return self._provider_key(self.config.get("provider", "gemini"))

    @staticmethod
    def _provider_key(provider: str) -> Optional[str]:
        if provider == "gemini":
            # Primär GEMINI_API_KEY, GOOGLE_API_KEY als Alias (Google-SDK-Konvention).
            return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        meta = OPENAI_COMPAT_PROVIDERS.get(provider)
        if not meta:
            return None
        for env_name in meta["env_keys"]:
            v = os.environ.get(env_name)
            if v:
                return v
        return None

    def _available_providers(self) -> Dict[str, bool]:
        """True, wenn für den Provider ein API-Key gesetzt ist."""
        out = {"gemini": bool(self._provider_key("gemini"))}
        for p in OPENAI_COMPAT_PROVIDERS:
            out[p] = bool(self._provider_key(p))
        return out

    def _get_client(self):
        """Google-GenAI-Client cachen – bei Key-Wechsel neu bauen."""
        key = self._provider_key("gemini")
        if not key:
            return None
        if self._client is None or self._client_key != key:
            from google import genai  # lokaler Import -> Server startet auch ohne Key
            self._client = genai.Client(api_key=key)
            self._client_key = key
        return self._client

    def _get_openai_client(self, provider: str):
        """AsyncOpenAI-Client für Groq/OpenRouter/Mistral cachen."""
        meta = OPENAI_COMPAT_PROVIDERS.get(provider)
        if not meta:
            return None
        key = self._provider_key(provider)
        if not key:
            return None
        cached = self._oai_clients.get(provider)
        if cached and cached[1] == key:
            return cached[0]
        from openai import AsyncOpenAI  # lokaler Import
        default_headers = None
        if provider == "openrouter":
            # OpenRouter empfiehlt diese Headers zur besseren Ranking-Sichtbarkeit
            default_headers = {
                "HTTP-Referer": os.environ.get("OPENROUTER_REFERER", "https://krypto-alert.local"),
                "X-Title": os.environ.get("OPENROUTER_TITLE", "Krypto Alert KI Trader"),
            }
        client = AsyncOpenAI(base_url=meta["base_url"], api_key=key, default_headers=default_headers)
        self._oai_clients[provider] = (client, key)
        return client

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
            # Migration: unbekannten Provider oder ungültiges Modell -> Default (Gemini Flash)
            prov = self.config.get("provider")
            mod = self.config.get("model")
            if prov not in ALLOWED_MODELS or mod not in ALLOWED_MODELS.get(prov, []):
                self.config["provider"] = "gemini"
                self.config["model"] = "gemini-3.5-flash"
                await self.db.settings.update_one(
                    {"_id": "ai_trader_config"},
                    {"$set": {"provider": "gemini", "model": "gemini-3.5-flash"}},
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
            # Finde Provider automatisch anhand des Modells
            for prov, models in ALLOWED_MODELS.items():
                if mod in models:
                    self.config["model"] = mod
                    self.config["provider"] = prov
                    self._effective_model = None
                    break
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

    def _resolve_coins(self, coins) -> List[str]:
        """Normalisiert den Coin-Filter aus dem Chat.

        Leer / None / enthält "ALL" => alle bekannten Symbole. Sonst nur die
        angeforderten Symbole (Reihenfolge von self.symbols beibehalten,
        unbekannte ignorieren)."""
        if not coins:
            return list(self.symbols)
        wanted = {str(c).upper() for c in coins}
        if "ALL" in wanted or "ALLE" in wanted:
            return list(self.symbols)
        filtered = [s for s in self.symbols if s.upper() in wanted]
        return filtered or list(self.symbols)

    async def _open_trades_text(self, allowed: Optional[List[str]] = None) -> str:
        rows = await self.db.auto_trades.find({"status": "open"}).to_list(50)
        if allowed is not None:
            allow = {s.upper() for s in allowed}
            rows = [t for t in rows if str(t.get("symbol", "")).upper() in allow]
        if not rows:
            return "(keine offenen Positionen)"
        out = []
        for t in rows:
            out.append(f"- {t.get('symbol')} {t.get('side')} @ {t.get('entry')} "
                       f"(SL {t.get('sl')}, TP1 {t.get('tp1')}, Modus {t.get('mode')})")
        return "\n".join(out)

    async def _context_brief(self, coins=None) -> str:
        parts = []
        selected = self._resolve_coins(coins)
        is_all = len(selected) == len(self.symbols)
        allow = {s.upper() for s in selected}

        focus = "ALLE COINS" if is_all else ", ".join(s.replace("USDT", "") for s in selected)
        parts.append(
            "FOKUS-COINS: " + focus + "\n"
            "(Der Nutzer hat den Chat auf diese Coins eingegrenzt – beziehe dich "
            "ausschließlich auf ihre Marktdaten, KI-Strategien, Signale und Trades. "
            "Ignoriere alle anderen Assets, außer der Nutzer fragt ausdrücklich danach.)"
        )

        snaps = []
        for s in selected:
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
                   for s, d in self.decisions.items() if s.upper() in allow]
            if dec:
                parts.append("LETZTE KI-ENTSCHEIDUNGEN:\n" + "\n".join(dec))
        parts.append("OFFENE POSITIONEN:\n" + await self._open_trades_text(selected))
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
        """Reihenfolge der Modelle innerhalb des aktuellen Providers: bevorzugtes
        Modell zuerst, danach die restlichen des Providers."""
        provider = self.config.get("provider", "gemini")
        preferred = self.config.get("model") or (ALLOWED_MODELS.get(provider) or [""])[0]
        order = FALLBACK_ORDER.get(provider, [preferred])
        chain = [preferred] + [m for m in order if m != preferred]
        # Nur Modelle behalten, die zu diesem Provider gehören
        allowed = set(ALLOWED_MODELS.get(provider, []))
        return [m for m in chain if m in allowed]

    async def _gemini_generate_json(self, prompt: str, system: str) -> tuple[str, str]:
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
                raise
        raise last_err or RuntimeError("Alle Gemini-Modelle rate-limited")

    async def _openai_compat_generate_json(self, prompt: str, system: str) -> tuple[str, str]:
        """Ruft Groq / OpenRouter / Mistral via OpenAI-kompatibler API auf.
        JSON-Mode wird per response_format erzwungen (wo verfügbar)."""
        provider = self.config.get("provider")
        client = self._get_openai_client(provider)
        if client is None:
            raise RuntimeError(f"API-Key für Provider '{provider}' fehlt (Render EnvVars setzen)")

        last_err: Optional[Exception] = None
        for model in self._fallback_chain():
            try:
                kwargs = dict(
                    model=model,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=0.4,
                )
                # JSON-Mode aktivieren (unterstützt von Groq, Mistral, OpenRouter für viele Modelle)
                kwargs["response_format"] = {"type": "json_object"}
                try:
                    resp = await client.chat.completions.create(**kwargs)
                except Exception as inner:
                    # Manche Modelle akzeptieren response_format nicht -> ohne noch mal versuchen
                    if "response_format" in str(inner).lower() or "json_object" in str(inner).lower():
                        kwargs.pop("response_format", None)
                        resp = await client.chat.completions.create(**kwargs)
                    else:
                        raise
                text = (resp.choices[0].message.content or "").strip()
                if not text:
                    raise RuntimeError(f"Leere Antwort von {provider}/{model}")
                self._effective_model = model
                if model != self.config.get("model"):
                    logger.warning(f"AI analysis: Fallback auf {model} (Pref war {self.config.get('model')})")
                return text, model
            except Exception as e:
                last_err = e
                if _is_rate_limit_error(e):
                    logger.warning(f"{provider} {model} rate-limited, versuche nächstes Modell…")
                    continue
                raise
        raise last_err or RuntimeError(f"Alle Modelle von {provider} rate-limited")

    async def _generate_json(self, prompt: str, system: str) -> tuple[str, str]:
        """Provider-Dispatcher für JSON-Analyse. Gibt (raw_text, effektives_model) zurück."""
        provider = self.config.get("provider", "gemini")
        if provider == "gemini":
            return await self._gemini_generate_json(prompt, system)
        if provider in OPENAI_COMPAT_PROVIDERS:
            return await self._openai_compat_generate_json(prompt, system)
        raise RuntimeError(f"Unbekannter Provider: {provider}")

    async def run_analysis(self, manual: bool = False) -> Dict:
        if self._analyzing:
            return {"status": "busy", "detail": "Analyse läuft bereits"}
        if not self.key:
            self.last_error = f"API-Key für Provider '{self.config.get('provider')}' fehlt (Render EnvVars setzen)"
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
        logger.info("AI Trader engine loop started (multi-provider: gemini/groq/openrouter/mistral)")
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

    async def chat_stream(self, text: str, coins=None):
        """SSE-Streaming der KI-Antwort. Wechselt bei 429 automatisch das Modell
        innerhalb desselben Providers. Unterstützt Gemini + OpenAI-kompatible
        Provider (Groq, OpenRouter, Mistral).

        `coins`: optionale Liste der Symbole, auf die der Chat-Kontext
        eingegrenzt wird (leer / None / "ALL" => alle Coins)."""
        provider = self.config.get("provider", "gemini")
        if not self.key:
            yield f"⚠️ API-Key für Provider '{provider}' fehlt – bitte in Render EnvVars setzen."
            return

        hist_rows = await self.db.ai_chat.find({"role": {"$in": ["user", "assistant"]}}) \
            .sort("ts", -1).limit(14).to_list(14)
        hist_rows.reverse()
        history = "\n".join(
            f"{'Nutzer' if r['role'] == 'user' else 'KI'}: {r.get('text', '')}" for r in hist_rows
        ) or "(noch keine Nachrichten)"
        context = await self._context_brief(coins=coins)
        system = CHAT_SYSTEM_TEMPLATE.format(context=context, history=history)

        await self.db.ai_chat.insert_one({
            "id": str(uuid.uuid4()), "role": "user", "text": text, "ts": _now_iso(),
        })

        acc = ""
        last_err: Optional[Exception] = None
        streamed_any = False

        if provider == "gemini":
            from google.genai import types  # local import
            client = self._get_client()
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
        else:
            client = self._get_openai_client(provider)
            for model in self._fallback_chain():
                try:
                    stream = await client.chat.completions.create(
                        model=model,
                        messages=[
                            {"role": "system", "content": system},
                            {"role": "user", "content": text},
                        ],
                        temperature=0.6,
                        stream=True,
                    )
                    async for chunk in stream:
                        try:
                            part = chunk.choices[0].delta.content
                        except Exception:
                            part = None
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
                        logger.warning(f"{provider} chat {model} rate-limited, versuche nächstes Modell…")
                        continue
                    err = f"\n⚠️ KI-Fehler: {str(e)[:200]}"
                    acc += err
                    yield err
                    last_err = None
                    break

        if last_err is not None:
            err = f"\n⚠️ KI-Fehler: Alle Modelle von {provider} rate-limited. {str(last_err)[:150]}"
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
            "provider_keys": self._available_providers(),
            "analyzing": self._analyzing,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "last_error": self.last_error,
            "decisions": self.decisions,
            "allowed_models": ALLOWED_MODELS,
            "effective_model": self._effective_model,
        }


ai_engine = AIEngine()
