import React from 'react';
import { TrendUp, TrendDown, Circle } from '@phosphor-icons/react';
import './CoinSidebar.css';

const TOP_10_COINS = [
  "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
  "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "MATICUSDT"
];

const CoinSidebar = ({ selectedCoin, onSelectCoin, performance }) => {
  const getCoinName = (symbol) => {
    return symbol.replace('USDT', '');
  };

  const getCoinPerformance = (symbol) => {
    return performance.find(p => p.symbol === symbol) || {};
  };

  return (
    <div className="coin-sidebar" data-testid="coin-sidebar">
      <div className="sidebar-header">
        <h3>TOP 10 COINS</h3>
        <div className="sidebar-subtitle">Live Scanner</div>
      </div>
      
      <div className="coin-list">
        {TOP_10_COINS.map(coin => {
          const perf = getCoinPerformance(coin);
          const isSelected = coin === selectedCoin;
          const hasSignals = perf.total_signals > 0;
          
          return (
            <div
              key={coin}
              className={`coin-item ${isSelected ? 'coin-item-selected' : ''}`}
              onClick={() => onSelectCoin(coin)}
              data-testid={`coin-item-${coin}`}
            >
              <div className="coin-header">
                <div className="coin-name">
                  <Circle 
                    size={8} 
                    weight="fill" 
                    className={hasSignals ? 'coin-indicator-active' : 'coin-indicator'}
                  />
                  <span className="mono">{getCoinName(coin)}</span>
                </div>
                {hasSignals && (
                  <span className="coin-signal-count mono">{perf.total_signals}</span>
                )}
              </div>
              
              {hasSignals && (
                <div className="coin-stats">
                  <div className="coin-stat">
                    <TrendUp size={12} className="text-long" />
                    <span className="mono text-secondary">{perf.long_signals || 0}</span>
                  </div>
                  <div className="coin-stat">
                    <TrendDown size={12} className="text-short" />
                    <span className="mono text-secondary">{perf.short_signals || 0}</span>
                  </div>
                  <div className="coin-stat">
                    <span className="text-muted">CRV</span>
                    <span className="mono text-secondary">{perf.avg_crv?.toFixed(2) || '0.00'}</span>
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
};

export default CoinSidebar;
