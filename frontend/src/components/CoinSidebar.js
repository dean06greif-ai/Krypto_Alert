import React from 'react';
import { TrendUp, TrendDown, Circle, Bell, BellSlash } from '@phosphor-icons/react';
import './CoinSidebar.css';

const GROUPS = [
  {
    name: 'TOP 10 COINS',
    items: [
      "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
      "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "DOTUSDT", "POLUSDT"
    ]
  },
  {
    name: 'OTHER',
    items: ["GOLD", "SILVER", "OIL"]
  }
];

const DISPLAY_NAMES = { GOLD: 'GOLD', SILVER: 'SILVER', OIL: 'OIL' };

const CoinSidebar = ({ selectedCoin, onSelectCoin, performance, notifications = {}, onToggleNotification }) => {
  const getCoinName = (symbol) => {
    if (DISPLAY_NAMES[symbol]) return DISPLAY_NAMES[symbol];
    return symbol.replace('USDT', '');
  };

  const getCoinPerformance = (symbol) => {
    return performance.find(p => p.symbol === symbol) || {};
  };

  const renderItem = (coin) => {
    const perf = getCoinPerformance(coin);
    const isSelected = coin === selectedCoin;
    const hasSignals = perf.total_signals > 0;
    const notifyOn = notifications[coin] !== false; // default enabled

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
          <div className="coin-header-right">
            {hasSignals && (
              <span className="coin-signal-count mono">{perf.total_signals}</span>
            )}
            <button
              className={`notify-toggle ${notifyOn ? 'notify-on' : 'notify-off'}`}
              onClick={(e) => { e.stopPropagation(); onToggleNotification && onToggleNotification(coin); }}
              title={notifyOn ? 'Benachrichtigungen an (klicken zum Ausschalten)' : 'Benachrichtigungen aus (klicken zum Einschalten)'}
              data-testid={`notify-toggle-${coin}`}
            >
              {notifyOn ? <Bell size={15} weight="fill" /> : <BellSlash size={15} />}
            </button>
          </div>
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
  };

  return (
    <div className="coin-sidebar" data-testid="coin-sidebar">
      <div className="sidebar-header">
        <h3>MARKETS</h3>
        <div className="sidebar-subtitle">Live Scanner</div>
      </div>

      {GROUPS.map(group => (
        <div key={group.name} className="coin-group">
          <div className="coin-group-title" data-testid={`group-${group.name}`}>{group.name}</div>
          <div className="coin-list">
            {group.items.map(renderItem)}
          </div>
        </div>
      ))}
    </div>
  );
};

export default CoinSidebar;
