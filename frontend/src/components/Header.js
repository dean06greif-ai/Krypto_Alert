import React, { useState, useEffect } from 'react';
import { Clock, Gear, ChartLineUp } from '@phosphor-icons/react';
import './Header.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const Header = ({ sessionActive, onSettingsClick, currentSession, customSessions, activeStrategy }) => {
  const [currentTime, setCurrentTime] = useState(new Date());

  useEffect(() => {
    const timer = setInterval(() => {
      setCurrentTime(new Date());
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  const formatTime = (date) => {
    return date.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
  };

  const is24_7 = !customSessions || customSessions.length === 0;
  const enabledSessions = (customSessions || []).filter(s => s.enabled !== false);

  return (
    <header className="header" data-testid="main-header">
      <div className="header-left">
        <div className="header-brand">
          <ChartLineUp size={28} weight="bold" className="brand-icon" />
          <div>
            <h1 className="header-title">CRYPTO SCANNER</h1>
            {activeStrategy && (
              <div className="header-strategy" data-testid="active-strategy-display">
                🎯 {activeStrategy.name}
              </div>
            )}
          </div>
        </div>
      </div>
      
      <div className="header-center">
        <div className="session-status">
          <Clock size={20} weight="bold" />
          <span className="mono">{formatTime(currentTime)}</span>
          <span className={`badge ${sessionActive ? 'badge-active' : 'badge-inactive'}`} data-testid="session-status-badge">
            {sessionActive 
              ? (currentSession ? `${currentSession.toUpperCase()} · ACTIVE` : 'TRADING ACTIVE')
              : 'OUTSIDE SESSIONS'}
          </span>
        </div>
        <div className="session-times">
          {is24_7 ? (
            <span className="text-warning" style={{fontWeight: 600}}>⚡ 24/7 MODUS AKTIV</span>
          ) : enabledSessions.length === 0 ? (
            <span className="text-muted">Keine aktiven Sessions</span>
          ) : (
            enabledSessions.map((s, i) => (
              <span key={i} className="text-muted">
                {i > 0 && <span style={{margin: '0 4px'}}>|</span>}
                {s.name}: {s.start}-{s.end}
              </span>
            ))
          )}
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
