import React, { useState, useEffect } from 'react';
import { TrendUp, TrendDown, Target, Clock, ChartBar } from '@phosphor-icons/react';
import './PerformanceAnalytics.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const PerformanceAnalytics = ({ performance, signals, selectedCoin }) => {
  const [timeAnalytics, setTimeAnalytics] = useState(null);
  const [view, setView] = useState('overview'); // overview, time-based

  const topPerformers = performance
    .filter(p => p.total_signals > 0)
    .sort((a, b) => b.total_signals - a.total_signals)
    .slice(0, 5);

  const totalSignals = signals.length;
  const longSignals = signals.filter(s => s.type === 'LONG').length;
  const shortSignals = signals.filter(s => s.type === 'SHORT').length;
  const preSignals = signals.filter(s => s.signal_class === 'PRE_SIGNAL').length;

  const getCoinName = (symbol) => symbol?.replace('USDT', '') || '';

  // Fetch time-based analytics for selected coin
  useEffect(() => {
    if (view === 'time-based' && selectedCoin) {
      fetch(`${API_URL}/api/analytics/time-based/${selectedCoin}`)
        .then(r => r.json())
        .then(data => setTimeAnalytics(data))
        .catch(err => console.error('Error fetching time analytics:', err));
    }
  }, [view, selectedCoin]);

  return (
    <div className="performance-analytics" data-testid="performance-analytics">
      <div className="analytics-header">
        <h3>PERFORMANCE</h3>
        <div className="analytics-subtitle">Live Statistics</div>
      </div>

      {/* View Switcher */}
      <div className="view-switcher">
        <button 
          className={`view-btn ${view === 'overview' ? 'active' : ''}`}
          onClick={() => setView('overview')}
          data-testid="view-overview"
        >
          <ChartBar size={14} />
          Übersicht
        </button>
        <button 
          className={`view-btn ${view === 'time-based' ? 'active' : ''}`}
          onClick={() => setView('time-based')}
          data-testid="view-time-based"
        >
          <Clock size={14} />
          Zeit-Analyse
        </button>
      </div>

      {view === 'overview' && (
        <>
          <div className="analytics-section">
            <div className="section-title">GESAMTÜBERSICHT</div>
            <div className="stats-grid">
              <div className="stat-card">
                <div className="stat-icon">
                  <Target size={20} className="text-warning" />
                </div>
                <div className="stat-content">
                  <div className="stat-value mono">{totalSignals}</div>
                  <div className="stat-label">Signals Total</div>
                </div>
              </div>
              <div className="stat-card">
                <div className="stat-icon">
                  <TrendUp size={20} className="text-long" />
                </div>
                <div className="stat-content">
                  <div className="stat-value mono text-long">{longSignals}</div>
                  <div className="stat-label">Long</div>
                </div>
              </div>
              <div className="stat-card">
                <div className="stat-icon">
                  <TrendDown size={20} className="text-short" />
                </div>
                <div className="stat-content">
                  <div className="stat-value mono text-short">{shortSignals}</div>
                  <div className="stat-label">Short</div>
                </div>
              </div>
              {preSignals > 0 && (
                <div className="stat-card">
                  <div className="stat-icon">
                    <Clock size={20} className="text-warning" />
                  </div>
                  <div className="stat-content">
                    <div className="stat-value mono text-warning">{preSignals}</div>
                    <div className="stat-label">Pre-Signals</div>
                  </div>
                </div>
              )}
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
              {signals.slice(0, 5).map((signal, idx) => (
                <div key={idx} className="recent-signal-item">
                  <span className={`badge ${signal.type === 'LONG' ? 'badge-long' : 'badge-short'}`}>
                    {signal.signal_class === 'PRE_SIGNAL' ? 'PRE-' : ''}{signal.type}
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
        </>
      )}

      {view === 'time-based' && (
        <>
          <div className="analytics-section">
            <div className="section-title">
              ZEIT-ANALYSE: {getCoinName(selectedCoin)}
            </div>
            
            {!timeAnalytics || timeAnalytics.time_analytics?.length === 0 ? (
              <div className="no-data">
                Noch keine Zeit-Analyse verfügbar für {getCoinName(selectedCoin)}.
                <br /><br />
                Sobald Signale generiert werden, siehst du hier welche Uhrzeiten am 
                erfolgreichsten sind!
              </div>
            ) : (
              <>
                <div className="time-section">
                  <div className="time-subtitle text-long">🎯 BESTE ZEITEN</div>
                  {timeAnalytics.best_hours?.slice(0, 3).map((stat, idx) => (
                    <div key={idx} className="time-item">
                      <div className="time-info">
                        <span className="mono">{String(stat.hour).padStart(2, '0')}:00</span>
                        <span className="text-muted"> · {stat.weekday}</span>
                      </div>
                      <div className="time-stats">
                        <span className="mono text-long">
                          {stat.win_rate.toFixed(0)}% WR
                        </span>
                        <span className="text-muted">·</span>
                        <span className="mono">{stat.total_signals}x</span>
                      </div>
                    </div>
                  ))}
                </div>

                <div className="time-section">
                  <div className="time-subtitle text-short">⚠️ SCHLECHTESTE ZEITEN</div>
                  {timeAnalytics.worst_hours?.slice(0, 3).map((stat, idx) => (
                    <div key={idx} className="time-item">
                      <div className="time-info">
                        <span className="mono">{String(stat.hour).padStart(2, '0')}:00</span>
                        <span className="text-muted"> · {stat.weekday}</span>
                      </div>
                      <div className="time-stats">
                        <span className="mono text-short">
                          {stat.win_rate.toFixed(0)}% WR
                        </span>
                        <span className="text-muted">·</span>
                        <span className="mono">{stat.total_signals}x</span>
                      </div>
                    </div>
                  ))}
                </div>
              </>
            )}
          </div>
        </>
      )}
    </div>
  );
};

export default PerformanceAnalytics;
