import React from 'react';
import { Clock, Gear } from '@phosphor-icons/react';
import './Header.css';

const Header = ({ sessionActive, onSettingsClick }) => {
  const [currentTime, setCurrentTime] = React.useState(new Date());

  React.useEffect(() => {
    const timer = setInterval(() => {
      setCurrentTime(new Date());
    }, 1000);

    return () => clearInterval(timer);
  }, []);

  const formatTime = (date) => {
    return date.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  return (
    <header className="header" data-testid="main-header">
      <div className="header-left">
        <h1 className="header-title">CRYPTO SCALPING SCANNER</h1>
        <div className="header-subtitle">4-Regel Strategie | Heikin Ashi | EMA 50/9 | RSI</div>
      </div>
      
      <div className="header-center">
        <div className="session-status">
          <Clock size={20} weight="bold" />
          <span className="mono">{formatTime(currentTime)}</span>
          <span className={`badge ${sessionActive ? 'badge-active' : 'badge-inactive'}`} data-testid="session-status-badge">
            {sessionActive ? 'TRADING SESSION ACTIVE' : 'OUTSIDE TRADING HOURS'}
          </span>
        </div>
        <div className="session-times">
          <span className="text-muted">London: 09:00-12:00</span>
          <span className="text-muted">|</span>
          <span className="text-muted">US: 15:30-18:30</span>
        </div>
      </div>
      
      <div className="header-right">
        <button className="btn" onClick={onSettingsClick} data-testid="settings-button">
          <Gear size={20} weight="bold" />
        </button>
      </div>
    </header>
  );
};

export default Header;
