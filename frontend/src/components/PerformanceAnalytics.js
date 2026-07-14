import React, { useState, useEffect, useCallback } from 'react';
import { TrendUp, TrendDown, Target, Clock, ChartBar, Lightning, CheckCircle, XCircle, Trash, Warning } from '@phosphor-icons/react';
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

const PerformanceAnalytics = ({ performance, signals, selectedCoin, selectedStrategy, isAdmin, onNeedAdmin, onCleared }) => {
  const [view, setView] = useState('overview');
  const [timeAnalytics, setTimeAnalytics] = useState(null);
  const [trades, setTrades] = useState([]);
  const [balance, setBalance] = useState(null);
  const [showClear, setShowClear] = useState(false);
  const [clearRange, setClearRange] = useState('24h');
  const [clearing, setClearing] = useState(false);

  const getCoinName = (s) => s?.replace('USDT', '') || '';

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
    fetch(`${API_URL}/api/autotrade/trades?limit=30`).then(r => r.json()).then(d => setTrades(d.trades || [])).catch(() => {});
    fetch(`${API_URL}/api/autotrade/balance`).then(r => r.json()).then(setBalance).catch(() => {});
  }, []);

  useEffect(() => {
    if (view === 'time-based' && selectedCoin) {
      fetch(`${API_URL}/api/analytics/time-based/${selectedCoin}`).then(r => r.json()).then(setTimeAnalytics).catch(() => {});
    }
    if (view === 'trades') { loadTrades(); const iv = setInterval(loadTrades, 15000); return () => clearInterval(iv); }
  }, [view, selectedCoin, loadTrades]);

  const openTrades = trades.filter(t => t.status === 'open');
  const closedTrades = trades.filter(t => t.status === 'closed');

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
                  <span className="mono text-muted" style={{ fontSize: '10px', marginLeft: 'auto' }}>{new Date(s.timestamp).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })}</span>
                </div>
              ))}
              {stratSignals.length === 0 && <div className="no-data">Keine Signale heute</div>}
            </div>
          </div>
        </>
      )}

      {view === 'trades' && (
        <>
          <div className="analytics-section">
            <div className="section-title">AUTO-TRADE {balance?.mode === 'paper' ? '· PAPER' : '· LIVE'}</div>
            <div className="stats-grid">
              <div className="stat-card"><div className="stat-content"><div className="stat-value mono" style={{ color: (balance?.realized_pnl || 0) >= 0 ? '#00FF66' : '#FF3366' }}>{(balance?.realized_pnl || 0).toFixed(2)}</div><div className="stat-label">PnL (USDT)</div></div></div>
              <div className="stat-card"><div className="stat-content"><div className="stat-value mono">{openTrades.length}</div><div className="stat-label">Offen</div></div></div>
              <div className="stat-card"><div className="stat-content"><div className="stat-value mono">{closedTrades.length}</div><div className="stat-label">Geschlossen</div></div></div>
            </div>
          </div>
          <div className="analytics-section">
            <div className="section-title">OFFENE TRADES</div>
            {openTrades.length === 0 && <div className="no-data">Keine offenen Trades</div>}
            {openTrades.map(t => (
              <div key={t.id} className="trade-row" data-testid={`trade-row-${t.id}`}>
                <span className={`badge ${t.side === 'LONG' ? 'badge-long' : 'badge-short'}`}>{t.side}</span>
                <span className="mono text-secondary">{getCoinName(t.symbol)}</span>
                <span className="mono text-muted" style={{ fontSize: '10px' }}>@{t.entry}{t.tp1_hit ? ' TP1✓' : ''}</span>
              </div>
            ))}
          </div>
          <div className="analytics-section">
            <div className="section-title">GESCHLOSSENE TRADES</div>
            {closedTrades.length === 0 && <div className="no-data">Keine</div>}
            {closedTrades.slice(0, 10).map(t => (
              <div key={t.id} className="trade-row">
                {t.result === 'win' ? <CheckCircle size={15} className="text-long" /> : t.result === 'loss' ? <XCircle size={15} className="text-short" /> : <Target size={15} className="text-warning" />}
                <span className="mono text-secondary">{getCoinName(t.symbol)}</span>
                <span className={`mono ${(t.realized_pnl || 0) >= 0 ? 'text-long' : 'text-short'}`} style={{ fontSize: '11px', marginLeft: 'auto' }}>{(t.realized_pnl || 0).toFixed(2)}</span>
              </div>
            ))}
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
