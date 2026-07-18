import React, { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { authHeaders, isAdmin } from '../auth';
import './AutoTradeModal.css';
import './StrategyAutoTradeModal.css';

const API_URL = process.env.REACT_APP_BACKEND_URL || '';

const DEFAULT_CFG = {
  enabled: false,
  mode: 'off',
  signals_enabled: true,
  max_capital: 100.0,
  leverage: 10,
  order_type: 'MARKET',
  sl_mode: 'structure',
  sl_fixed_percent: 1.0,
  sl_ticks: 4,
  sl_lookback: 10,
  tp1_crv: 1.0,
  tp1_close_percent: 50,
  tp_full_crv: 2.0,
  breakeven_enabled: true,
  fee_percent: 0.06,
  trade_pre_signals: false,
};

export default function StrategyAutoTradeModal({ strategyId, strategyName, symbol, onClose, onSaved }) {
  const [cfg, setCfg] = useState(null);
  const [saving, setSaving] = useState(false);

  const load = async () => {
    try {
      const res = await fetch(
        `${API_URL}/api/autotrade/strategy/${strategyId}/coin/${symbol}`,
        { headers: authHeaders() }
      );
      if (res.ok) {
        const data = await res.json();
        const loaded = { ...DEFAULT_CFG, ...(data.config || {}) };
        if (!['live', 'paper', 'off'].includes(loaded.mode)) loaded.mode = 'off';
        setCfg(loaded);
      } else {
        setCfg({ ...DEFAULT_CFG });
      }
    } catch {
      setCfg({ ...DEFAULT_CFG });
    }
  };

  useEffect(() => {
    if (strategyId && symbol) load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [strategyId, symbol]);

  const update = (k, v) => setCfg(prev => ({ ...prev, [k]: v }));

  const setMode = (newMode) =>
    setCfg(prev => ({ ...prev, mode: newMode, enabled: newMode !== 'off' }));

  const save = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    setSaving(true);
    try {
      const res = await fetch(
        `${API_URL}/api/autotrade/strategy/${strategyId}/coin/${symbol}`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify(cfg),
        }
      );
      if (res.ok) {
        toast.success(`Gespeichert für ${coinShort}`);
        onSaved?.();
        onClose?.();
      } else {
        toast.error('Fehler beim Speichern');
      }
    } catch {
      toast.error('Verbindungsfehler');
    } finally {
      setSaving(false);
    }
  };

  if (!cfg) return (
    <div className="at-overlay" onClick={onClose}>
      <div className="at-panel"><div className="sat-loading">Lädt...</div></div>
    </div>
  );

  const coinShort = ['GOLD', 'SILVER', 'OIL'].includes(symbol)
    ? symbol
    : (symbol || '').replace('USDT', '');
  const posSize = cfg.max_capital
    ? (cfg.max_capital * (cfg.leverage || 1)).toFixed(2)
    : '–';

  const modeMeta = {
    live:  { cls: 'live',  label: 'ECHTGELD · LIVE',      pill: 'LIVE'  },
    paper: { cls: 'paper', label: 'SIMULATION · PAPER',   pill: 'PAPER' },
    off:   { cls: 'off',   label: 'DEAKTIVIERT · AUS',    pill: 'AUS'   },
  };
  const m = modeMeta[cfg.mode] || modeMeta.off;

  return (
    <div className="at-overlay" onClick={onClose}>
      <div className="at-panel" onClick={e => e.stopPropagation()} data-testid="strategy-autotrade-modal">

        {/* Header (same look as AutoTradeModal) */}
        <div className="at-header">
          <div className="at-title">
            <span style={{ fontSize: 18 }}>⚡</span>
            <span>AUTO-TRADE · {(strategyName || strategyId || '').toUpperCase()}</span>
            {coinShort && (
              <span className="sat-coin-badge">{coinShort}</span>
            )}
          </div>
          <button className="at-close" onClick={onClose} data-testid="strategy-autotrade-close">✕</button>
        </div>

        {/* Active-Mode Banner (immer sichtbar) */}
        <div className={`sat-active-mode ${m.cls}`} data-testid="sat-active-mode">
          <div className="sat-active-mode-dot" />
          <div className="sat-active-mode-text">
            <div className="sat-active-mode-label">AKTIVER MODUS</div>
            <div className="sat-active-mode-value">{m.label}</div>
          </div>
          <div className="sat-active-mode-pill" data-testid="sat-active-mode-pill">
            {m.pill}
          </div>
        </div>

        {/* Mode Selection (LIVE / PAPER / AUS) */}
        <div className="at-mode-row">
          <div className="at-mode-toggle sat-mode-3" data-testid="sat-mode-toggle">
            <button
              className={cfg.mode === 'live' ? 'active live' : ''}
              onClick={() => setMode('live')}
            >LIVE</button>
            <button
              className={cfg.mode === 'paper' ? 'active' : ''}
              onClick={() => setMode('paper')}
            >PAPER</button>
            <button
              className={cfg.mode === 'off' ? 'active off' : ''}
              onClick={() => setMode('off')}
            >AUS</button>
          </div>
        </div>

        {cfg.mode === 'live' && (
          <div className="at-warn err">
            ⚠️ LIVE-Modus: Echtes Geld wird gehandelt. Bitunix API muss konfiguriert sein.
          </div>
        )}

        {cfg.mode !== 'off' && (
          <>
            {/* Capital + Leverage */}
            <div className="at-section">
              <div className="at-field">
                <label>Max. Kapital (USDT Margin)</label>
                <input
                  type="number"
                  value={cfg.max_capital}
                  min={1}
                  step={1}
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
            <div className="at-possize">
              Positionsgröße: <b>{posSize} USDT</b> · Order: {cfg.order_type || 'MARKET'}
            </div>

            {/* Stop Loss */}
            <div className="at-block">
              <div className="at-block-title">STOP LOSS</div>
              <div className="at-seg">
                <button
                  className={cfg.sl_mode === 'structure' ? 'active' : ''}
                  onClick={() => update('sl_mode', 'structure')}
                >Struktur (Support/Widerstand)</button>
                <button
                  className={cfg.sl_mode === 'fixed' ? 'active' : ''}
                  onClick={() => update('sl_mode', 'fixed')}
                >Fest %</button>
              </div>
              {cfg.sl_mode === 'structure' ? (
                <div className="at-section">
                  <div className="at-field">
                    <label>Ticks unter/über Tief/Hoch</label>
                    <input
                      type="number"
                      value={cfg.sl_ticks}
                      onChange={e => update('sl_ticks', parseInt(e.target.value) || 0)}
                      data-testid="sat-sl-ticks"
                    />
                  </div>
                  <div className="at-field">
                    <label>Lookback (Kerzen)</label>
                    <input
                      type="number"
                      value={cfg.sl_lookback}
                      onChange={e => update('sl_lookback', parseInt(e.target.value) || 0)}
                      data-testid="sat-sl-lookback"
                    />
                  </div>
                </div>
              ) : (
                <div className="at-field">
                  <label>SL Abstand %</label>
                  <input
                    type="number"
                    step={0.1}
                    value={cfg.sl_fixed_percent}
                    onChange={e => update('sl_fixed_percent', parseFloat(e.target.value) || 0)}
                    data-testid="sat-sl-percent"
                  />
                </div>
              )}
            </div>

            {/* Take Profit */}
            <div className="at-block">
              <div className="at-block-title">TAKE PROFIT (dynamisch)</div>
              <div className="at-section">
                <div className="at-field">
                  <label>TP1 bei CRV</label>
                  <input
                    type="number"
                    step={0.1}
                    value={cfg.tp1_crv}
                    onChange={e => update('tp1_crv', parseFloat(e.target.value) || 0)}
                    data-testid="sat-tp1-crv"
                  />
                </div>
                <div className="at-field">
                  <label>TP1 schließt % der Position</label>
                  <input
                    type="number"
                    min={1}
                    max={99}
                    value={cfg.tp1_close_percent}
                    onChange={e => update('tp1_close_percent', parseInt(e.target.value) || 0)}
                    data-testid="sat-tp1-close"
                  />
                </div>
              </div>
              <div className="at-field">
                <label>TP Full bei CRV</label>
                <input
                  type="number"
                  step={0.1}
                  value={cfg.tp_full_crv}
                  onChange={e => update('tp_full_crv', parseFloat(e.target.value) || 0)}
                  data-testid="sat-tpfull-crv"
                />
              </div>
              <label className="at-check">
                <input
                  type="checkbox"
                  checked={!!cfg.breakeven_enabled}
                  onChange={e => update('breakeven_enabled', e.target.checked)}
                  data-testid="sat-breakeven"
                />
                <span>Bei CRV 1 → Stop Loss auf Break-Even + Gebühren</span>
              </label>
              {cfg.breakeven_enabled && (
                <div className="at-field small">
                  <label>Gebühren % (Round-Trip Offset)</label>
                  <input
                    type="number"
                    step={0.01}
                    value={cfg.fee_percent}
                    onChange={e => update('fee_percent', parseFloat(e.target.value) || 0)}
                    data-testid="sat-fee"
                  />
                </div>
              )}
              <label className="at-check">
                <input
                  type="checkbox"
                  checked={!!cfg.trade_pre_signals}
                  onChange={e => update('trade_pre_signals', e.target.checked)}
                  data-testid="sat-pre-signals"
                />
                <span>Auch Pre-Signale traden</span>
              </label>
            </div>

            {/* Signals */}
            <div className="at-block">
              <label className="at-check">
                <input
                  type="checkbox"
                  checked={cfg.signals_enabled !== false}
                  onChange={e => update('signals_enabled', e.target.checked)}
                  data-testid="sat-signals-enabled"
                />
                <span>Signal-Benachrichtigungen (Telegram) aktiv</span>
              </label>
            </div>
          </>
        )}

        {/* Action Buttons */}
        <div className="sat-actions">
          <button className="sat-cancel-btn" onClick={onClose} data-testid="sat-cancel">
            Abbrechen
          </button>
          <button
            className="at-save"
            style={{ margin: 0, flex: 1 }}
            onClick={save}
            disabled={saving}
            data-testid="sat-save"
          >
            {saving ? 'Speichern...' : `Speichern für ${coinShort}`}
          </button>
        </div>
      </div>
    </div>
  );
}
