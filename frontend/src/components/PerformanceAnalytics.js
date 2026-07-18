import React, { useState, useEffect, useCallback } from 'react';
import { TrendUp, TrendDown, Target, Clock, ChartBar, Lightning, CheckCircle, XCircle, Trash, Warning, Sparkle } from '@phosphor-icons/react';
import { toast } from 'sonner';
import { authHeaders } from '../auth';
import './PerformanceAnalytics.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const CLEAR_RANGES = [
  { key: 'hour', label: 'Letzte Stunde' },
  { key: '24h', label: 'Letzte 24 Stunden' },
  { key: '7d', label: 'Letzte 7 Tage' },
  { key: '4w', label: 'Letzte 4 Wochen' },
  { key: 'all', label: 'Gesamter Zeitraum (alles)' },
];

const PerformanceAnalytics = ({ performance, strategies = [], enabledIds = [], signals, selectedCoin, selectedStrategy, isAdmin, onNeedAdmin, onCleared }) => {
  const [view, setView] = useState('overview');
  const [timeAnalytics, setTimeAnalytics] = useState(null);
  const [trades, setTrades] = useState([]);
  const [balance, setBalance] = useState(null);
  const [showClear, setShowClear] = useState(false);
  const [clearRange, setClearRange] = useState('24h');
  const [clearing, setClearing] = useState(false);
  const [tradeFilter, setTradeFilter] = useState('all');
  const [aiLoading, setAiLoading] = useState(false);
  const [aiReview, setAiReview] = useState(null);
  const [aiError, setAiError] = useState(null);

  const getCoinName = (s) => s?.replace('USDT', '') || '';
  const stratName = (t) => t?.strategy_name || strategies.find(s => s.id === t?.strategy_id)?.name || t?.strategy_id || '—';
  const modeInfo = (m) => (m === 'live')
    ? { label: 'LIVE', cls: 'mode-live' }
    : { label: 'PAPER', cls: 'mode-paper' };

  const stratSignals = signals.filter(s => !selectedStrategy || s.strategy_id === selectedStrategy);
  const totalSignals = stratSignals.length;
  const longSignals = stratSignals.filter(s => s.type === 'LONG').length;
  const shortSignals = stratSignals.filter(s => s.type === 'SHORT').length;
  const wins = stratSignals.filter(s => s.result === 'win').length;
  const losses = stratSignals.filter(s => s.result === 'loss').length;
  const decided = wins + losses;
  const winRate = decided ? Math.round(wins / decided * 100) : 0;

  const totalWins = performance.reduce((a, p) => a + (p.wins || 0), 0);
  const totalLosses = performance.reduce((a, p) => a + (p.losses || 0), 0);
  const globalDecided = totalWins + totalLosses;
  const globalWinRate = globalDecided ? Math.round(totalWins / globalDecided * 100) : 0;

  const topPerformers = performance.filter(p => p.total_signals > 0)
    .sort((a, b) => (b.win_rate || 0) - (a.win_rate || 0)).slice(0, 5);

  const loadTrades = useCallback(() => {
    fetch(`${API_URL}/api/autotrade/trades?limit=200`).then(r => r.json()).then(d => setTrades(d.trades || [])).catch(() => {});
    fetch(`${API_URL}/api/autotrade/balance`).then(r => r.json()).then(setBalance).catch(() => {});
  }, []);

  useEffect(() => {
    if (view === 'time-based' && selectedCoin) {
      fetch(`${API_URL}/api/analytics/time-based/${selectedCoin}`).then(r => r.json()).then(setTimeAnalytics).catch(() => {});
    }
    if (view === 'trades') { loadTrades(); const iv = setInterval(loadTrades, 15000); return () => clearInterval(iv); }
  }, [view, selectedCoin, loadTrades]);

  const filterFn = (t) => tradeFilter === 'all' || t.mode === tradeFilter;
  const openTrades = trades.filter(t => t.status === 'open' && filterFn(t));
  const closedTrades = trades.filter(t => t.status === 'closed' && filterFn(t));

  const paperCount = trades.filter(t => t.mode !== 'live').length;
  const liveCount = trades.filter(t => t.mode === 'live').length;

  // Coin-specific slices (for currently selected coin)
  const coinClosedTrades = closedTrades.filter(t => t.symbol === selectedCoin);
  const coinOpenTrades = openTrades.filter(t => t.symbol === selectedCoin);
  const coinPnl = coinClosedTrades.reduce((a, t) => a + (t.realized_pnl || 0), 0);

  // Performance per ACTIVE strategy for the SELECTED COIN
  // Show every active strategy, even without trades yet.
  const activeStrategies = strategies.filter(s => enabledIds.includes(s.id));
  const activeStratList = activeStrategies.length ? activeStrategies : strategies;

  const stratRows = activeStratList.map(strat => {
    const rowTrades = closedTrades.filter(t => t.strategy_id === strat.id && t.symbol === selectedCoin);
    const wins = rowTrades.filter(t => t.result === 'win').length;
    const losses = rowTrades.filter(t => t.result === 'loss').length;
    const decided = wins + losses;
    const pnl = rowTrades.reduce((a, t) => a + (t.realized_pnl || 0), 0);
    const openCount = openTrades.filter(t => t.strategy_id === strat.id && t.symbol === selectedCoin).length;
    return {
      id: strat.id,
      name: strat.name || strat.id,
      wins,
      losses,
      total: rowTrades.length,
      openCount,
      pnl,
      wr: decided ? Math.round((wins / decided) * 100) : 0,
    };
  }).sort((a, b) => (b.total + b.openCount) - (a.total + a.openCount));

  const openClear = () => {
    if (!isAdmin) { onNeedAdmin && onNeedAdmin(); return; }
    setShowClear(true);
  };

  const runClear = async () => {
    setClearing(true);
    try {
      const res = await fetch(`${API_URL}/api/analytics/clear`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ range: clearRange }),
      });
      if (res.ok) {
        const data = await res.json();
        const total = Object.values(data.deleted || {}).reduce((a, b) => a + b, 0);
        toast.success(`Analyse-Daten gelöscht (${total} Einträge)`);
        setShowClear(false);
        onCleared && onCleared();
      } else if (res.status === 401) {
        toast.error('Admin-Login erforderlich');
        onNeedAdmin && onNeedAdmin();
      } else {
        toast.error('Fehler beim Löschen');
      }
    } catch {
      toast.error('Verbindungsfehler');
    } finally {
      setClearing(false);
    }
  };

  const rangeLabel = CLEAR_RANGES.find(r => r.key === clearRange)?.label || '';

  const runAiReview = async () => {
    setAiLoading(true);
    setAiError(null);
    setAiReview(null);
    try {
      const res = await fetch(`${API_URL}/api/analytics/ai-review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(selectedStrategy ? { strategy_id: selectedStrategy } : {}),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const msg = data?.detail || `Fehler ${res.status}`;
        setAiError(msg);
        toast.error(`KI-Analyse fehlgeschlagen: ${msg}`);
      } else {
        setAiReview(data.review || 'Keine Antwort erhalten.');
        toast.success('KI-Analyse fertig');
      }
    } catch (e) {
      setAiError('Verbindungsfehler zum Backend');
      toast.error('Verbindungsfehler');
    } finally {
      setAiLoading(false);
    }
  };

  return (
    <div className="performance-analytics" data-testid="performance-analytics">
      <div className="analytics-header">
        <div><h3>ANALYSE</h3><div className="analytics-subtitle">Live Statistics</div></div>
        <button className="clear-data-btn" onClick={openClear} data-testid="clear-analytics-btn" title="Analyse-Daten löschen">
          <Trash size={15} weight="bold" />
        </button>
      </div>

      <div className="view-switcher">
        <button className={`view-btn ${view === 'overview' ? 'active' : ''}`} onClick={() => setView('overview')} data-testid="view-overview"><ChartBar size={14} />Übersicht</button>
        <button className={`view-btn ${view === 'trades' ? 'active' : ''}`} onClick={() => setView('trades')} data-testid="view-trades"><Lightning size={14} />Trades</button>
        <button className={`view-btn ${view === 'time-based' ? 'active' : ''}`} onClick={() => setView('time-based')} data-testid="view-time-based"><Clock size={14} />Zeit</button>
      </div>

      {view === 'overview' && (
        <>
          <div className="analytics-section">
            <div className="section-title">HEUTE (aktive Strategie)</div>
            <div className="stats-grid">
              <div className="stat-card"><div className="stat-icon"><Target size={20} className="text-warning" /></div><div className="stat-content"><div className="stat-value mono">{totalSignals}</div><div className="stat-label">Signale</div></div></div>
              <div className="stat-card"><div className="stat-icon"><TrendUp size={20} className="text-long" /></div><div className="stat-content"><div className="stat-value mono text-long">{longSignals}</div><div className="stat-label">Long</div></div></div>
              <div className="stat-card"><div className="stat-icon"><TrendDown size={20} className="text-short" /></div><div className="stat-content"><div className="stat-value mono text-short">{shortSignals}</div><div className="stat-label">Short</div></div></div>
              <div className="stat-card"><div className="stat-icon"><CheckCircle size={20} className="text-long" /></div><div className="stat-content"><div className="stat-value mono">{winRate}%</div><div className="stat-label">Win-Rate ({decided})</div></div></div>
            </div>
          </div>

          <div className="analytics-section">
            <div className="section-title">GESAMT-ANALYSE (dauerhaft)</div>
            <div className="global-stats">
              <div className="global-stat"><span className="text-long mono">{totalWins}</span><span className="text-muted">Wins</span></div>
              <div className="global-stat"><span className="text-short mono">{totalLosses}</span><span className="text-muted">Losses</span></div>
              <div className="global-stat"><span className="mono" style={{ color: globalWinRate >= 50 ? '#00FF66' : '#FF3366' }}>{globalWinRate}%</span><span className="text-muted">Win-Rate</span></div>
            </div>
          </div>

          <div className="analytics-section">
            <div className="section-title">TOP COINS (Win-Rate)</div>
            <div className="top-coins-list">
              {topPerformers.length === 0 && <div className="no-data">Noch keine Daten</div>}
              {topPerformers.map((coin, i) => (
                <div key={coin.symbol} className="top-coin-item" data-testid={`top-coin-${coin.symbol}`}>
                  <div className="coin-rank">{i + 1}</div>
                  <div className="coin-info"><div className="coin-name mono">{getCoinName(coin.symbol)}</div>
                    <div className="coin-signals"><span className="text-long mono">{coin.long_signals}</span><span className="text-muted">/</span><span className="text-short mono">{coin.short_signals}</span></div></div>
                  <div className="coin-crv"><div className="crv-label">WR</div><div className="crv-value mono" style={{ color: (coin.win_rate || 0) >= 50 ? '#00FF66' : '#FF3366' }}>{(coin.win_rate || 0).toFixed(0)}%</div></div>
                </div>
              ))}
            </div>
          </div>

          <div className="analytics-section">
            <div className="section-title">LETZTE SIGNALE</div>
            <div className="recent-signals-list">
              {stratSignals.slice(0, 6).map((s, i) => (
                <div key={i} className="recent-signal-item">
                  <span className={`badge ${s.type === 'LONG' ? 'badge-long' : 'badge-short'}`}>{s.signal_class === 'PRE_SIGNAL' ? 'PRE-' : ''}{s.type}</span>
                  <span className="mono text-secondary">{getCoinName(s.symbol)}</span>
                  {s.result && <span className={s.result === 'win' ? 'text-long' : 'text-short'} style={{ fontSize: '10px' }}>{s.result === 'win' ? '✓' : '✗'}</span>}
                  <span className="mono text-muted" style={{ fontSize: '10px', marginLeft: 'auto' }}>{new Date(s.timestamp).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin' })}</span>
                </div>
              ))}
              {stratSignals.length === 0 && <div className="no-data">Keine Signale heute</div>}
            </div>
          </div>

          <div className="analytics-section ai-review-section">
            <button
              className="ai-review-btn"
              onClick={runAiReview}
              disabled={aiLoading}
              data-testid="ai-review-start-btn"
            >
              <Sparkle size={14} weight="bold" />
              {aiLoading ? 'Analysiere...' : 'KI-Analyse starten'}
            </button>
            {aiLoading && (
              <div className="ai-review-loading" data-testid="ai-review-loading">
                <div className="ai-spinner" /> Coach denkt nach...
              </div>
            )}
            {aiError && !aiLoading && (
              <div className="ai-review-error" data-testid="ai-review-error">
                <Warning size={14} weight="bold" /> {aiError}
              </div>
            )}
            {aiReview && !aiLoading && (
              <div className="ai-review-card" data-testid="ai-review-result">
                <pre className="ai-review-text">{aiReview}</pre>
              </div>
            )}
          </div>
        </>
      )}

      {view === 'trades' && (
        <>
          <div className={`mode-banner ${(balance?.mode === 'live') ? 'mode-live' : 'mode-paper'}`} data-testid="active-mode-banner">
            <div className="mode-banner-dot" />
            <div className="mode-banner-text">
              <span className="mode-banner-label">AKTIVER MODUS</span>
              <span className="mode-banner-value">{(balance?.mode === 'live') ? 'ECHTGELD · LIVE' : 'PAPER-TRADING'}</span>
            </div>
            <span className={`mode-pill ${(balance?.mode === 'live') ? 'mode-live' : 'mode-paper'}`}>{(balance?.mode === 'live') ? 'LIVE' : 'PAPER'}</span>
          </div>

          <div className="analytics-section">
            <div className="stats-grid">
              <div className="stat-card" data-testid="pnl-total-card">
                <div className="stat-content">
                  <div className="stat-value mono" style={{ color: (balance?.realized_pnl || 0) >= 0 ? '#00FF66' : '#FF3366' }}>
                    {(balance?.realized_pnl || 0).toFixed(2)}
                  </div>
                  <div className="stat-label">PnL Gesamt (USDT)</div>
                </div>
              </div>
              <div className="stat-card"><div className="stat-content"><div className="stat-value mono">{openTrades.length}</div><div className="stat-label">Offen</div></div></div>
              <div className="stat-card"><div className="stat-content"><div className="stat-value mono">{closedTrades.length}</div><div className="stat-label">Geschlossen</div></div></div>
            </div>
            <div className="stats-grid" style={{ marginTop: 8 }}>
              <div className="stat-card stat-card-coin" data-testid="pnl-coin-card">
                <div className="stat-content">
                  <div className="stat-value mono" style={{ color: coinPnl >= 0 ? '#00FF66' : '#FF3366' }}>
                    {coinPnl.toFixed(2)}
                  </div>
                  <div className="stat-label">PnL {getCoinName(selectedCoin)} (USDT)</div>
                </div>
              </div>
              <div className="stat-card"><div className="stat-content"><div className="stat-value mono">{coinOpenTrades.length}</div><div className="stat-label">Offen · {getCoinName(selectedCoin)}</div></div></div>
              <div className="stat-card"><div className="stat-content"><div className="stat-value mono">{coinClosedTrades.length}</div><div className="stat-label">Geschl. · {getCoinName(selectedCoin)}</div></div></div>
            </div>
          </div>

          <div className="trade-filter" data-testid="trade-filter">
            <button className={`trade-filter-btn ${tradeFilter === 'all' ? 'active' : ''}`} onClick={() => setTradeFilter('all')} data-testid="trade-filter-all">Alle <span className="tf-count">{trades.length}</span></button>
            <button className={`trade-filter-btn paper ${tradeFilter === 'paper' ? 'active' : ''}`} onClick={() => setTradeFilter('paper')} data-testid="trade-filter-paper">Paper <span className="tf-count">{paperCount}</span></button>
            <button className={`trade-filter-btn live ${tradeFilter === 'live' ? 'active' : ''}`} onClick={() => setTradeFilter('live')} data-testid="trade-filter-live">Live <span className="tf-count">{liveCount}</span></button>
          </div>

          <div className="analytics-section">
            <div className="section-title">
              PERFORMANCE JE STRATEGIE · {getCoinName(selectedCoin)}
            </div>
            {stratRows.length === 0 && <div className="no-data">Keine aktiven Strategien</div>}
            {stratRows.map(s => {
              const empty = s.total === 0 && s.openCount === 0;
              return (
                <div key={s.id} className={`strat-perf-row ${empty ? 'strat-perf-empty' : ''}`} data-testid={`strat-perf-${s.id}`}>
                  <div className="strat-perf-name" title={s.name}>{s.name}</div>
                  <div className="strat-perf-stats">
                    {s.openCount > 0 && <span className="mono text-warning" title="offene Trades">{s.openCount}○</span>}
                    <span className="text-long mono">{s.wins}W</span>
                    <span className="text-short mono">{s.losses}L</span>
                    <span className="mono" style={{ color: (s.wins + s.losses) === 0 ? '#5C6070' : (s.wr >= 50 ? '#00FF66' : '#FF3366') }}>
                      {(s.wins + s.losses) === 0 ? '—' : `${s.wr}%`}
                    </span>
                    <span className={`mono ${s.pnl === 0 ? 'text-muted' : (s.pnl >= 0 ? 'text-long' : 'text-short')}`}>
                      {s.pnl.toFixed(2)}
                    </span>
                  </div>
                </div>
              );
            })}
          </div>

          <div className="analytics-section">
            <div className="section-title">OFFENE TRADES</div>
            {openTrades.length === 0 && <div className="no-data">Keine offenen Trades</div>}
            {openTrades.map(t => {
              const m = modeInfo(t.mode);
              return (
                <div key={t.id} className="trade-row trade-row-lg" data-testid={`trade-row-${t.id}`}>
                  <span className={`mode-tag ${m.cls}`} data-testid={`trade-mode-${t.id}`}>{m.label}</span>
                  <span className={`badge ${t.side === 'LONG' ? 'badge-long' : 'badge-short'}`}>{t.side}</span>
                  <span className="mono text-secondary">{getCoinName(t.symbol)}</span>
                  <span className="strat-chip" data-testid={`trade-strategy-${t.id}`}>{stratName(t)}</span>
                  <span className="mono text-muted" style={{ fontSize: '10px', marginLeft: 'auto' }}>@{t.entry}{t.tp1_hit ? ' TP1✓' : ''}</span>
                </div>
              );
            })}
          </div>

          <div className="analytics-section">
            <div className="section-title">GESCHLOSSENE TRADES</div>
            {closedTrades.length === 0 && <div className="no-data">Keine</div>}
            {closedTrades.slice(0, 12).map(t => {
              const m = modeInfo(t.mode);
              return (
                <div key={t.id} className="trade-row trade-row-lg">
                  <span className={`mode-tag ${m.cls}`}>{m.label}</span>
                  {t.result === 'win' ? <CheckCircle size={15} className="text-long" /> : t.result === 'loss' ? <XCircle size={15} className="text-short" /> : <Target size={15} className="text-warning" />}
                  <span className="mono text-secondary">{getCoinName(t.symbol)}</span>
                  <span className="strat-chip">{stratName(t)}</span>
                  <span className={`mono ${(t.realized_pnl || 0) >= 0 ? 'text-long' : 'text-short'}`} style={{ fontSize: '11px', marginLeft: 'auto' }}>{(t.realized_pnl || 0).toFixed(2)}</span>
                </div>
              );
            })}
          </div>
        </>
      )}

      {view === 'time-based' && (
        <div className="analytics-section">
          <div className="section-title">ZEIT-ANALYSE: {getCoinName(selectedCoin)}</div>
          {!timeAnalytics || (timeAnalytics.time_analytics || []).length === 0 ? (
            <div className="no-data">Noch keine Zeit-Analyse. Sobald Signale kommen, siehst du hier die besten Stunden.</div>
          ) : (
            <div className="time-section">
              <div className="time-subtitle text-long">BESTE ZEITEN</div>
              {(timeAnalytics.best_hours || []).slice(0, 5).map((stat, i) => (
                <div key={i} className="time-item">
                  <div className="time-info"><span className="mono">{String(stat.hour).padStart(2, '0')}:00</span><span className="text-muted"> · {stat.weekday}</span></div>
                  <div className="time-stats"><span className="mono text-long">{stat.win_rate.toFixed(0)}% WR</span><span className="text-muted">·</span><span className="mono">{stat.total_signals}x</span></div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}

      {showClear && (
        <div className="clear-overlay" onClick={() => !clearing && setShowClear(false)}>
          <div className="clear-modal" onClick={e => e.stopPropagation()} data-testid="clear-analytics-modal">
            <div className="clear-modal-header">
              <Trash size={18} weight="bold" />
              <h4>Analyse-Daten löschen</h4>
            </div>
            <p className="clear-modal-sub">Wähle den Zeitraum, der gelöscht werden soll (wie beim Browser-Verlauf).</p>
            <div className="clear-ranges">
              {CLEAR_RANGES.map(r => (
                <label key={r.key} className={`clear-range ${clearRange === r.key ? 'active' : ''} ${r.key === 'all' ? 'danger' : ''}`} data-testid={`clear-range-${r.key}`}>
                  <input type="radio" name="clear-range" value={r.key} checked={clearRange === r.key} onChange={() => setClearRange(r.key)} />
                  <span>{r.label}</span>
                </label>
              ))}
            </div>
            <div className="clear-warn"><Warning size={14} weight="bold" /> Gelöschte Signale &amp; Statistiken können nicht wiederhergestellt werden.</div>
            <div className="clear-actions">
              <button className="clear-cancel" onClick={() => setShowClear(false)} disabled={clearing} data-testid="clear-cancel-btn">Abbrechen</button>
              <button className="clear-confirm" onClick={runClear} disabled={clearing} data-testid="clear-confirm-btn">
                {clearing ? 'Lösche...' : `Löschen (${rangeLabel})`}
              </button>
            </div>
          </div>
        </div>
      )}

    </div>
  );
};

export default PerformanceAnalytics;
