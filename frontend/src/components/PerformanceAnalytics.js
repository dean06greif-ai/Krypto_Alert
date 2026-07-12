import React from 'react';
import { TrendUp, TrendDown, Target } from '@phosphor-icons/react';
import './PerformanceAnalytics.css';

const PerformanceAnalytics = ({ performance, signals }) => {
  const topPerformers = performance
    .filter(p => p.total_signals > 0)
    .sort((a, b) => b.total_signals - a.total_signals)
    .slice(0, 5);

  const totalSignals = signals.length;
  const longSignals = signals.filter(s => s.type === 'LONG').length;
  const shortSignals = signals.filter(s => s.type === 'SHORT').length;

  const getCoinName = (symbol) => {
    return symbol.replace('USDT', '');
  };

  return (
    <div className="performance-analytics" data-testid="performance-analytics">
      <div className="analytics-header">
        <h3>PERFORMANCE</h3>
        <div className="analytics-subtitle">Live Statistics</div>
      </div>

      <div className="analytics-section">
        <div className="section-title">GESAMTÜBERSICHT</div>
        <div className="stats-grid">
          <div className="stat-card">
            <div className="stat-icon">
              <Target size={20} className="text-warning" />
            </div>
            <div className="stat-content">
              <div className="stat-value mono">{totalSignals}</div>
              <div className="stat-label">Total Signals</div>
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-icon">
              <TrendUp size={20} className="text-long" />
            </div>
            <div className="stat-content">
              <div className="stat-value mono text-long">{longSignals}</div>
              <div className="stat-label">Long Signals</div>
            </div>
          </div>
          <div className="stat-card">
            <div className="stat-icon">
              <TrendDown size={20} className="text-short" />
            </div>
            <div className="stat-content">
              <div className="stat-value mono text-short">{shortSignals}</div>
              <div className="stat-label">Short Signals</div>
            </div>
          </div>
        </div>
      </div>

      <div className="analytics-section">
        <div className="section-title">TOP 5 COINS</div>
        <div className="top-coins-list">
          {topPerformers.length === 0 && (
            <div className="no-data">Keine Daten verfügbar</div>
          )}
          {topPerformers.map((coin, index) => (
            <div key={coin.symbol} className="top-coin-item" data-testid={`top-coin-${coin.symbol}`}>
              <div className="coin-rank">{index + 1}</div>
              <div className="coin-info">
                <div className="coin-name mono">{getCoinName(coin.symbol)}</div>
                <div className="coin-signals">
                  <span className="text-long mono">{coin.long_signals}</span>
                  <span className="text-muted">/</span>
                  <span className="text-short mono">{coin.short_signals}</span>
                </div>
              </div>
              <div className="coin-crv">
                <div className="crv-label">CRV</div>
                <div className="crv-value mono">{coin.avg_crv?.toFixed(2) || '0.00'}</div>
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="analytics-section">
        <div className="section-title">LETZTE SIGNALE</div>
        <div className="recent-signals-list">
          {signals.slice(0, 5).map(signal => (
            <div key={signal.timestamp} className="recent-signal-item">
              <span className={`badge ${signal.type === 'LONG' ? 'badge-long' : 'badge-short'}`}>
                {signal.type}
              </span>
              <span className="mono text-secondary">{getCoinName(signal.symbol)}</span>
              <span className="mono text-muted" style={{ fontSize: '10px' }}>
                {new Date(signal.timestamp).toLocaleTimeString('de-DE', { 
                  hour: '2-digit', 
                  minute: '2-digit' 
                })}
              </span>
            </div>
          ))}
          {signals.length === 0 && (
            <div className="no-data">Keine Signale bisher</div>
          )}
        </div>
      </div>
    </div>
  );
};

export default PerformanceAnalytics;
