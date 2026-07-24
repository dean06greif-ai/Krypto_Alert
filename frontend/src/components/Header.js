import React, { useState, useEffect, useCallback } from 'react';
import { Clock, Gear, ChartLineUp, Wallet, TrendUp, TrendDown, Lock, LockOpen, Trophy, ClockCounterClockwise, MagicWand } from '@phosphor-icons/react';
import { authHeaders } from '../auth';
import CapitalModal from './CapitalModal';
import './Header.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const BalanceWidget = () => {
  const [bal, setBal] = useState(null);
  const [showCapital, setShowCapital] = useState(false);

  const load = useCallback(async () => {
    try {
      const d = await fetch(`${API_URL}/api/autotrade/balance`).then(r => r.json());
      setBal(d);
    } catch (_) { /* ignore */ }
  }, []);

  useEffect(() => {
    load();
    const iv = setInterval(load, 15000);
    return () => clearInterval(iv);
  }, [load]);

  if (!bal) return null;
  const isLive = bal.mode === 'live';
  const pnl = bal.realized_pnl || 0;
  const pnlPos = pnl >= 0;

  // Paper overlay data
  const paperPnl = bal.paper_pnl ?? null;
  const hasPaperActivity = (paperPnl !== null && paperPnl !== 0);
  const paperPnlPos = (paperPnl || 0) >= 0;

  const alloc = bal.allocation?.[isLive ? 'live' : 'paper'];

  return (
    <div className="balance-widget-wrapper">
      <div className="balance-widget bw-clickable" data-testid="bitunix-balance-widget"
        onClick={() => setShowCapital(true)} title="Kapital-Zuweisung öffnen"
        role="button" tabIndex={0}>
        <div className={`bw-mode ${isLive ? 'live' : 'paper'}`} data-testid="bw-mode">
          <Wallet size={14} weight="fill" />
          {isLive ? 'LIVE' : 'PAPER'}
        </div>
        {isLive ? (
          bal.bitunix_configured ? (
            <div className="bw-stack" data-testid="bw-live-stack">
              <span className="bw-usdt-label">USDT</span>
              <span className="bw-primary-value mono" data-testid="bw-total">
                {bal.margin_balance != null ? Number(bal.margin_balance).toFixed(2) : (bal.bitunix_error ? 'API-Fehler' : '—')}
              </span>
              {alloc?.allocated != null ? (
                <span className="bw-sub-line" data-testid="bw-alloc">
                  <span className="bw-sub-label">Bot</span>
                  <span className="mono">{Number(alloc.allocated).toFixed(2)}</span>
                  <span className="bw-sub-label">· frei</span>
                  <span className="mono">{alloc.free != null ? Number(alloc.free).toFixed(2) : '—'}</span>
                </span>
              ) : (
                <span className="bw-sub-line" data-testid="bw-free">
                  <span className="bw-sub-label">Kapital</span>
                  <span className="mono">{bal.available != null ? Number(bal.available).toFixed(2) : '—'}</span>
                </span>
              )}
            </div>
          ) : (
            <div className="bw-item bw-warn" data-testid="bw-unconfigured">Bitunix nicht konfiguriert</div>
          )
        ) : (
          <div className="bw-stack" data-testid="bw-paper-stack">
            <span className="bw-usdt-label">PnL</span>
            <span className={`bw-primary-value mono ${pnlPos ? 'pos' : 'neg'}`}>
              {pnlPos ? <TrendUp size={13} weight="bold" /> : <TrendDown size={13} weight="bold" />}
              {pnl.toFixed(2)}
            </span>
            {alloc?.allocated != null && (
              <span className="bw-sub-line" data-testid="bw-alloc">
                <span className="bw-sub-label">Bot</span>
                <span className="mono">{Number(alloc.allocated).toFixed(2)}</span>
                <span className="bw-sub-label">· frei</span>
                <span className="mono">{alloc.free != null ? Number(alloc.free).toFixed(2) : '—'}</span>
              </span>
            )}
          </div>
        )}
      </div>

      {/* Paper Overlay - erscheint neben Live wenn Paper-Trades aktiv */}
      {isLive && hasPaperActivity && (
        <div className="paper-overlay" data-testid="paper-overlay">
          <div className="paper-overlay-mode">
            <Wallet size={12} weight="fill" />
            PAPER
          </div>
          <div className="paper-overlay-pnl">
            <span className={`bw-value mono ${paperPnlPos ? 'pos' : 'neg'}`}>
              {paperPnlPos ? <TrendUp size={11} weight="bold" /> : <TrendDown size={11} weight="bold" />}
              {(paperPnl || 0).toFixed(2)}
            </span>
          </div>
        </div>
      )}

      {showCapital && (
        <CapitalModal
          initialScope={isLive ? 'live' : 'paper'}
          onClose={() => setShowCapital(false)}
          onSaved={load}
        />
      )}
    </div>
  );
};

const Header = ({ sessionActive, onSettingsClick, currentSession, customSessions, activeStrategy, adminAuthed, onAdminClick, onCompareClick, onBacktestClick, onOptimizerClick }) => {
  const [currentTime, setCurrentTime] = useState(new Date());

  useEffect(() => {
    const timer = setInterval(() => {
      setCurrentTime(new Date());
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  const formatTime = (date) => {
    return date.toLocaleTimeString('de-DE', {
      hour: '2-digit', minute: '2-digit', second: '2-digit',
      timeZone: 'Europe/Berlin',
    });
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
          {is24_7 ? null : enabledSessions.length === 0 ? (
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
        <BalanceWidget />
        <button className="btn" onClick={onCompareClick} title="Strategie-Vergleich" data-testid="compare-strategies-button">
          <Trophy size={20} weight="bold" />
        </button>
        <button className="btn" onClick={onBacktestClick} title="Backtester (historische Daten, alle Timeframes)" data-testid="backtester-button">
          <ClockCounterClockwise size={20} weight="bold" />
        </button>
        <button className="btn" onClick={onOptimizerClick} title="Strategie-Optimizer (Parameter & Discovery)" data-testid="optimizer-button">
          <MagicWand size={20} weight="bold" />
        </button>
        <button
          className={`btn btn-admin ${adminAuthed ? 'is-admin' : ''}`}
          onClick={onAdminClick}
          title={adminAuthed ? 'Admin abmelden' : 'Admin-Login'}
          aria-label={adminAuthed ? 'Admin abmelden' : 'Admin-Login'}
          data-testid="admin-lock-button"
        >
          {adminAuthed
            ? <LockOpen size={20} weight="bold" />
            : <Lock size={20} weight="bold" />}
        </button>
        <button className="btn" onClick={onSettingsClick} data-testid="settings-button">
          <Gear size={20} weight="bold" />
        </button>
      </div>
    </header>
  );
};

export default Header;