import React from 'react';
import { CheckCircle, Circle } from '@phosphor-icons/react';
import './SignalPanel.css';

const SignalPanel = ({ symbol, signals }) => {
  const latestSignal = signals[0];

  const rules = [
    { id: 'rule1_ema50', label: 'EMA 50 Position', description: 'Price above/below EMA 50' },
    { id: 'rule2_rsi', label: 'RSI Level', description: 'RSI < 32 (Long) or > 64 (Short)' },
    { id: 'rule3_ema9_trigger', label: 'EMA 9 Trigger', description: 'HA Candle closes above/below EMA 9' },
    { id: 'rule4_time_window', label: 'Time Window', description: '2 Candles (Signal + Confirmation)' },
  ];

  return (
    <div className="signal-panel" data-testid="signal-panel">
      <div className="panel-header">
        <h3>4-REGEL STRATEGIE</h3>
        <div className="panel-subtitle">Live Signal Detection</div>
      </div>

      <div className="rules-grid">
        {rules.map(rule => {
          const isMet = latestSignal?.rules_met?.[rule.id] || false;
          
          return (
            <div key={rule.id} className="rule-item" data-testid={`rule-${rule.id}`}>
              <div className="rule-icon">
                {isMet ? (
                  <CheckCircle size={20} weight="fill" className="text-long" />
                ) : (
                  <Circle size={20} className="text-muted" />
                )}
              </div>
              <div className="rule-content">
                <div className="rule-label">{rule.label}</div>
                <div className="rule-description">{rule.description}</div>
              </div>
            </div>
          );
        })}
      </div>

      {latestSignal && (
        <div className={`current-signal ${latestSignal.type === 'LONG' ? 'signal-long' : 'signal-short'}`}>
          <div className="signal-header">
            <span className={`badge ${latestSignal.type === 'LONG' ? 'badge-long' : 'badge-short'}`}>
              {latestSignal.type} SIGNAL
            </span>
            <span className="mono text-muted" style={{ fontSize: '11px' }}>
              {new Date(latestSignal.timestamp).toLocaleTimeString('de-DE')}
            </span>
          </div>

          <div className="signal-prices">
            <div className="price-item">
              <span className="price-label">ENTRY</span>
              <span className="price-value mono">${latestSignal.entry_price}</span>
            </div>
            <div className="price-item">
              <span className="price-label">STOP LOSS</span>
              <span className="price-value mono text-short">${latestSignal.stop_loss}</span>
            </div>
            <div className="price-item">
              <span className="price-label">TP1 (40%)</span>
              <span className="price-value mono text-long">${latestSignal.take_profit_1}</span>
            </div>
            <div className="price-item">
              <span className="price-label">TP FULL</span>
              <span className="price-value mono text-long">${latestSignal.take_profit_full}</span>
            </div>
          </div>

          <div className="signal-crv">
            <span className="crv-label">COST-REWARD RATIO</span>
            <span className="crv-value mono">{latestSignal.crv}</span>
          </div>
        </div>
      )}

      {!latestSignal && (
        <div className="no-signal">
          <Circle size={32} weight="thin" className="text-muted" />
          <p className="text-muted">Waiting for signal...</p>
        </div>
      )}
    </div>
  );
};

export default SignalPanel;
