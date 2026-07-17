import React, { useState, useEffect } from 'react';
import { X, Lightning, Warning } from '@phosphor-icons/react';
import { toast } from 'sonner';
import { authHeaders, isAdmin } from '../auth';
import './AutoTradeModal.css';
import './StrategyAutoTradeModal.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const DEFAULT_CFG = {
  enabled: false,
  mode: 'off',
  max_capital: 2.0,
  sl_pct: 1.0,
  tp_pct: 2.0,
  leverage: 5,
  signals_enabled: true,
};

const StrategyAutoTradeModal = ({ strategyId, strategyName, onClose, onSaved }) => {
  const [cfg, setCfg] = useState(null);
  const [saving, setSaving] = useState(false);
  const [bitunixOk, setBitunixOk] = useState(false);

  const load = async () => {
    try {
      const [stratRes, configRes] = await Promise.all([
        fetch(`${API_URL}/api/autotrade/strategy/${strategyId}`).then(r => r.json()),
        fetch(`${API_URL}/api/autotrade/config`).then(r => r.json()),
      ]);
      const loadedCfg = { ...DEFAULT_CFG, ...stratRes.config };
      setCfg(loadedCfg);
      setBitunixOk(configRes.bitunix_configured);
    } catch (e) {
      console.error('Failed to load strategy autotrade config', e);
      setCfg({ ...DEFAULT_CFG });
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [strategyId]);

  const update = (k, v) => setCfg(prev => ({ ...prev, [k]: v }));

  const setMode = (newMode) => {
    setCfg(prev => ({
      ...prev,
      mode: newMode,
      enabled: newMode !== 'off' ? true : prev.enabled,
    }));
  };

  const save = async () => {
    if (!isAdmin()) {
      toast.error('Admin-Login erforderlich zum Speichern');
      return;
    }
    setSaving(true);
    try {
      const res = await fetch(`${API_URL}/api/autotrade/strategy/${strategyId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(cfg),
      });
      if (res.status === 401) {
        toast.error('Nicht autorisiert – bitte als Admin anmelden');
        return;
      }
      if (res.ok) {
        toast.success(`Auto-Trade für ${strategyName || strategyId} gespeichert`);
        onSaved && onSaved();
        onClose();
      } else {
        const err = await res.json().catch(() => ({}));
        toast.error(err.detail || 'Fehler beim Speichern');
      }
    } catch (e) {
      toast.error('Verbindungsfehler beim Speichern');
    } finally {
      setSaving(false);
    }
  };

  if (!cfg) return null;

  return (
    <div className="at-overlay" onClick={onClose}>
      <div className="at-panel" onClick={e => e.stopPropagation()} data-testid="strategy-autotrade-modal">
        <div className="at-header">
          <div className="at-title">
            <Lightning size={20} weight="fill" />
            <span>AUTO-TRADE · {strategyName || strategyId}</span>
          </div>
          <button className="at-close" onClick={onClose} data-testid="sat-close">
            <X size={22} weight="bold" />
          </button>
        </div>

        {/* Mode Selection - same style as coin AutoTradeModal */}
        <div className="at-mode-row">
          <div className="at-mode-toggle sat-mode-3" data-testid="sat-mode-toggle">
            <button
              className={cfg.mode === 'off' ? 'active off' : ''}
              onClick={() => setMode('off')}
              data-testid="sat-mode-off"
            >
              AUS
            </button>
            <button
              className={cfg.mode === 'paper' ? 'active' : ''}
              onClick={() => setMode('paper')}
              data-testid="sat-mode-paper"
            >
              PAPER
            </button>
            <button
              className={cfg.mode === 'live' ? 'active live' : ''}
              onClick={() => setMode('live')}
              data-testid="sat-mode-live"
            >
              LIVE
            </button>
          </div>
        </div>

        {cfg.mode === 'live' && (
          <div className={`at-warn ${bitunixOk ? '' : 'err'}`}>
            <Warning size={16} weight="fill" />
            {bitunixOk
              ? 'LIVE Modus: Echte Orders auf Bitunix werden ausgeführt.'
              : 'Bitunix API nicht konfiguriert!'}
          </div>
        )}

        {cfg.mode !== 'off' && (
          <>
            {/* Capital & Leverage */}
            <div className="at-section">
              <div className="at-field">
                <label>Max. Kapital (USDT)</label>
                <input
                  type="number"
                  value={cfg.max_capital}
                  min={0.1}
                  step={0.1}
                  onChange={e => update('max_capital', parseFloat(e.target.value) || 0)}
                  data-testid="sat-max-capital"
                />
              </div>
              <div className="at-field">
                <label>Hebel: <b>{cfg.leverage}x</b></label>
                <input
                  type="range"
                  min={1}
                  max={125}
                  value={cfg.leverage}
                  onChange={e => update('leverage', parseInt(e.target.value))}
                  data-testid="sat-leverage"
                />
              </div>
            </div>

            {/* SL & TP */}
            <div className="at-section">
              <div className="at-field">
                <label>Stop Loss %</label>
                <input
                  type="number"
                  value={cfg.sl_pct}
                  min={0.1}
                  step={0.1}
                  onChange={e => update('sl_pct', parseFloat(e.target.value) || 0)}
                  data-testid="sat-sl-pct"
                />
              </div>
              <div className="at-field">
                <label>Take Profit %</label>
                <input
                  type="number"
                  value={cfg.tp_pct}
                  min={0.1}
                  step={0.1}
                  onChange={e => update('tp_pct', parseFloat(e.target.value) || 0)}
                  data-testid="sat-tp-pct"
                />
              </div>
            </div>

            <div className="at-possize">
              Positionsgröße: <b>{((cfg.max_capital || 0) * (cfg.leverage || 1)).toFixed(0)} USDT</b>
            </div>
          </>
        )}

        {/* Signal Notifications Toggle */}
        <div className="at-block">
          <label className="at-check">
            <input
              type="checkbox"
              checked={cfg.signals_enabled}
              onChange={e => update('signals_enabled', e.target.checked)}
              data-testid="sat-signals-enabled"
            />
            <span>Signal-Benachrichtigungen (Telegram) aktiv</span>
          </label>
        </div>

        <button
          className="at-save"
          onClick={save}
          disabled={saving}
          data-testid="sat-save"
        >
          {saving ? 'Speichere...' : 'Einstellungen speichern'}
        </button>

        <div style={{ textAlign: 'center', color: '#5C6070', fontSize: '11px', lineHeight: 1.4 }}>
          <small>Diese Einstellungen überschreiben die globalen Auto-Trade Settings für Signale dieser Strategie.</small>
        </div>
      </div>
    </div>
  );
};

export default StrategyAutoTradeModal;
