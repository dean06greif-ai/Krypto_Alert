import React, { useState, useEffect } from 'react';
import { Clock, Gear, ChartLineUp, Lock, LockOpen, Wallet, TrendUp, TrendDown, PauseCircle, PlayCircle } from '@phosphor-icons/react';
import { toast } from 'sonner';
import { authHeaders } from '../auth';
import './Header.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const BalanceWidget = () => {
  const [bal, setBal] = useState(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const d = await fetch(`${API_URL}/api/autotrade/balance`).then(r => r.json());
        if (alive) setBal(d);
      } catch (_) { /* ignore */ }
    };
    load();
    const iv = setInterval(load, 15000);
    return () => { alive = false; clearInterval(iv); };
  }, []);

  if (!bal) return null;
  const isLive = bal.mode === 'live';
  const pnl = bal.realized_pnl || 0;
  const pnlPos = pnl >= 0;

  return (
    <div className="balance-widget" data-testid="bitunix-balance-widget">
      <div className={`bw-mode ${isLive ? 'live' : 'paper'}`} data-testid="bw-mode">
        <Wallet size={14} weight="fill" />
        {isLive ? 'LIVE' : 'PAPER'}
      </div>
      {isLive ? (
        bal.bitunix_configured ? (
          <div className="bw-item" data-testid="bw-usdt">
            <span className="bw-label">USDT</span>
            <span className="bw-value mono">
              {bal.available != null ? Number(bal.available).toFixed(2) : (bal.bitunix_error ? 'API-Fehler' : '—')}
            </span>
          </div>
        ) : (
          <div className="bw-item bw-warn" data-testid="bw-unconfigured">Bitunix nicht konfiguriert</div>
        )
      ) : (
        <div className="bw-item" data-testid="bw-pnl">
          <span className="bw-label">PnL</span>
          <span className={`bw-value mono ${pnlPos ? 'pos' : 'neg'}`}>
            {pnlPos ? <TrendUp size={12} weight="bold" /> : <TrendDown size={12} weight="bold" />}
            {pnl.toFixed(2)}
          </span>
        </div>
      )}
      <div className="bw-item" data-testid="bw-open-trades">
        <span className="bw-label">Offen</span>
        <span className="bw-value mono">{bal.open_trades ?? 0}</span>
      </div>
    </div>
  );
};

const Header = ({ sessionActive, onSettingsClick, currentSession, customSessions, activeStrategy, isAdmin, onAdminClick, controlState, onControlChanged, onRequireAdmin }) => {
  const [currentTime, setCurrentTime] = useState(new Date());
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    const timer = setInterval(() => {
      setCurrentTime(new Date());
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  const toggleControl = async (kind) => {
    if (!isAdmin) { onRequireAdmin && onRequireAdmin(); return; }
    if (busy) return;
    setBusy(true);
    try {
      const path = kind === 'trades' ? 'stop-trades' : 'stop-signals';
      const r = await fetch(`${API_URL}/api/control/${path}`, {
        method: 'POST', headers: { ...authHeaders() },
      });
      if (!r.ok) throw new Error(await r.text());
      const d = await r.json();
      if (kind === 'trades') {
        toast.success(d.trades_paused
          ? `Trades gestoppt${d.closed_trades ? ` – ${d.closed_trades} Bot-Trade(s) geschlossen` : ''}`
          : 'Trades wieder aktiv');
      } else {
        toast.success(d.signals_paused ? 'Signals gestoppt' : 'Signals wieder aktiv');
      }
      onControlChanged && onControlChanged();
    } catch (e) {
      toast.error('Fehler: ' + (e.message || 'unbekannt'));
    } finally {
      setBusy(false);
    }
  };

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
        <div className="control-toggles" data-testid="admin-control-toggles">
          <button
            type="button"
            className={`ctrl-toggle ${controlState?.trades_paused ? 'ctrl-off' : 'ctrl-on'}`}
            onClick={() => toggleControl('trades')}
            disabled={busy}
            title={controlState?.trades_paused
              ? 'Trades sind pausiert – klicken zum Reaktivieren (nur Admin)'
              : 'Alle Bot-Trades stoppen & schließen (nur Admin)'}
            data-testid="toggle-stop-trades"
          >
            {controlState?.trades_paused
              ? <PlayCircle size={16} weight="fill" />
              : <PauseCircle size={16} weight="fill" />}
            <span className="ctrl-label">Trades</span>
            <span className={`ctrl-pill ${controlState?.trades_paused ? 'off' : 'on'}`}>
              {controlState?.trades_paused ? 'AUS' : 'AN'}
            </span>
          </button>
          <button
            type="button"
            className={`ctrl-toggle ${controlState?.signals_paused ? 'ctrl-off' : 'ctrl-on'}`}
            onClick={() => toggleControl('signals')}
            disabled={busy}
            title={controlState?.signals_paused
              ? 'Signals sind pausiert – klicken zum Reaktivieren (nur Admin)'
              : 'Alle Signals stoppen (nur Admin)'}
            data-testid="toggle-stop-signals"
          >
            {controlState?.signals_paused
              ? <PlayCircle size={16} weight="fill" />
              : <PauseCircle size={16} weight="fill" />}
            <span className="ctrl-label">Signals</span>
            <span className={`ctrl-pill ${controlState?.signals_paused ? 'off' : 'on'}`}>
              {controlState?.signals_paused ? 'AUS' : 'AN'}
            </span>
          </button>
        </div>
        <button
          className={`btn ${isAdmin ? 'btn-admin-on' : ''}`}
          onClick={onAdminClick}
          title={isAdmin ? 'Admin aktiv (klicken zum Abmelden)' : 'Admin-Login'}
          data-testid="admin-button"
        >
          {isAdmin ? <LockOpen size={20} weight="bold" /> : <Lock size={20} weight="bold" />}
        </button>
        <button className="btn" onClick={onSettingsClick} data-testid="settings-button">
          <Gear size={20} weight="bold" />
        </button>
      </div>
    </header>
  );
};

export default Header;
