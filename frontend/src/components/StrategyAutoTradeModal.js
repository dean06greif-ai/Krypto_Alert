import React, { useState, useEffect } from 'react';
import { toast } from 'sonner';
import { authHeaders, isAdmin } from '../auth';
import SafeOverlay from './SafeOverlay';
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
  be_mode: 'tp1',
  be_trigger_crv: 1.0,
  be_trigger_profit_pct: 30,
  require_all_rules: false,
  fee_percent: 0.06,
  trade_pre_signals: false,
  profit_secure_enabled: false,
  profit_secure_trigger_pct: 30,
  profit_lock_pct: 50,
};

export default function StrategyAutoTradeModal({ strategyId, strategyName, symbol, onClose, onSaved }) {
  const [cfg, setCfg] = useState(null);
  const [saving, setSaving] = useState(false);

  const fetchCfg = async () => {
    const res = await fetch(
      `${API_URL}/api/autotrade/strategy/${strategyId}/coin/${symbol}`,
      { headers: authHeaders() }
    );
    if (!res.ok) throw new Error('load failed');
    const data = await res.json();
    const loaded = { ...DEFAULT_CFG, ...(data.config || {}) };
    if (!['live', 'paper', 'off'].includes(loaded.mode)) loaded.mode = 'off';
    return loaded;
  };

  const load = async () => {
    try {
      setCfg(await fetchCfg());
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

  const postCfg = async (body) => fetch(
    `${API_URL}/api/autotrade/strategy/${strategyId}/coin/${symbol}`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...authHeaders() },
      body: JSON.stringify(body),
    }
  );

  const save = async () => {
    if (!isAdmin()) { toast.error('Admin-Login erforderlich'); return; }
    setSaving(true);
    try {
      let res = await postCfg(cfg);
      if (res.status === 401) { toast.error('Nicht autorisiert – bitte als Admin anmelden'); return; }
      if (!res.ok) { toast.error('Fehler beim Speichern'); return; }
      // Verifizieren, dass der Modus wirklich persistiert wurde (Fix:
      // Live/Paper-Umschaltung musste vorher teils 2x bestätigt werden).
      let verified = null;
      try { verified = await fetchCfg(); } catch { /* ignore */ }
      if (verified && verified.mode !== cfg.mode) {
        res = await postCfg(cfg);
        try { verified = await fetchCfg(); } catch { /* ignore */ }
        if (!verified || verified.mode !== cfg.mode) {
          toast.error('Modus konnte nicht bestätigt werden – bitte erneut prüfen');
          return;
        }
      }
      const modeLabel = { live: 'LIVE', paper: 'PAPER', off: 'AUS' }[cfg.mode];
      toast.success(`Gespeichert für ${coinShort} · Modus: ${modeLabel}`);
      onSaved?.();
      onClose?.();
    } catch {
      toast.error('Verbindungsfehler');
    } finally {
      setSaving(false);
    }
  };

  if (!cfg) return (
    <SafeOverlay className="at-overlay" onClose={onClose}>
      <div className="at-panel"><div className="sat-loading">Lädt...</div></div>
    </SafeOverlay>
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
    <SafeOverlay className="at-overlay" onClose={onClose}>
      <div className="at-panel" onClick={e => e.stopPropagation()} data-testid="strategy-autotrade-modal">

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

        <div className="at-mode-row">
          <div className="at-mode-toggle sat-mode-3" data-testid="sat-mode-toggle">
            <button
              className={cfg.mode === 'live' ? 'active live' : ''}
              onClick={() => setMode('live')}
              data-testid="sat-mode-live"
            >LIVE</button>
            <button
              className={cfg.mode === 'paper' ? 'active' : ''}
              onClick={() => setMode('paper')}
              data-testid="sat-mode-paper"
            >PAPER</button>
            <button
              className={cfg.mode === 'off' ? 'active off' : ''}
              onClick={() => setMode('off')}
              data-testid="sat-mode-off"
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
              <div className="at-field">
                <label>Break-Even Modus</label>
                <select
                  value={cfg.be_mode || (cfg.breakeven_enabled ? 'tp1' : 'off')}
                  onChange={e => {
                    const v = e.target.value;
                    setCfg(prev => ({ ...prev, be_mode: v, breakeven_enabled: v !== 'off' }));
                  }}
                  data-testid="sat-be-mode"
                >
                  <option value="tp1">Bei TP1 → SL auf Break-Even + Gebühren</option>
                  <option value="crv">Bei frei wählbarem CRV (z.B. 1R, 2R, 3R)</option>
                  <option value="profit_pct">Bei festem Gewinn-% auf die Marge</option>
                  <option value="smart">Smart (Backtest: Swing-Low/High, Live: wie TP1)</option>
                  <option value="off">Break-Even deaktiviert</option>
                </select>
              </div>
              {cfg.be_mode === 'crv' && (
                <div className="at-field small">
                  <label>Break-Even ab CRV (R)</label>
                  <input
                    type="number" step={0.1} min={0.1}
                    value={cfg.be_trigger_crv}
                    onChange={e => update('be_trigger_crv', parseFloat(e.target.value) || 1)}
                    data-testid="sat-be-crv"
                  />
                </div>
              )}
              {cfg.be_mode === 'profit_pct' && (
                <div className="at-field small">
                  <label>Break-Even ab Gewinn % auf Marge</label>
                  <input
                    type="number" step={5} min={1}
                    value={cfg.be_trigger_profit_pct}
                    onChange={e => update('be_trigger_profit_pct', parseFloat(e.target.value) || 30)}
                    data-testid="sat-be-pct"
                  />
                </div>
              )}
              <div className="at-field small">
                <label>Gebühren % (pro Fill, wird bei Paper & Live berechnet)</label>
                <input
                  type="number"
                  step={0.01}
                  value={cfg.fee_percent}
                  onChange={e => update('fee_percent', parseFloat(e.target.value) || 0)}
                  data-testid="sat-fee"
                />
              </div>
              <label className="at-check">
                <input
                  type="checkbox"
                  checked={!!cfg.trade_pre_signals}
                  onChange={e => update('trade_pre_signals', e.target.checked)}
                  data-testid="sat-pre-signals"
                />
                <span>Auch Pre-Signale traden</span>
              </label>
              <label className="at-check">
                <input
                  type="checkbox"
                  checked={!!cfg.require_all_rules}
                  onChange={e => update('require_all_rules', e.target.checked)}
                  data-testid="sat-require-all"
                />
                <span>Nur traden wenn ALLE Regeln erfüllt sind (100% Regel-Treffer)</span>
              </label>
            </div>

            {/* Gewinnsicherung */}
            <div className="at-block">
              <div className="at-block-title">GEWINNSICHERUNG</div>
              <label className="at-check" style={{ marginTop: 0 }}>
                <input
                  type="checkbox"
                  checked={!!cfg.profit_secure_enabled}
                  onChange={e => update('profit_secure_enabled', e.target.checked)}
                  data-testid="sat-profit-secure"
                />
                <span>Bei Gewinn: Stop-Loss in den Gewinn ziehen &amp; Marge freisetzen</span>
              </label>
              {cfg.profit_secure_enabled && (
                <div className="at-section" style={{ marginTop: 10 }}>
                  <div className="at-field">
                    <label>Auslöser: Gewinn % auf Marge</label>
                    <input
                      type="number"
                      step={5}
                      min={1}
                      value={cfg.profit_secure_trigger_pct}
                      onChange={e => update('profit_secure_trigger_pct', parseFloat(e.target.value) || 0)}
                      data-testid="sat-ps-trigger"
                    />
                  </div>
                  <div className="at-field">
                    <label>Gesicherter Gewinn-Anteil %</label>
                    <input
                      type="number"
                      step={5}
                      min={1}
                      max={95}
                      value={cfg.profit_lock_pct}
                      onChange={e => update('profit_lock_pct', parseFloat(e.target.value) || 0)}
                      data-testid="sat-ps-lock"
                    />
                  </div>
                </div>
              )}
              {cfg.profit_secure_enabled && (
                <div className="at-possize" style={{ marginBottom: 0 }}>
                  Ab <b>+{cfg.profit_secure_trigger_pct}%</b> Gewinn auf die Marge wird der SL so gesetzt,
                  dass <b>{cfg.profit_lock_pct}%</b> des Gewinns gesichert sind (Live: SL wird auf Bitunix nachgezogen).
                </div>
              )}
            </div>

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
    </SafeOverlay>
  );
}
