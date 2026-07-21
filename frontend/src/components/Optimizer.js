import React, { useState, useEffect, useRef, useCallback } from 'react';
import { X, Play, MagicWand, Trophy, CheckCircle, FloppyDisk } from '@phosphor-icons/react';
import { toast } from 'sonner';
import { authHeaders, isAdmin } from '../auth';
import SafeOverlay from './SafeOverlay';
import TIMEFRAMES from '../constants/timeframes';
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

const DAY_OPTIONS = [1, 2, 3, 5, 7, 14, 30, 60, 90, 180, 360];

const fmtEta = (s) => {
  if (s === null || s === undefined) return '';
  if (s >= 3600) return `~${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}min`;
  if (s >= 60) return `~${Math.floor(s / 60)}min ${s % 60}s`;
  return `~${s}s`;
};

export default function Optimizer({ onClose }) {
  const [mode, setMode] = useState('params');
  const [strategies, setStrategies] = useState([]);
  const [coins, setCoins] = useState([]);
  const [selStrategy, setSelStrategy] = useState('');
  const [selCoins, setSelCoins] = useState([]);
  const [days, setDays] = useState(3);
  const [timeframe, setTimeframe] = useState('1m');
  const [objective, setObjective] = useState('combo');
  const [iterations, setIterations] = useState(40);
  const [minTrades, setMinTrades] = useState(10);
  const [maxRules, setMaxRules] = useState(4);
  const [indicators, setIndicators] = useState(INDICATOR_POOL.map(i => i.id));
  const [includeTradeParams, setIncludeTradeParams] = useState(true);
  const [algorithm, setAlgorithm] = useState('random');
  const [baseStrategy, setBaseStrategy] = useState('');
  const [updateBase, setUpdateBase] = useState(false);
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const [saveName, setSaveName] = useState('');
  const [applied, setApplied] = useState(false);
  const [showApplyChoice, setShowApplyChoice] = useState(false);
  const [applying, setApplying] = useState(false);
  const [overrides, setOverrides] = useState([]);
  const pollRef = useRef(null);

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
      if (list.length) setSelStrategy(list[0].id);
    });
    fetch(`${API_URL}/api/coins`).then(r => r.json()).then(d => {
      setCoins(d.coins || []);
      setSelCoins((d.coins || []).slice(0, 1));
    });
    fetch(`${API_URL}/api/optimizer/results?limit=1`).then(r => r.json()).then(d => {
      const last = (d.results || [])[0];
      if (last?.result) { setResult(last.result); setMode(last.result.mode || 'params'); }
    }).catch(() => {});
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  const toggleCoin = (c) =>
    setSelCoins(selCoins.includes(c) ? selCoins.filter(x => x !== c) : [...selCoins, c]);

  const toggleInd = (id) =>
    setIndicators(indicators.includes(id) ? indicators.filter(x => x !== id) : [...indicators, id]);

  const poll = useCallback((jobId) => {
    pollRef.current = setInterval(async () => {
      try {
        const j = await fetch(`${API_URL}/api/optimizer/status/${jobId}`).then(r => r.json());
        setJob(j);
        if (j.status === 'done') {
          clearInterval(pollRef.current);
          setResult(j.result);
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

  const run = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    if (!selCoins.length) { toast.error('Mind. 1 Coin wählen'); return; }
    if (mode === 'params' && !selStrategy) { toast.error('Strategie wählen'); return; }
    if (mode !== 'params' && indicators.length === 0) { toast.error('Mind. 1 Indikator anhaken'); return; }
    try {
      const res = await fetch(`${API_URL}/api/optimizer/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({
          mode, strategy_id: selStrategy, symbols: selCoins, days, timeframe,
          objective, iterations, min_trades: minTrades, max_rules: maxRules,
          indicators: mode === 'params' ? undefined : indicators,
          include_trade_params: includeTradeParams,
          algorithm,
          base_strategy_id: mode !== 'params' && baseStrategy ? baseStrategy : undefined,
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
      <span className="mono neg">DD {fmt(m.max_drawdown)}</span>
    </>
  ) : null;

  return (
    <SafeOverlay className="opt-overlay" onClose={onClose}>
      <div className="opt-panel" onClick={e => e.stopPropagation()} data-testid="optimizer-modal">
        <div className="opt-header">
          <h2><MagicWand size={20} weight="bold" style={{ color: '#B388FF' }} /> STRATEGIE-OPTIMIZER</h2>
          <button className="opt-close" onClick={onClose} data-testid="optimizer-close"><X size={22} weight="bold" /></button>
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
          {mode !== 'discovery' && (
            <label className="opt-field">Iterationen
              <input type="number" min={5} max={300} value={iterations}
                onChange={e => setIterations(parseInt(e.target.value) || 40)} data-testid="opt-iterations" />
            </label>
          )}
          {mode !== 'params' && (
            <label className="opt-field">Max. Regeln
              <input type="number" min={1} max={6} value={maxRules}
                onChange={e => setMaxRules(parseInt(e.target.value) || 4)} data-testid="opt-max-rules" />
            </label>
          )}
          {mode === 'params' && (
            <label className="opt-check">
              <input type="checkbox" checked={includeTradeParams}
                onChange={e => setIncludeTradeParams(e.target.checked)} data-testid="opt-trade-params" />
              TP/SL mitoptimieren
            </label>
          )}
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

        <button className="opt-run" onClick={run} disabled={running} data-testid="opt-run">
          <Play size={15} weight="fill" /> {running ? 'Optimiert...' : 'Optimierung starten'}
        </button>

        {running && (
          <div className="opt-progress" data-testid="opt-progress">
            <div className="opt-progress-bar"><div style={{ width: `${job.progress || 0}%` }} /></div>
            <div className="opt-progress-row">
              <div className="opt-progress-text" data-testid="opt-progress-text">
                {job.phase} · {job.progress || 0}%
                {job.eta_seconds != null && <span className="opt-eta"> · Restzeit {fmtEta(job.eta_seconds)}</span>}
              </div>
              <button className="opt-cancel-run" onClick={cancel} data-testid="opt-cancel">
                <X size={13} weight="bold" /> Abbrechen
              </button>
            </div>
            {job.best?.metrics && (
              <div className="opt-best-live">Bester Stand: {metricsRow(job.best.metrics)}</div>
            )}
          </div>
        )}

        {result && !running && (
          <div className="opt-result" data-testid="opt-result">
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
                        <span key={k} className="opt-param-pill">{k}: <b>{v}</b></span>
                      ))}
                      {Object.entries(result.best?.trade_params || {}).map(([k, v]) => (
                        <span key={k} className="opt-param-pill trade">{k}: <b>{v}</b></span>
                      ))}
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
                      <thead><tr><th>#</th><th>Score</th><th>Trades</th><th>WR</th><th>PnL</th><th>Parameter</th></tr></thead>
                      <tbody>
                        {result.top.slice(0, 10).map((t, i) => (
                          <tr key={i}>
                            <td>{i + 1}</td>
                            <td className="mono">{fmt(t.score, 1)}</td>
                            <td>{t.metrics.trades}</td>
                            <td className={t.metrics.win_rate >= 50 ? 'pos' : 'neg'}>{fmt(t.metrics.win_rate, 1)}%</td>
                            <td className={`mono ${t.metrics.pnl >= 0 ? 'pos' : 'neg'}`}>{fmt(t.metrics.pnl)}</td>
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
