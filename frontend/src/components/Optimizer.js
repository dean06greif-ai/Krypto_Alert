import React, { useState, useEffect, useRef, useCallback } from 'react';
import { X, Play, MagicWand, Trophy, CheckCircle, FloppyDisk, ChartLine, Cloud, Desktop, Gear } from '@phosphor-icons/react';
import { toast } from 'sonner';
import { authHeaders, isAdmin } from '../auth';
import SafeOverlay from './SafeOverlay';
import LocalWorkerPanel from './LocalWorkerPanel';
import BenchmarkBar from './BenchmarkBar';
import TIMEFRAMES from '../constants/timeframes';
import EquityChart from './EquityChart';
import './Optimizer.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const fmt = (v, d = 2) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));

const MODES = [
  { id: 'params', title: 'Parameter-Optimierung', desc: 'Testet viele Parameter-Kombinationen einer bestehenden Strategie und findet die besten Einstellungen (inkl. TP/SL).' },
  { id: 'discovery', title: 'Strategie-Discovery', desc: 'Baut eine neue Strategie: fügt Regel für Regel den Indikator hinzu, der die Winrate am stärksten verbessert.' },
  { id: 'combo', title: 'Discovery + Optimierung', desc: 'Erst neue Strategie entdecken, dann die Schwellenwerte per Feintuning weiter optimieren.' },
];

const INDICATOR_POOL = [
  { id: 'rsi', label: 'RSI' },
  { id: 'ema_fast', label: 'EMA Fast' },
  { id: 'ema_slow', label: 'EMA Slow' },
  { id: 'macd', label: 'MACD Cross' },
  { id: 'macd_hist', label: 'MACD Histogramm' },
  { id: 'bb_lower', label: 'Bollinger Reversion' },
  { id: 'bb_upper', label: 'Bollinger Breakout' },
  { id: 'bb_width_pct', label: 'Bollinger Breite' },
  { id: 'stoch_k', label: 'Stochastik' },
  { id: 'vwap', label: 'VWAP' },
  { id: 'rel_volume', label: 'Rel. Volumen' },
  { id: 'ha_color', label: 'Heikin-Ashi' },
  { id: 'price_change_pct', label: 'Momentum %' },
  { id: 'atr_pct', label: 'ATR %' },
];

const OBJECTIVES = [
  { v: 'combo', l: 'Kombi (PnL × Winrate)' },
  { v: 'win_rate', l: 'Höchste Win-Rate' },
  { v: 'pnl', l: 'Höchster PnL' },
];

const ALGORITHMS = [
  { v: 'random', l: 'Random Search' },
  { v: 'bayes', l: 'Bayes (TPE) – schneller zum Optimum' },
];

const OPT_GROUPS = [
  { k: 'tpsl', l: 'TP/SL optimieren', d: 'TP1/Full-CRV, SL-Modus (Struktur/ATR/Fest), SL-Lookback, ATR-Puffer, TP1-%' },
  { k: 'breakeven', l: 'Break-Even optimieren', d: 'BE-Modus (TP1/CRV/Gewinn-%) + Trigger' },
  { k: 'profit_secure', l: 'Gewinnsicherung optimieren', d: 'An/Aus, Auslöser-%, gesicherter Anteil' },
  { k: 'leverage', l: 'Hebel optimieren', d: 'Fester Hebel 3x–50x' },
  { k: 'auto_leverage', l: 'Auto-Leverage optimieren', d: 'An/Aus, Modus (% oder Ticks hinter Stop), Abstand, Max-Hebel' },
  { k: 'sessions', l: 'Zeitfenster optimieren', d: '24/7 vs. typische Handelsfenster' },
];

const DAY_OPTIONS = [1, 2, 3, 5, 7, 14, 30, 60, 90, 180, 360, 540, 720, 900, 1080, 1440];

const STATE_KEY = 'opt_ui_state_v1';
const loadState = () => {
  try { return JSON.parse(localStorage.getItem(STATE_KEY)) || {}; } catch { return {}; }
};

const fmtEta = (s) => {
  if (s === null || s === undefined) return '';
  if (s >= 3600) return `~${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}min`;
  if (s >= 60) return `~${Math.floor(s / 60)}min ${s % 60}s`;
  return `~${s}s`;
};

