import React, { useState, useEffect } from 'react';
import { X, Lightning, Warning } from '@phosphor-icons/react';
import { toast } from 'sonner';
import { authHeaders, isAdmin } from '../auth';
import './AutoTradeModal.css';
import './StrategyAutoTradeModal.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

// Full parity with coin AutoTradeModal + explicit "off" mode.
// Reihenfolge der Tabs: LIVE | PAPER | AUS (AUS voreingestellt).
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

const StrategyAutoTradeModal = ({ strategyId, strategyName, onClose, onSaved }) => {
  const [cfg, setCfg] = useState(null);
  const [saving, setSaving] = useState(false);
  const [bitunixOk, setBitunixOk] = useState(false);
  const [coinToggles, setCoinToggles] = useState({}); // {SYMBOL: bool}

  const load = async () => {
    try {
      const [stratRes, configRes, coinsRes] = await Promise.all([
        fetch(`${API_URL}/api/autotrade/strategy/${strategyId}`).then(r => r.json()),
        fetch(`${API_URL}/api/autotrade/config`).then(r => r.json()),
        fetch(`${API_URL}/api/strategies/${strategyId}/coins`).then(r => r.json()),
      ]);
      // Merge: coin defaults (backend) -> strategy override -> local defaults
      const coinDefaults = configRes.defaults || {};
      const stratDefaults = stratRes.defaults || {};
      const loadedCfg = {
        ...DEFAULT_CFG,
        ...coinDefaults,
        ...stratDefaults,
        ...(stratRes.config || {}),
      };
      // Ensure mode is one of live|paper|off
      if (!['live', 'paper', 'off'].includes(loadedCfg.mode)) {
        loadedCfg.mode = 'off';
      }
      setCfg(loadedCfg);
      setBitunixOk(configRes.bitunix_configured);
      setCoinToggles(coinsRes.coins || {});
    } catch (e) {
      console.error('Failed to load strategy autotrade config', e);
      setCfg({ ...DEFAULT_CFG });
    }
  };

  useEffect(() => { load(); /* eslint-disable-next-line */ }, [strategyId]);

  const update = (k, v) => setCfg(prev => ({ ...prev, [k]: v }));

  const toggleCoin = async (symbol) => {
    if (!isAdmin()) {
      toast.error('Admin-Login erforderlich');
      return;
    }
    const next = !(coinToggles[symbol] !== false); // flip; missing => true, so first click => false
    const prev = coinToggles;
    // Optimistic update
    setCoinToggles({ ...prev, [symbol]: next });
    try {
      const res = await fetch(
        `${API_URL}/api/strategies/${strategyId}/coins/${symbol}`,
        {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json', ...authHeaders() },
          body: JSON.stringify({ enabled: next }),
        }
      );
      if (!res.ok) {
        setCoinToggles(prev); // rollback
        toast.error(`Fehler bei ${symbol}`);
      }
    } catch (e) {
      setCoinToggles(prev); // rollback
      toast.error('Verbindungsfehler');
    }
  };

  const setMode = (newMode) => {
    setCfg(prev => ({
      ...prev,
      mode: newMode,
      // enabled tracks mode: off -> disabled, live/paper -> enabled
      enabled: newMode !== 'off',
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
        toast.success(
          `Auto-Trade ${strategyName || strategyId} gespeichert (${cfg.mode.toUpperCase()})`
        );
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
  const posSize = ((cfg.max_capital || 0) * (cfg.leverage || 1)).toFixed(0);

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

        {/* Mode Selection – Reihenfolge: LIVE | PAPER | AUS (AUS default rechts) */}
        <div className="at-mode-row">
          <div className="at-mode-toggle sat-mode-3" data-testid="sat-mode-toggle">
            <button
              className={cfg.mode === 'live' ? 'active live' : ''}
              onClick={() => setMode('live')}
              data-testid="sat-mode-live"
            >
              LIVE
            </button>
            <button
              className={cfg.mode === 'paper' ? 'active' : ''}
              onClick={() => setMode('paper')}
              data-testid="sat-mode-paper"
            >
              PAPER
            </button>
            <button
              className={cfg.mode === 'off' ? 'active off' : ''}
              onClick={() => setMode('off')}
              data-testid="sat-mode-off"
            >
              AUS
            </button>
          </div>
        </div>

        {cfg.mode === 'live' && (
          <div className={`at-warn ${bitunixOk ? '' : 'err'}`}>
            <Warning size={16} weight="fill" />
            {bitunixOk
              ? 'LIVE Modus: Echte Orders auf Bitunix (nur auf deinem Server ausführbar).'
              : 'Bitunix API nicht konfiguriert!'}
          </div>
        )}

        {cfg.mode !== 'off' && (
          <>
            {/* Capital + leverage (Bitunix-like) */}
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

            {/* SL system */}
            <div className="at-block">
              <div className="at-block-title">STOP LOSS</div>
              <div className="at-seg">
                <button
                  className={cfg.sl_mode === 'structure' ? 'active' : ''}
                  onClick={() => update('sl_mode', 'structure')}
                  data-testid="sat-sl-mode-structure"
                >
                  Struktur (Support/Widerstand)
                </button>
                <button
                  className={cfg.sl_mode === 'fixed' ? 'active' : ''}
                  onClick={() => update('sl_mode', 'fixed')}
                  data-testid="sat-sl-mode-fixed"
                >
                  Fest %
                </button>
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

            {/* TP system */}
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
          </>
        )}

        {/* Signal Notifications Toggle – always visible */}
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

        {/* Per-coin activation grid: enable/disable this strategy for
             individual coins. Parameters above stay the same for all coins.
             Missing entries default to enabled=true. */}
        <div className="at-block">
          <div className="at-block-title">AKTIVE COINS FÜR DIESE STRATEGIE</div>
          <div className="sat-coin-grid" data-testid="sat-coin-grid">
            {['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT','ADAUSDT','DOGEUSDT','AVAXUSDT','DOTUSDT','POLUSDT','GOLD','SILVER','OIL'].map(sym => {
              const on = coinToggles[sym] !== false;
              const short = ['GOLD','SILVER','OIL'].includes(sym) ? sym : sym.replace('USDT','');
              return (
                <button
                  key={sym}
                  type="button"
                  className={`sat-coin-chip ${on ? 'on' : 'off'}`}
                  onClick={() => toggleCoin(sym)}
                  data-testid={`sat-coin-toggle-${sym}`}
                  title={on ? `Aktiv – klick zum Ausschalten (${sym})` : `Inaktiv – klick zum Einschalten (${sym})`}
                >
                  <span className="sat-coin-dot" />
                  <span className="sat-coin-name">{short}</span>
                </button>
              );
            })}
          </div>
          <div style={{ color: '#5C6070', fontSize: '11px', marginTop: '6px' }}>
            Parameter oben gelten für alle aktiven Coins. Deaktivierte Coins
            werden von dieser Strategie ignoriert (keine Signale, keine Trades).
          </div>
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
          <small>
            Diese Einstellungen überschreiben die globalen Auto-Trade Settings für Signale
            dieser Strategie. Modus &quot;AUS&quot; deaktiviert Trades und ist voreingestellt.
          </small>
        </div>
      </div>
    </div>
  );
};

export default StrategyAutoTradeModal;
