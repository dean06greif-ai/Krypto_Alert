import React, { useState, useEffect, useRef, useCallback } from 'react';
import { X, Play, Trophy, ClockCounterClockwise, Gear, DownloadSimple, ArrowCounterClockwise } from '@phosphor-icons/react';
import { toast } from 'sonner';
import { authHeaders, isAdmin } from '../auth';
import SafeOverlay from './SafeOverlay';
import TIMEFRAMES from '../constants/timeframes';
import './Backtester.css';
import './BacktesterExtra.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const DAY_OPTIONS = [1, 2, 3, 5, 7, 14, 30, 60, 90, 180, 360];

const BE_MODES = [
  { v: 'tp1', l: 'Bei TP1 → SL auf Break-Even + Gebühren' },
  { v: 'crv', l: 'Bei frei wählbarem CRV (z.B. 1R, 2R, 3R)' },
  { v: 'profit_pct', l: 'Bei festem Gewinn-% auf die Marge' },
  { v: 'smart', l: 'Smart (Backtest: Swing-Low/High, Live: wie TP1)' },
  { v: 'off', l: 'Break-Even deaktiviert' },
];

const fmtEta = (s) => {
  if (s === null || s === undefined) return '';
  if (s >= 3600) return `~${Math.floor(s / 3600)}h ${Math.round((s % 3600) / 60)}min`;
  if (s >= 60) return `~${Math.floor(s / 60)}min ${s % 60}s`;
  return `~${s}s`;
};

const fmt = (v, d = 2) => (v === null || v === undefined ? '–' : Number(v).toFixed(d));

const cleanCfg = (c) => {
  const out = {};
  Object.entries(c || {}).forEach(([k, v]) => {
    if (k === 'params') {
      const p = {};
      Object.entries(v || {}).forEach(([pk, pv]) => {
        if (pv !== '' && pv !== null && pv !== undefined && !Number.isNaN(pv)) p[pk] = pv;
      });
      if (Object.keys(p).length) out.params = p;
    } else if (v !== '' && v !== null && v !== undefined && !Number.isNaN(v)) {
      out[k] = v;
    }
  });
  return out;
};