export default function Optimizer({ onClose }) {
  const saved = useRef(loadState()).current;
  const [mode, setMode] = useState(saved.mode || 'params');
  const [strategies, setStrategies] = useState([]);
  const [coins, setCoins] = useState([]);
  const [selStrategy, setSelStrategy] = useState(saved.selStrategy || '');
  const [selCoins, setSelCoins] = useState(saved.selCoins || []);
  const [days, setDays] = useState(saved.days ?? 3);
  const [timeframe, setTimeframe] = useState(saved.timeframe || '1m');
  const [optSessions, setOptSessions] = useState(saved.optSessions || '');
  const [objective, setObjective] = useState(saved.objective || 'combo');
  const [iterations, setIterations] = useState(saved.iterations ?? 40);
  const [minTrades, setMinTrades] = useState(saved.minTrades ?? 10);
  const [maxRules, setMaxRules] = useState(saved.maxRules ?? 4);
  const [indicators, setIndicators] = useState(saved.indicators || INDICATOR_POOL.map(i => i.id));
  const [optFlags, setOptFlags] = useState(saved.optFlags || { tpsl: true });
  const [algorithm, setAlgorithm] = useState(saved.algorithm || 'random');
  const [baseStrategy, setBaseStrategy] = useState(saved.baseStrategy || '');
  const [updateBase, setUpdateBase] = useState(false);
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const [saveName, setSaveName] = useState('');
  const [applied, setApplied] = useState(false);
  const [showApplyChoice, setShowApplyChoice] = useState(false);
  const [applying, setApplying] = useState(false);
  const [overrides, setOverrides] = useState([]);
  const [ram, setRam] = useState(null);
  // Equity-Chart im Optimizer: Standard AUS (Performance), on-demand geladen.
  const [showEquity, setShowEquity] = useState(false);
  const [equityScope, setEquityScope] = useState('optimized'); // 'optimized' | 'all'
  const [equityPoints, setEquityPoints] = useState(null);
  const [equityLoading, setEquityLoading] = useState(false);
  const [equityJobId, setEquityJobId] = useState(null);
  const [execution, setExecution] = useState(saved.execution || 'cloud');
  const [lwOnline, setLwOnline] = useState(false);
  const [showLW, setShowLW] = useState(false);
  const pollRef = useRef(null);

  // ---- QoL: Auswahl lokal merken (bleibt beim Schließen/Neuöffnen erhalten) ----
  useEffect(() => {
    try {
      localStorage.setItem(STATE_KEY, JSON.stringify({
        mode, selStrategy, selCoins, days, timeframe, objective, iterations,
        minTrades, maxRules, indicators, optFlags, algorithm, baseStrategy, optSessions,
        execution,
      }));
    } catch { /* ignore */ }
  }, [mode, selStrategy, selCoins, days, timeframe, objective, iterations,
    minTrades, maxRules, indicators, optFlags, algorithm, baseStrategy, optSessions,
    execution]);

  // ---- Lokaler Worker: Online-Status für die Ausführungs-Auswahl ----
  useEffect(() => {
    const check = () => fetch(`${API_URL}/api/localworker/status`).then(r => r.json())
      .then(d => setLwOnline(!!d.online)).catch(() => setLwOnline(false));
    check();
    const iv = setInterval(check, 10000);
    return () => clearInterval(iv);
  }, []);

  const loadRam = () => {
    fetch(`${API_URL}/api/system/ram`).then(r => r.json()).then(setRam).catch(() => {});
  };

  const clearCache = async () => {
    try {
      const d = await fetch(`${API_URL}/api/system/cache/clear`, {
        method: 'POST', headers: authHeaders(),
      }).then(r => r.json());
      toast.success(`Cache geleert (${d.candles_freed || 0} Kerzen freigegeben)`);
      loadRam();
    } catch { toast.error('Verbindungsfehler'); }
  };

  const loadEquity = async (scope) => {
    if (!equityJobId) { toast.error('Keine Job-ID – bitte Optimierung neu starten'); return; }
    setEquityLoading(true);
    setEquityScope(scope);
    setEquityPoints(null); // sofort "lädt..."-Zustand zeigen, kein stale render zwischen scope-Wechsel
    try {
      const r = await fetch(`${API_URL}/api/optimizer/equity/${equityJobId}?scope=${scope}`);
      const d = await r.json();
      if (!r.ok) { toast.error(d.detail || 'Equity-Simulation fehlgeschlagen'); return; }
      setEquityPoints(d.points || []);
      if ((d.points || []).length === 0) {
        toast.info('Keine geschlossenen Trades im simulierten Zeitraum');
      }
    } catch { toast.error('Verbindungsfehler bei Equity-Simulation'); }
    finally { setEquityLoading(false); }
  };

  const toggleEquity = async () => {
    const next = !showEquity;
    setShowEquity(next);
    if (next && !equityPoints) await loadEquity('optimized');
  };

  const loadOverrides = useCallback((sid) => {
    if (!sid) { setOverrides([]); return; }
    fetch(`${API_URL}/api/optimizer/overrides/${sid}`)
      .then(r => r.json())
      .then(d => setOverrides(d.symbols || []))
      .catch(() => setOverrides([]));
  }, []);

  useEffect(() => {
    if (mode === 'params') loadOverrides(selStrategy);
  }, [selStrategy, mode, loadOverrides]);

  const recTf = strategies.find(s => s.id === selStrategy)?.timeframe;

  useEffect(() => {
    if (mode === 'params' && recTf) setTimeframe(recTf);
  }, [selStrategy, mode, recTf]);

  useEffect(() => {
    fetch(`${API_URL}/api/strategies`).then(r => r.json()).then(d => {
      const list = d.strategies || [];
      setStrategies(list);
      setSelStrategy(prev => (prev && list.some(s => s.id === prev)) ? prev : (list[0]?.id || ''));
    });
    fetch(`${API_URL}/api/coins`).then(r => r.json()).then(d => {
      const cs = d.coins || [];
      setCoins(cs);
      setSelCoins(prev => {
        const valid = (prev || []).filter(c => cs.includes(c));
        return valid.length ? valid : cs.slice(0, 1);
      });
    });
    fetch(`${API_URL}/api/optimizer/results?limit=1`).then(r => r.json()).then(d => {
      const last = (d.results || [])[0];
      if (last?.result) {
        setResult(last.result);
        if (last.id) setEquityJobId(last.id);
      }
    }).catch(() => {});
    // Läuft gerade eine Optimierung? -> Fortschritt & Abbrechen wieder anzeigen
    fetch(`${API_URL}/api/optimizer/active`).then(r => r.json()).then(d => {
      if (d.active) { setJob(d.active); poll(d.active.id); }
    }).catch(() => {});
    loadRam();
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleCoin = (c) =>
    setSelCoins(selCoins.includes(c) ? selCoins.filter(x => x !== c) : [...selCoins, c]);

  const toggleInd = (id) =>
    setIndicators(indicators.includes(id) ? indicators.filter(x => x !== id) : [...indicators, id]);

  const toggleOpt = (k) =>
    setOptFlags(prev => ({ ...prev, [k]: !prev[k] }));

  const poll = useCallback((jobId) => {
    pollRef.current = setInterval(async () => {
      try {
        const j = await fetch(`${API_URL}/api/optimizer/status/${jobId}`).then(r => r.json());
        setJob(j);
        if (j.status === 'done') {
          clearInterval(pollRef.current);
          setResult(j.result);
          setEquityJobId(jobId);
          setEquityPoints(null);
          setShowEquity(false);
          setApplied(false);
          toast.success('Optimierung abgeschlossen');
        } else if (j.status === 'error') {
          clearInterval(pollRef.current);
          toast.error(`Optimierung fehlgeschlagen: ${j.error}`);
        } else if (j.status === 'cancelled') {
          clearInterval(pollRef.current);
          toast.info('Optimierung abgebrochen');
        }
      } catch { /* keep polling */ }
    }, 1500);
  }, []);

  const cancel = async () => {
    if (!job?.id) return;
    try {
      await fetch(`${API_URL}/api/optimizer/cancel/${job.id}`, {
        method: 'POST', headers: authHeaders(),
      });
      toast.info('Abbruch angefordert...');
    } catch { toast.error('Verbindungsfehler'); }
  };

  const forceReset = async () => {
    try {
      await fetch(`${API_URL}/api/optimizer/reset`, { method: 'POST', headers: authHeaders() });
      if (pollRef.current) clearInterval(pollRef.current);
      setJob(null);
      toast.success('Optimizer zurückgesetzt – neue Läufe sind wieder möglich');
    } catch { toast.error('Verbindungsfehler'); }
  };

  const run = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    if (!selCoins.length) { toast.error('Mind. 1 Coin wählen'); return; }
    if (mode === 'params' && !selStrategy) { toast.error('Strategie wählen'); return; }
    if (mode !== 'params' && indicators.length === 0) { toast.error('Mind. 1 Indikator anhaken'); return; }
    if (execution === 'local' && !lwOnline) {
      toast.error('Kein lokaler Worker verbunden – Worker starten oder Cloud wählen');
      setShowLW(true);
      return;
    }
    try {
      const res = await fetch(`${API_URL}/api/optimizer/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({
          mode, strategy_id: selStrategy, symbols: selCoins, days, timeframe,
          objective, iterations, min_trades: minTrades, max_rules: maxRules,
          indicators: mode === 'params' ? undefined : indicators,
          optimize: optFlags,
          include_trade_params: !!optFlags.tpsl,
          algorithm,
          sessions: optSessions.trim() || undefined,
          base_strategy_id: mode !== 'params' && baseStrategy ? baseStrategy : undefined,
          execution,
        }),
      });
      const d = await res.json();
      if (!res.ok) { toast.error(d.detail || 'Start fehlgeschlagen'); return; }
      setResult(null);
      setApplied(false);
      setJob({ id: d.job_id, status: 'running', progress: 0, phase: 'Startet...' });
      poll(d.job_id);
    } catch { toast.error('Verbindungsfehler'); }
  };

  const applyParams = async (scope) => {
    const best = result?.best;
    if (!best) return;
    setApplying(true);
    try {
      const res = await fetch(`${API_URL}/api/optimizer/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ type: 'params', strategy_id: result.strategy_id,
          params: best.params, trade_params: best.trade_params,
          scope, symbols: scope === 'coins' ? result.symbols : undefined }),
      });
      const d = await res.json();
      if (!res.ok) { toast.error(d.detail || 'Übernahme fehlgeschlagen'); return; }
      setApplied(true);
      setShowApplyChoice(false);
      loadOverrides(result.strategy_id);
      toast.success(scope === 'coins'
        ? `Einstellungen für ${(result.symbols || []).map(s => s.replace('USDT', '')).join(', ')} übernommen (Coin-spezifisch)`
        : 'Einstellungen global für alle Coins übernommen');
    } catch { toast.error('Verbindungsfehler'); }
    finally { setApplying(false); }
  };

  const applyToBacktester = async () => {
    const best = result?.best;
    if (!best) return;
    try {
      const res = await fetch(`${API_URL}/api/optimizer/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ type: 'backtest', strategy_id: result.strategy_id,
          params: best.params, trade_params: best.trade_params, timeframe: result.timeframe }),
      });
      const d = await res.json();
      if (!res.ok) { toast.error(d.detail || 'Übernahme fehlgeschlagen'); return; }
      toast.success('In Backtester übernommen – Strategie dort auswählen & testen');
    } catch { toast.error('Verbindungsfehler'); }
  };

  const saveStrategy = async () => {
    if (!result?.definition) return;
    try {
      const res = await fetch(`${API_URL}/api/optimizer/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ type: 'strategy', definition: result.definition,
          name: saveName || undefined, timeframe: result.timeframe,
          sessions: result.sessions || undefined,
          trade_params: result.trade_params || undefined,
          update_strategy_id: updateBase && result.base_strategy_id ? result.base_strategy_id : undefined }),
      });
      const d = await res.json();
      if (!res.ok) { toast.error(d.detail || 'Speichern fehlgeschlagen'); return; }
      setApplied(true);
      toast.success(d.updated
        ? 'Basis-Strategie aktualisiert – Änderungen sind sofort aktiv'
        : 'Strategie gespeichert & aktiviert – sichtbar in den Strategie-Tabs');
    } catch { toast.error('Verbindungsfehler'); }
  };

  const running = job?.status === 'running';
  const metricsRow = (m) => m ? (
    <>
      <span>{m.trades} Trades</span>
      <span className={m.win_rate >= 50 ? 'pos' : 'neg'}>{fmt(m.win_rate, 1)}% WR</span>
      <span className={`mono ${m.pnl >= 0 ? 'pos' : 'neg'}`}>{fmt(m.pnl)} PnL</span>
      {m.pnl_pct !== undefined && (
        <span className={`mono ${m.pnl_pct >= 0 ? 'pos' : 'neg'}`}>{fmt(m.pnl_pct, 1)}% PnL</span>
      )}
      <span className="mono neg">DD {fmt(m.max_drawdown)}</span>
      {m.max_drawdown_pct !== undefined && (
        <span className="mono neg">DD {fmt(m.max_drawdown_pct, 1)}%</span>
      )}
    </>
  ) : null;

  const tradeParamPills = (tp) => Object.entries(tp || {}).map(([k, v]) => (
    <span key={k} className="opt-param-pill trade">{k}: <b>{String(v)}</b></span>
  ));

  return (
    <SafeOverlay className="opt-overlay" onClose={onClose}>
      <div className="opt-panel" onClick={e => e.stopPropagation()} data-testid="optimizer-modal">
        <div className="opt-header">
          <h2><MagicWand size={20} weight="bold" style={{ color: '#B388FF' }} /> STRATEGIE-OPTIMIZER</h2>
          <button className="opt-close" onClick={onClose} data-testid="optimizer-close"><X size={22} weight="bold" /></button>
        </div>

        <div className="opt-row" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}
          data-testid="opt-ram-row">
          <span style={{ fontSize: 11, color: '#8A8FA3' }} data-testid="opt-ram-info">
            {ram ? `RAM Backend: ${ram.process_rss_mb} MB · System: ${ram.system_used_percent}% belegt · Kerzen-Cache: ${(ram.candle_cache?.total_candles || 0).toLocaleString('de-DE')} Kerzen (~${ram.candle_cache?.estimated_mb} MB)` : 'RAM-Info lädt...'}
          </span>
          <button className="opt-chip" style={{ fontSize: 11 }} onClick={loadRam} data-testid="opt-ram-refresh">↻ RAM</button>
          <button className="opt-chip" style={{ fontSize: 11 }} onClick={clearCache} data-testid="opt-clear-cache">Cache leeren</button>
        </div>

        <div className="opt-modes">
          {MODES.map(m => (
            <button key={m.id} className={`opt-mode ${mode === m.id ? 'on' : ''}`}
              onClick={() => setMode(m.id)} data-testid={`opt-mode-${m.id}`}>
              <div className="opt-mode-title">{m.title}</div>
              <div className="opt-mode-desc">{m.desc}</div>
            </button>
          ))}
        </div>

        <div className="opt-setup">
          {mode === 'params' && (
            <label className="opt-field">Strategie
              <select value={selStrategy} onChange={e => setSelStrategy(e.target.value)} data-testid="opt-strategy">
                {strategies.map(s => <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </label>
          )}
          <label className="opt-field">Timeframe
            {mode === 'params' && recTf && (
              <span data-testid="opt-rec-tf" style={{ color: '#B388FF', fontSize: '0.8em', marginLeft: 6 }}>
                (Empfohlen: {TIMEFRAMES.find(t => t.v === recTf)?.l || recTf})
              </span>
            )}
            <select value={timeframe} onChange={e => setTimeframe(e.target.value)} data-testid="opt-timeframe">
              {TIMEFRAMES.map(t => <option key={t.v} value={t.v}>{t.l}</option>)}
            </select>
          </label>
          <label className="opt-field">Zeitraum
            <select value={days} onChange={e => setDays(parseInt(e.target.value))} data-testid="opt-days">
              {DAY_OPTIONS.map(d => <option key={d} value={d}>{d} Tag{d > 1 ? 'e' : ''}</option>)}
            </select>
          </label>
          <label className="opt-field">Zeitfenster (optional)
            <input type="text" placeholder="z.B. 15:00-18:00 · leer = 24h" value={optSessions}
              onChange={e => setOptSessions(e.target.value)} data-testid="opt-sessions"
              title="Festes Handels-Zeitfenster (Berlin-Zeit) für die Optimierung vorgeben, z.B. 15:00-18:00 oder 09:00-12:00,15:00-18:00" />
          </label>
          {mode === 'params' && (
            <label className="opt-field">Algorithmus
              <select value={algorithm} onChange={e => setAlgorithm(e.target.value)} data-testid="opt-algorithm">
                {ALGORITHMS.map(a => <option key={a.v} value={a.v}>{a.l}</option>)}
              </select>
            </label>
          )}
          {mode !== 'params' && (
            <label className="opt-field">Basis-Strategie (weiterentwickeln)
              <select value={baseStrategy} onChange={e => setBaseStrategy(e.target.value)} data-testid="opt-base-strategy">
                <option value="">– Neue Strategie von Null –</option>
                {strategies.filter(s => s.is_custom).map(s =>
                  <option key={s.id} value={s.id}>{s.name}</option>)}
              </select>
            </label>
          )}
          <label className="opt-field">Ziel
            <select value={objective} onChange={e => setObjective(e.target.value)} data-testid="opt-objective">
              {OBJECTIVES.map(o => <option key={o.v} value={o.v}>{o.l}</option>)}
            </select>
          </label>
          <label className="opt-field">Min. Trades
            <input type="number" min={1} value={minTrades}
              onChange={e => setMinTrades(parseInt(e.target.value) || 1)} data-testid="opt-min-trades" />
          </label>
          <label className="opt-field">Iterationen
            <input type="number" min={5} max={300} value={iterations}
              onChange={e => setIterations(parseInt(e.target.value) || 40)} data-testid="opt-iterations" />
          </label>
          {mode !== 'params' && (
            <label className="opt-field">Max. Regeln
              <input type="number" min={1} max={6} value={maxRules}
                onChange={e => setMaxRules(parseInt(e.target.value) || 4)} data-testid="opt-max-rules" />
            </label>
          )}
        </div>

        <div className="opt-row">
          <div className="opt-label">
            WAS SOLL MITOPTIMIERT WERDEN? {mode !== 'params' && '(Regeln werden bei Discovery immer optimiert)'}
          </div>
          <div className="opt-chips">
            {OPT_GROUPS.map(g => (
              <button key={g.k} className={`opt-chip ${optFlags[g.k] ? 'on' : ''}`}
                onClick={() => toggleOpt(g.k)} title={g.d} data-testid={`opt-flag-${g.k}`}>
                {optFlags[g.k] ? '☑' : '☐'} {g.l}
              </button>
            ))}
          </div>
          <div className="opt-override-legend" style={{ marginTop: 4 }}>
            Achtung: Jede zusätzliche Gruppe vergrößert den Suchraum – ggf. mehr Iterationen wählen.
          </div>
        </div>

        <div className="opt-row">
          <div className="opt-label">COINS</div>
          <div className="opt-chips">
            {coins.map(c => (
              <button key={c} className={`opt-chip ${selCoins.includes(c) ? 'on' : ''} ${overrides.includes(c) ? 'has-override' : ''}`}
                onClick={() => toggleCoin(c)} data-testid={`opt-coin-${c}`}
                title={overrides.includes(c) ? 'Coin-spezifische Optimizer-Einstellungen aktiv' : undefined}>
                {c.replace('USDT', '')}
                {overrides.includes(c) && <span className="opt-chip-dot" data-testid={`opt-override-dot-${c}`} />}
              </button>
            ))}
          </div>
          {mode === 'params' && overrides.length > 0 && (
            <div className="opt-override-legend" data-testid="opt-override-legend">
              <span className="opt-chip-dot inline" /> Coin-spezifische Einstellungen aktiv: {overrides.map(s => s.replace('USDT', '')).join(', ')}
            </div>
          )}
        </div>

        {mode !== 'params' && (
          <div className="opt-row">
            <div className="opt-label">INDIKATOREN FÜR DIE SUCHE (Häkchen = wird getestet)</div>
            <div className="opt-chips">
              {INDICATOR_POOL.map(i => (
                <button key={i.id} className={`opt-chip ${indicators.includes(i.id) ? 'on' : ''}`}
                  onClick={() => toggleInd(i.id)} data-testid={`opt-ind-${i.id}`}>
                  {i.label}
                </button>
              ))}
            </div>
          </div>
        )}

        <div className="opt-exec-row" style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', margin: '10px 0 4px' }}>
          <div className="bt-exec" data-testid="opt-execution-toggle">
            <span className="bt-exec-label">Ausführung</span>
            <button className={`bt-exec-btn ${execution === 'cloud' ? 'on' : ''}`}
              onClick={() => setExecution('cloud')} data-testid="opt-exec-cloud"
              title="Berechnung auf dem Server (wie bisher)">
              <Cloud size={13} weight="bold" /> Cloud
            </button>
            <button className={`bt-exec-btn ${execution === 'local' ? 'on' : ''}`}
              onClick={() => setExecution('local')} data-testid="opt-exec-local"
              title="Berechnung auf deinem PC über den lokalen Worker – identische Ergebnisse, nutzt lokal gespeicherte Kerzendaten">
              <Desktop size={13} weight="bold" /> Lokal
              <span className={`bt-exec-dot ${lwOnline ? 'on' : ''}`} data-testid="opt-exec-dot" />
            </button>
            <button className="bt-exec-manage" onClick={() => setShowLW(true)}
              title="Lokale Ausführung verwalten: Worker, Einstellungen & Marktdaten"
              data-testid="opt-exec-manage">
              <Gear size={13} weight="bold" />
            </button>
          </div>
        </div>
        {showLW && <LocalWorkerPanel onClose={() => setShowLW(false)} />}

        <button className="opt-run" onClick={run} disabled={running} data-testid="opt-run">
          <Play size={15} weight="fill" /> {running ? 'Optimiert...' : 'Optimierung starten'}
        </button>

        {running && (
          <div className="opt-progress" data-testid="opt-progress">
            <div className="opt-progress-bar"><div style={{ width: `${job.progress || 0}%` }} /></div>
            <div className="opt-progress-row">
              <div className="opt-progress-text" data-testid="opt-progress-text">
                {(job.execution === 'local' || job.params?.execution === 'local') &&
                  <span className="bt-exec-tag" data-testid="opt-local-tag">💻 Lokal</span>}
                {job.phase} · {job.progress || 0}%
                {job.eta_seconds != null && <span className="opt-eta"> · Restzeit {fmtEta(job.eta_seconds)}</span>}
              </div>
              <button className="opt-cancel-run" onClick={cancel} data-testid="opt-cancel">
                <X size={13} weight="bold" /> Abbrechen
              </button>
              <button className="opt-cancel-run" onClick={forceReset} data-testid="opt-force-reset"
                title="Notfall: hängende Optimierung sofort freigeben">
                <X size={13} weight="bold" /> Zurücksetzen (Notfall)
              </button>
            </div>
            {job.best?.metrics && (
              <div className="opt-best-live">Bester Stand: {metricsRow(job.best.metrics)}</div>
            )}
          </div>
        )}

        {result && !running && (
          <div className="opt-result" data-testid="opt-result">
            <div className="opt-equity-toggle" style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', margin: '4px 0 10px' }}>
              <button className={`opt-chip ${showEquity ? 'on' : ''}`}
                onClick={toggleEquity} data-testid="opt-equity-toggle"
                style={{ fontSize: 12, fontWeight: 600 }}>
                <ChartLine size={13} weight="bold" style={{ verticalAlign: -2, marginRight: 4 }} />
                {showEquity ? 'Equity-Kurve ausblenden' : 'Equity-Kurve anzeigen'}
              </button>
              {showEquity && equityJobId && (
                <>
                  <button className={`opt-chip ${equityScope === 'optimized' ? 'on' : ''}`}
                    onClick={() => loadEquity('optimized')} disabled={equityLoading}
                    data-testid="opt-equity-scope-optimized"
                    style={{ fontSize: 11 }}
                    title="Nur die im Lauf verwendeten Coins">
                    Nur optimierte Coins
                  </button>
                  <button className={`opt-chip ${equityScope === 'all' ? 'on' : ''}`}
                    onClick={() => loadEquity('all')} disabled={equityLoading}
                    data-testid="opt-equity-scope-all"
                    style={{ fontSize: 11 }}
                    title="Auch andere Coins simulieren – zeigt wie robust die Strategie ist">
                    Auch andere Coins prüfen
                  </button>
                  {equityLoading && (
                    <span style={{ fontSize: 11, color: '#8A8FA3' }} data-testid="opt-equity-loading">
                      Simuliere…
                    </span>
                  )}
                </>
              )}
              <span style={{ fontSize: 10, color: '#8A8FA3', marginLeft: 4 }}>
                Standard AUS wegen Performance – bei mehreren Coins/Strategien kurz Geduld.
              </span>
            </div>
            {showEquity && (
              <div data-testid="opt-equity-chart-wrap" style={{ marginBottom: 12 }}>
                <EquityChart points={equityPoints} csvHref={null}
                  title={`EQUITY-KURVE · ${equityScope === 'all' ? 'ALLE COINS (Robustheit)' : 'Optimierte Coins'}`} />
              </div>
            )}
            {result.benchmark && <BenchmarkBar b={result.benchmark} testid="opt-benchmark" />}
            {result.mode === 'params' ? (
              <>
                <div className="opt-section-title">
                  <Trophy size={15} weight="fill" style={{ color: '#FFD700' }} />
                  ERGEBNIS · {result.strategy_name} · {result.timeframe} · {result.days} Tage
                </div>
                <div className="opt-compare">
                  <div className="opt-card">
                    <div className="opt-card-title">AKTUELLE EINSTELLUNGEN</div>
                    <div className="opt-metrics">{metricsRow(result.baseline?.metrics)}</div>
                  </div>
                  <div className="opt-card best">
                    <div className="opt-card-title">OPTIMIERT {result.best?.is_baseline && '(keine Verbesserung gefunden)'}</div>
                    <div className="opt-metrics">{metricsRow(result.best?.metrics)}</div>
                  </div>
                </div>
                {!result.best?.is_baseline && (
                  <>
                    <div className="opt-params-list" data-testid="opt-best-params">
                      {Object.entries(result.best?.params || {}).map(([k, v]) => (
                        <span key={k} className="opt-param-pill">{k}: <b>{String(v)}</b></span>
                      ))}
                      {tradeParamPills(result.best?.trade_params)}
                    </div>
                    <button className="opt-apply" onClick={() => setShowApplyChoice(v => !v)} disabled={applied} data-testid="opt-apply-params">
                      <CheckCircle size={15} weight="bold" />
                      {applied ? 'Übernommen ✓' : 'Beste Parameter übernehmen (Live/Paper Einstellungen)'}
                    </button>
                    {showApplyChoice && !applied && (
                      <div className="opt-apply-choice" data-testid="opt-apply-choice">
                        <div className="opt-apply-choice-title">Wofür sollen die Einstellungen gelten?</div>
                        <button className="opt-apply-choice-btn coins" onClick={() => applyParams('coins')} disabled={applying} data-testid="opt-apply-scope-coins">
                          <b>Nur für optimierte Coins</b>
                          <span>{(result.symbols || []).map(s => s.replace('USDT', '')).join(', ')} – Coin-spezifische Overrides</span>
                        </button>
                        <button className="opt-apply-choice-btn global" onClick={() => applyParams('global')} disabled={applying} data-testid="opt-apply-scope-global">
                          <b>Für alle Coins</b>
                          <span>Einstellungen gelten global für die Strategie</span>
                        </button>
                        <button className="opt-apply-choice-cancel" onClick={() => setShowApplyChoice(false)} data-testid="opt-apply-scope-cancel">Abbrechen</button>
                      </div>
                    )}
                    <button className="opt-apply opt-apply-bt" onClick={applyToBacktester} data-testid="opt-apply-backtest">
                      <FloppyDisk size={15} weight="bold" />
                      In Backtester übernehmen (dort direkt testen)
                    </button>
                  </>
                )}
                {(result.top || []).length > 0 && (
                  <div className="opt-table-wrap">
                    <table className="opt-table">
                      <thead><tr><th>#</th><th>Score</th><th>Trades</th><th>WR</th><th>PnL</th><th>PnL %</th><th>Parameter</th></tr></thead>
                      <tbody>
                        {result.top.slice(0, 10).map((t, i) => (
                          <tr key={i}>
                            <td>{i + 1}</td>
                            <td className="mono">{fmt(t.score, 1)}</td>
                            <td>{t.metrics.trades}</td>
                            <td className={t.metrics.win_rate >= 50 ? 'pos' : 'neg'}>{fmt(t.metrics.win_rate, 1)}%</td>
                            <td className={`mono ${t.metrics.pnl >= 0 ? 'pos' : 'neg'}`}>{fmt(t.metrics.pnl)}</td>
                            <td className={`mono ${(t.metrics.pnl_pct || 0) >= 0 ? 'pos' : 'neg'}`}>{t.metrics.pnl_pct !== undefined ? `${fmt(t.metrics.pnl_pct, 1)}%` : '–'}</td>
                            <td className="opt-small">{Object.entries({ ...t.params, ...t.trade_params }).map(([k, v]) => `${k}=${v}`).join(' · ')}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </>
            ) : (
              <>
                <div className="opt-section-title">
                  <Trophy size={15} weight="fill" style={{ color: '#FFD700' }} />
                  ENTDECKTE STRATEGIE · {result.timeframe} · {result.days} Tage
                </div>
                {result.metrics ? (
                  <div className="opt-card best">
                    <div className="opt-metrics">{metricsRow(result.metrics)}</div>
                  </div>
                ) : (
                  <div className="opt-empty">Keine Regel-Kombination hat die Mindest-Trades erreicht – Zeitraum erhöhen oder Min. Trades senken.</div>
                )}
                <div className="opt-rules">
                  <div className="opt-rules-col">
                    <div className="opt-rules-title pos">LONG-REGELN</div>
                    {(result.rules?.long || []).map((r, i) => <div key={i} className="opt-rule">{r}</div>)}
                  </div>
                  <div className="opt-rules-col">
                    <div className="opt-rules-title neg">SHORT-REGELN</div>
                    {(result.rules?.short || []).map((r, i) => <div key={i} className="opt-rule">{r}</div>)}
                  </div>
                </div>
                {result.trade_params && Object.keys(result.trade_params).length > 0 && (
                  <div className="opt-params-list" data-testid="opt-discovery-trade-params">
                    <span style={{ fontSize: 11, color: '#8A8FA3', alignSelf: 'center' }}>BESTE TRADE-EINSTELLUNGEN:</span>
                    {tradeParamPills(result.trade_params)}
                  </div>
                )}
                {(result.steps || []).length > 0 && (
                  <div className="opt-steps">
                    <div className="opt-label">SUCH-VERLAUF</div>
                    {result.steps.map((s, i) => (
                      <div key={i} className="opt-step">
                        <span className="opt-step-round">Runde {s.round}</span>
                        {s.added
                          ? <span>+ {s.added} → {s.metrics?.trades} Trades · {fmt(s.metrics?.win_rate, 1)}% WR · {fmt(s.metrics?.pnl)} PnL</span>
                          : <span className="opt-small">{s.info}</span>}
                      </div>
                    ))}
                    {(result.refine_log || []).map((s, i) => (
                      <div key={`r${i}`} className="opt-step">
                        <span className="opt-step-round tune">Tuning</span>
                        <span>{s.change} → {fmt(s.metrics?.win_rate, 1)}% WR · {fmt(s.metrics?.pnl)} PnL</span>
                      </div>
                    ))}
                  </div>
                )}
                {result.metrics && (
                  <div className="opt-save-row">
                    <input type="text" placeholder="Name der neuen Strategie"
                      value={saveName} onChange={e => setSaveName(e.target.value)}
                      data-testid="opt-save-name" />
                    {result.base_strategy_id && (
                      <label className="opt-check" style={{ whiteSpace: 'nowrap' }}>
                        <input type="checkbox" checked={updateBase}
                          onChange={e => setUpdateBase(e.target.checked)} data-testid="opt-update-base" />
                        Basis-Strategie aktualisieren
                      </label>
                    )}
                    <button className="opt-apply" onClick={saveStrategy} disabled={applied} data-testid="opt-save-strategy">
                      <FloppyDisk size={15} weight="bold" />
                      {applied ? 'Gespeichert ✓' : (updateBase && result.base_strategy_id ? 'Basis-Strategie aktualisieren' : 'Als Strategie speichern & aktivieren')}
                    </button>
                  </div>
                )}
              </>
            )}
          </div>
        )}

        {!result && !running && (
          <div className="opt-empty" data-testid="opt-empty">
            Wähle einen Modus und starte die Optimierung – der Algorithmus testet automatisch
            hunderte Kombinationen auf echten historischen Daten.
          </div>
        )}
      </div>
    </SafeOverlay>
  );
}
