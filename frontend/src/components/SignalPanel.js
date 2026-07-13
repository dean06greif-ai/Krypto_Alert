import React, { useEffect, useState } from 'react';
import { CheckCircle, Circle, Info } from '@phosphor-icons/react';
import './SignalPanel.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const SignalPanel = ({ symbol, signals, activeStrategy, strategyParams }) => {
  const latestSignal = signals[0];

  // Build rules list dynamically based on active strategy
  const getRulesForStrategy = () => {
    if (!activeStrategy) return [];
    
    if (activeStrategy.id === 'scalping_4_rules') {
      const params = strategyParams || {};
      const rsiLong = params.rsi_long_threshold ?? 32;
      const rsiShort = params.rsi_short_threshold ?? 64;
      const emaSlow = params.ema_slow_period ?? 50;
      const emaFast = params.ema_fast_period ?? 9;
      return [
        { id: 'rule1_ema_slow', label: `EMA ${emaSlow} Position`, description: `Preis über/unter EMA ${emaSlow}` },
        { id: 'rule2_rsi', label: `RSI Level`, description: `RSI < ${rsiLong} (Long) / > ${rsiShort} (Short)` },
        { id: 'rule3_ema_fast_trigger', label: `EMA ${emaFast} Trigger`, description: `HA-Kerze schließt über/unter EMA ${emaFast}` },
        { id: 'rule4_time_window', label: 'Time Window', description: '2 Kerzen (Signal + Bestätigung)' },
      ];
    } else if (activeStrategy.id === 'rsi_only') {
      const params = strategyParams || {};
      const rsiLong = params.rsi_long_threshold ?? 30;
      const rsiShort = params.rsi_short_threshold ?? 70;
      return [
        { id: 'rsi_extreme', label: `RSI Extremwert`, description: `RSI < ${rsiLong} (Long) oder > ${rsiShort} (Short)` },
      ];
    }
    return [];
  };

  const rules = getRulesForStrategy();

  return (
    <div className="signal-panel" data-testid="signal-panel">
      <div className="panel-header">
        <div>
          <h3>{activeStrategy?.name?.toUpperCase() || 'STRATEGIE'}</h3>
          <div className="panel-subtitle">Live Signal Detection</div>
        </div>
        {activeStrategy && (
          <div className="strategy-active-indicator">
            <Info size={14} weight="bold" />
            <span>{activeStrategy.timeframe}</span>
          </div>
        )}
      </div>

      {rules.length > 0 && (
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
      )}

      {latestSignal && (
        <div className={`current-signal ${latestSignal.type === 'LONG' ? 'signal-long' : 'signal-short'}`}>
          <div className="signal-header">
            <span className={`badge ${latestSignal.type === 'LONG' ? 'badge-long' : 'badge-short'}`}>
              {latestSignal.signal_class === 'PRE_SIGNAL' ? 'PRE-' : ''}{latestSignal.type} SIGNAL
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
          <p className="text-muted">Warte auf Signal für {symbol?.replace('USDT', '')}...</p>
          {activeStrategy && (
            <p className="text-muted" style={{fontSize: '11px', marginTop: '8px'}}>
              Strategie: {activeStrategy.name}
            </p>
          )}
        </div>
      )}
    </div>
  );
};

export default SignalPanel;