export default function Backtester({ onClose }) {
  const [strategies, setStrategies] = useState([]);
  const [coins, setCoins] = useState([]);
  const [selStrats, setSelStrats] = useState([]);
  const [selCoins, setSelCoins] = useState([]);
  const [days, setDays] = useState(3);
  const [capital, setCapital] = useState(100);
  const [leverage, setLeverage] = useState(10);
  const [fee, setFee] = useState(0.06);
  const [psEnabled, setPsEnabled] = useState(false);
  const [beMode, setBeMode] = useState('tp1');
  const [beTriggerCrv, setBeTriggerCrv] = useState(1.0);
  const [beTriggerPct, setBeTriggerPct] = useState(30);
  const [requireAll, setRequireAll] = useState(false);
  const [sessions, setSessions] = useState('');
  const [stratCfgs, setStratCfgs] = useState({});
  const [openCfg, setOpenCfg] = useState(null);
  const [job, setJob] = useState(null);
  const [result, setResult] = useState(null);
  const [resultId, setResultId] = useState(null);
  const pollRef = useRef(null);

  useEffect(() => {
    fetch(`${API_URL}/api/strategies`).then(r => r.json()).then(d => {
      const list = d.strategies || [];
      setStrategies(list);
      setSelStrats(list.slice(0, 2).map(s => s.id));
    });
    fetch(`${API_URL}/api/coins`).then(r => r.json()).then(d => {
      setCoins(d.coins || []);
      setSelCoins((d.coins || []).slice(0, 3));
    });
    fetch(`${API_URL}/api/backtest/strategy-configs`).then(r => r.json())
      .then(d => setStratCfgs(d.configs || {})).catch(() => {});
    // letztes Ergebnis laden
    fetch(`${API_URL}/api/backtest/results?limit=1`).then(r => r.json()).then(d => {
      const last = (d.results || [])[0];
      if (last?.result) { setResult(last.result); setResultId(last.id); }
    }).catch(() => {});
    // Läuft gerade ein Backtest? -> Fortschritt wieder anzeigen (persistent)
    fetch(`${API_URL}/api/backtest/active`).then(r => r.json()).then(d => {
      if (d.active) { setJob(d.active); poll(d.active.id); }
    }).catch(() => {});
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, []);

  const toggle = (list, setList, v) =>
    setList(list.includes(v) ? list.filter(x => x !== v) : [...list, v]);

  const updateCfg = (sid, key, value) =>
    setStratCfgs(prev => ({ ...prev, [sid]: { ...(prev[sid] || {}), [key]: value } }));

  const updateCfgParam = (sid, key, value) =>
    setStratCfgs(prev => ({
      ...prev,
      [sid]: { ...(prev[sid] || {}), params: { ...((prev[sid] || {}).params || {}), [key]: value } },
    }));

  const resetCfg = (sid) => {
    setStratCfgs(prev => {
      const n = { ...prev };
      delete n[sid];
      return n;
    });
    toast.success('Backtest-Einstellungen zurückgesetzt');
  };

  const persistCfgs = async (cfgs) => {
    if (!isAdmin()) return;
    try {
      await fetch(`${API_URL}/api/backtest/strategy-configs`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ configs: cfgs }),
      });
    } catch { /* ignore */ }
  };

  const poll = useCallback((jobId) => {
    pollRef.current = setInterval(async () => {
      try {
        const j = await fetch(`${API_URL}/api/backtest/status/${jobId}`).then(r => r.json());
        setJob(j);
        if (j.status === 'done') {
          clearInterval(pollRef.current);
          setResult(j.result);
          setResultId(jobId);
          toast.success('Backtest abgeschlossen');
        } else if (j.status === 'error') {
          clearInterval(pollRef.current);
          toast.error(`Backtest fehlgeschlagen: ${j.error}`);
        } else if (j.status === 'cancelled') {
          clearInterval(pollRef.current);
          toast.info('Backtest abgebrochen');
        }
      } catch { /* keep polling */ }
    }, 1500);
  }, []);

  const cancel = async () => {
    if (!job?.id) return;
    try {
      await fetch(`${API_URL}/api/backtest/cancel/${job.id}`, {
        method: 'POST', headers: authHeaders(),
      });
      toast.info('Abbruch angefordert...');
    } catch { toast.error('Verbindungsfehler'); }
  };

  const run = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    if (!selStrats.length || !selCoins.length) { toast.error('Mind. 1 Strategie und 1 Coin wählen'); return; }
    const strategyConfigs = {};
    selStrats.forEach(sid => {
      const c = cleanCfg(stratCfgs[sid]);
      if (Object.keys(c).length) strategyConfigs[sid] = c;
    });
    persistCfgs(stratCfgs);
    try {
      const res = await fetch(`${API_URL}/api/backtest/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({
          strategy_ids: selStrats, symbols: selCoins, days,
          max_capital: capital, leverage, fee_percent: fee,
          profit_secure_enabled: psEnabled,
          be_mode: beMode, be_trigger_crv: beTriggerCrv, be_trigger_profit_pct: beTriggerPct,
          require_all_rules: requireAll,
          sessions: sessions.trim() || undefined,
          strategy_configs: strategyConfigs,
        }),
      });
      const d = await res.json();
      if (!res.ok) { toast.error(d.detail || 'Start fehlgeschlagen'); return; }
      setResult(null);
      setResultId(null);
      setJob({ id: d.job_id, status: 'running', progress: 0, phase: 'Startet...' });
      poll(d.job_id);
    } catch { toast.error('Verbindungsfehler'); }
  };

  const running = job?.status === 'running';
  const perStrategy = result?.per_strategy || [];
  const perPair = result?.per_pair || [];
  const bestPerSymbol = result?.best_per_symbol || {};
  const resultCoins = [...new Set(perPair.map(p => p.symbol))];
  const resultStrats = [...new Set(perPair.map(p => p.strategy_id))];
  const pairMap = {};
  perPair.forEach(p => { pairMap[`${p.strategy_id}_${p.symbol}`] = p; });

  const renderCfgPanel = (s) => {
    const cfg = stratCfgs[s.id] || {};
    const params = cfg.params || {};
    const hasCustom = Object.keys(cleanCfg(cfg)).length > 0;
    return (
      <div className="btc-panel" key={`cfg-${s.id}`} data-testid={`bt-cfg-panel-${s.id}`}>
        <div className="btc-head">
          <span className="btc-title">{s.name} · Backtest-Einstellungen {hasCustom && <span className="btc-badge">ANGEPASST</span>}</span>
          <button className="btc-reset" onClick={() => resetCfg(s.id)} data-testid={`bt-cfg-reset-${s.id}`}>
            <ArrowCounterClockwise size={13} weight="bold" /> Standard
          </button>
        </div>
        <div className="btc-grid">
          <label>Timeframe
            <select value={cfg.timeframe || ''} onChange={e => updateCfg(s.id, 'timeframe', e.target.value)}
              data-testid={`bt-cfg-tf-${s.id}`}>
              <option value="">Standard ({s.timeframe || '1m'})</option>
              {TIMEFRAMES.map(t => <option key={t.v} value={t.v}>{t.l}</option>)}
            </select>
          </label>
          <label>TP1 bei CRV
            <input type="number" step={0.1} placeholder="1.0" value={cfg.tp1_crv ?? ''}
              onChange={e => updateCfg(s.id, 'tp1_crv', e.target.value === '' ? '' : parseFloat(e.target.value))}
              data-testid={`bt-cfg-tp1-${s.id}`} />
          </label>
          <label>TP Full bei CRV
            <input type="number" step={0.1} placeholder="2.0" value={cfg.tp_full_crv ?? ''}
              onChange={e => updateCfg(s.id, 'tp_full_crv', e.target.value === '' ? '' : parseFloat(e.target.value))}
              data-testid={`bt-cfg-tpfull-${s.id}`} />
          </label>
          <label>TP1 schließt %
            <input type="number" min={1} max={99} placeholder="50" value={cfg.tp1_close_percent ?? ''}
              onChange={e => updateCfg(s.id, 'tp1_close_percent', e.target.value === '' ? '' : parseInt(e.target.value))} />
          </label>
          <label>SL Modus
            <select value={cfg.sl_mode || ''} onChange={e => updateCfg(s.id, 'sl_mode', e.target.value)}
              data-testid={`bt-cfg-slmode-${s.id}`}>
              <option value="">Standard (Struktur)</option>
              <option value="structure">Struktur</option>
              <option value="atr">ATR</option>
              <option value="fixed">Fest %</option>
            </select>
          </label>
          {cfg.sl_mode === 'fixed' ? (
            <label>SL Abstand %
              <input type="number" step={0.1} placeholder="1.0" value={cfg.sl_fixed_percent ?? ''}
                onChange={e => updateCfg(s.id, 'sl_fixed_percent', e.target.value === '' ? '' : parseFloat(e.target.value))} />
            </label>
          ) : (
            <label>SL Lookback (Kerzen)
              <input type="number" placeholder="10" value={cfg.sl_lookback ?? ''}
                onChange={e => updateCfg(s.id, 'sl_lookback', e.target.value === '' ? '' : parseInt(e.target.value))} />
            </label>
          )}
          <label>Break-Even
            <select value={cfg.be_mode || ''} onChange={e => updateCfg(s.id, 'be_mode', e.target.value)}
              data-testid={`bt-cfg-bemode-${s.id}`}>
              <option value="">Standard (global)</option>
              {BE_MODES.map(m => <option key={m.v} value={m.v}>{m.l}</option>)}
            </select>
          </label>
          {cfg.be_mode === 'crv' && (
            <label>BE ab CRV (R)
              <input type="number" step={0.1} placeholder="1.0" value={cfg.be_trigger_crv ?? ''}
                onChange={e => updateCfg(s.id, 'be_trigger_crv', e.target.value === '' ? '' : parseFloat(e.target.value))} />
            </label>
          )}
          {cfg.be_mode === 'profit_pct' && (
            <label>BE ab Gewinn %
              <input type="number" step={5} placeholder="30" value={cfg.be_trigger_profit_pct ?? ''}
                onChange={e => updateCfg(s.id, 'be_trigger_profit_pct', e.target.value === '' ? '' : parseFloat(e.target.value))} />
            </label>
          )}
          <label>Zeitfenster (nur diese Strategie)
            <input type="text" placeholder="z.B. 09:00-12:00 · leer = global" value={cfg.sessions ?? ''}
              onChange={e => updateCfg(s.id, 'sessions', e.target.value)}
              data-testid={`bt-cfg-sessions-${s.id}`} />
          </label>
        </div>
        {Object.keys(s.params || {}).length > 0 && (
          <>
            <div className="btc-sub">STRATEGIE-PARAMETER (nur für diesen Backtest)</div>
            <div className="btc-grid">
              {Object.entries(s.params).map(([pk, pm]) => (
                <label key={pk} title={pm.description}>{pm.label}
                  <input type="number" min={pm.min} max={pm.max} step={pm.step}
                    placeholder={String(pm.value)}
                    value={params[pk] ?? ''}
                    onChange={e => updateCfgParam(s.id, pk, e.target.value === '' ? '' : parseFloat(e.target.value))}
                    data-testid={`bt-cfg-param-${s.id}-${pk}`} />
                </label>
              ))}
            </div>
          </>
        )}
      </div>
    );
  };

  return (
    <SafeOverlay className="bt-overlay" onClose={onClose}>
      <div className="bt-panel" onClick={e => e.stopPropagation()} data-testid="backtester-modal">
        <div className="bt-header">
          <h2><ClockCounterClockwise size={20} weight="bold" style={{ color: '#00A8FF' }} /> STRATEGIE-BACKTESTER</h2>
          <button className="bt-close" onClick={onClose} data-testid="backtester-close"><X size={22} weight="bold" /></button>
        </div>

        <div className="bt-setup">
          <div className="bt-col">
            <div className="bt-label">STRATEGIEN <span className="btc-hint-inline">(⚙ = TP/SL, Timeframe & Parameter pro Strategie)</span></div>
            <div className="bt-chips">
              {strategies.map(s => (
                <span key={s.id} className="btc-chipwrap">
                  <button
                    className={`bt-chip ${selStrats.includes(s.id) ? 'on' : ''}`}
                    onClick={() => toggle(selStrats, setSelStrats, s.id)}
                    data-testid={`bt-strat-${s.id}`}>
                    {s.name}
                    {(stratCfgs[s.id]?.timeframe) && <span className="btc-tf-tag">{stratCfgs[s.id].timeframe}</span>}
                  </button>
                  {selStrats.includes(s.id) && (
                    <button className={`btc-gear ${openCfg === s.id ? 'on' : ''}`}
                      onClick={() => setOpenCfg(openCfg === s.id ? null : s.id)}
                      title="Backtest-Einstellungen für diese Strategie"
                      data-testid={`bt-cfg-toggle-${s.id}`}>
                      <Gear size={13} weight="bold" />
                    </button>
                  )}
                </span>
              ))}
            </div>
            {openCfg && selStrats.includes(openCfg) &&
              renderCfgPanel(strategies.find(s => s.id === openCfg) || { id: openCfg, params: {} })}
          </div>
          <div className="bt-col">
            <div className="bt-label">COINS</div>
            <div className="bt-chips">
              {coins.map(c => (
                <button key={c}
                  className={`bt-chip ${selCoins.includes(c) ? 'on' : ''}`}
                  onClick={() => toggle(selCoins, setSelCoins, c)}
                  data-testid={`bt-coin-${c}`}>
                  {c.replace('USDT', '')}
                </button>
              ))}
            </div>
          </div>
        </div>

        <div className="bt-params">
          <label>Zeitraum
            <select value={days} onChange={e => setDays(parseInt(e.target.value))} data-testid="bt-days">
              {DAY_OPTIONS.map(d => <option key={d} value={d}>{d} Tag{d > 1 ? 'e' : ''}</option>)}
            </select>
          </label>
          <label>Kapital (USDT)
            <input type="number" min={1} value={capital} onChange={e => setCapital(parseFloat(e.target.value) || 100)} data-testid="bt-capital" />
          </label>
          <label>Hebel
            <input type="number" min={1} max={125} value={leverage} onChange={e => setLeverage(parseInt(e.target.value) || 10)} data-testid="bt-leverage" />
          </label>
          <label>Gebühr % / Fill
            <input type="number" step={0.01} value={fee} onChange={e => setFee(parseFloat(e.target.value) || 0)} data-testid="bt-fee" />
          </label>
          <label className="bt-check">
            <input type="checkbox" checked={psEnabled} onChange={e => setPsEnabled(e.target.checked)} data-testid="bt-profit-secure" />
            Gewinnsicherung
          </label>
          <label>Break-Even
            <select value={beMode} onChange={e => setBeMode(e.target.value)} data-testid="bt-be-mode">
              {BE_MODES.map(m => <option key={m.v} value={m.v}>{m.l}</option>)}
            </select>
          </label>
          {beMode === 'crv' && (
            <label>BE ab CRV (R)
              <input type="number" step={0.1} min={0.1} value={beTriggerCrv}
                onChange={e => setBeTriggerCrv(parseFloat(e.target.value) || 1)} data-testid="bt-be-crv" />
            </label>
          )}
          {beMode === 'profit_pct' && (
            <label>BE ab Gewinn %
              <input type="number" step={5} min={1} value={beTriggerPct}
                onChange={e => setBeTriggerPct(parseFloat(e.target.value) || 30)} data-testid="bt-be-pct" />
            </label>
          )}
          <label className="bt-check" title="Trades nur wenn ALLE Regeln der Strategie erfüllt sind (kein 3/5-Regeln-Trade)">
            <input type="checkbox" checked={requireAll} onChange={e => setRequireAll(e.target.checked)} data-testid="bt-require-all" />
            Nur 100% Regel-Treffer
          </label>
          <label className="bt-session-field">Zeitfenster (global, leer = 24h)
            <input type="text" placeholder="z.B. 09:00-12:00,15:00-22:00" value={sessions}
              onChange={e => setSessions(e.target.value)} data-testid="bt-sessions" />
          </label>
          <button className="bt-run" onClick={run} disabled={running} data-testid="bt-run">
            <Play size={15} weight="fill" /> {running ? 'Läuft...' : 'Backtest starten'}
          </button>
        </div>

        {running && (
          <div className="bt-progress" data-testid="bt-progress">
            <div className="bt-progress-bar"><div style={{ width: `${job.progress || 0}%` }} /></div>
            <div className="bt-progress-row">
              <div className="bt-progress-text" data-testid="bt-progress-text">
                {job.phase} · {job.progress || 0}%
                {job.eta_seconds != null && <span className="bt-eta"> · Restzeit {fmtEta(job.eta_seconds)}</span>}
              </div>
              <button className="bt-cancel" onClick={cancel} data-testid="bt-cancel">
                <X size={13} weight="bold" /> Abbrechen
              </button>
            </div>
          </div>
        )}

        {result && (
          <>
            <div className="bt-section-title">
              <Trophy size={15} weight="fill" style={{ color: '#FFD700' }} />
              GESAMT-RANKING ({result.days} Tage · Kapital {result.config?.max_capital} USDT · {result.config?.leverage}x · Gebühren inkl.)
              {resultId && (
                <span className="btc-export">
                  <a href={`${API_URL}/api/backtest/export/${resultId}?kind=trades`}
                    className="btc-export-btn" data-testid="bt-export-trades">
                    <DownloadSimple size={13} weight="bold" /> Trades CSV
                  </a>
                  <a href={`${API_URL}/api/backtest/export/${resultId}?kind=candles`}
                    className="btc-export-btn" data-testid="bt-export-candles">
                    <DownloadSimple size={13} weight="bold" /> Kerzen CSV
                  </a>
                </span>
              )}
            </div>
            <div className="bt-table-wrap">
              <table className="bt-table" data-testid="bt-strategy-table">
                <thead>
                  <tr><th>#</th><th>Strategie</th><th>TF</th><th>Trades</th><th>Win-Rate</th><th>PnL</th><th>Ø PnL</th><th>Max DD</th><th>Gebühren</th><th>Gesichert</th><th>Ø Dauer</th></tr>
                </thead>
                <tbody>
                  {perStrategy.map((s, i) => (
                    <tr key={s.strategy_id} className={i === 0 && s.pnl > 0 ? 'bt-best' : ''}>
                      <td>{i === 0 && s.pnl > 0 ? <Trophy size={13} weight="fill" style={{ color: '#FFD700' }} /> : i + 1}</td>
                      <td className="bt-name">{s.strategy_name}</td>
                      <td className="btc-tf-cell">{s.timeframe || '1m'}</td>
                      <td>{s.trades}</td>
                      <td className={s.win_rate >= 50 ? 'pos' : 'neg'}>{fmt(s.win_rate, 1)}%</td>
                      <td className={`mono ${s.pnl >= 0 ? 'pos' : 'neg'}`}>{fmt(s.pnl)}</td>
                      <td className={`mono ${s.avg_pnl >= 0 ? 'pos' : 'neg'}`}>{fmt(s.avg_pnl, 3)}</td>
                      <td className="mono neg">{fmt(s.max_drawdown)}</td>
                      <td className="mono">{fmt(s.fees)}</td>
                      <td>{s.secured || 0}</td>
                      <td>{fmt(s.avg_duration_min, 1)} min</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            <div className="bt-section-title">MATRIX: WELCHE STRATEGIE PASST ZU WELCHEM COIN?</div>
            <div className="bt-table-wrap">
              <table className="bt-table bt-matrix" data-testid="bt-matrix-table">
                <thead>
                  <tr>
                    <th>Coin</th>
                    {resultStrats.map(sid => (
                      <th key={sid}>{perPair.find(p => p.strategy_id === sid)?.strategy_name || sid}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {resultCoins.map(sym => (
                    <tr key={sym}>
                      <td className="bt-name">{sym.replace('USDT', '')}</td>
                      {resultStrats.map(sid => {
                        const p = pairMap[`${sid}_${sym}`];
                        const isBest = bestPerSymbol[sym]?.strategy_id === sid && (p?.pnl || 0) > 0;
                        return (
                          <td key={sid} className={isBest ? 'bt-best' : ''}>
                            {p ? (
                              <div className="bt-cell">
                                <span className={`mono ${p.pnl >= 0 ? 'pos' : 'neg'}`}>{fmt(p.pnl)}</span>
                                <span className="bt-cell-sub">{p.trades} T · {fmt(p.win_rate, 0)}% WR · {p.timeframe || '1m'}</span>
                              </div>
                            ) : '–'}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="bt-hint">
              🏆 = bester PnL. Simulation nutzt dieselbe Logik wie Paper/Live: Struktur-SL, TP1-Teilverkauf,
              Break-Even, ATR-Trailing{result.config?.profit_secure_enabled ? ', Gewinnsicherung' : ''} und Gebühren pro Fill.
              CSV-Export enthält jeden einzelnen Trade inkl. Indikatorwerten &amp; alle ausgewerteten Kerzen zum Nachprüfen.
              Hinweis: Hohe Timeframes (24h+) brauchen einen langen Zeitraum, sonst gibt es zu wenig Kerzen.
            </div>
          </>
        )}

        {!result && !running && (
          <div className="bt-empty" data-testid="bt-empty">
            Wähle Strategien &amp; Coins und starte den Backtest –
            die Vergangenheit zeigt dir in Minuten, was Paper-Trading Wochen kosten würde.
          </div>
        )}
      </div>
    </SafeOverlay>
  );
}
