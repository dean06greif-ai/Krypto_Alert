import React, { useState, useEffect } from 'react';
import { X, TelegramLogo, Warning, Lightning, ChartLineUp, Plus, Trash, ArrowLeft, Sliders } from '@phosphor-icons/react';
import { toast } from 'sonner';
import { authHeaders } from '../auth';
import './SettingsPanel.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const SettingsPanel = ({ onClose, focusStrategy }) => {
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [activeTab, setActiveTab] = useState('strategy'); // strategy, sessions, telegram
  const [settings, setSettings] = useState({
    custom_sessions: [],
    pre_signal_enabled: true,
    active_strategy: 'scalping_4_rules',
    strategy_params: {},
    coin_params: {},
  });
  const [strategies, setStrategies] = useState([]);
  const [loading, setLoading] = useState(true);
  const [paramCoin, setParamCoin] = useState(''); // '' = Global, else per-coin override
  const ALL_COINS = ["BTCUSDT","ETHUSDT","BNBUSDT","SOLUSDT","XRPUSDT","ADAUSDT","DOGEUSDT","AVAXUSDT","DOTUSDT","POLUSDT","GOLD","SILVER","OIL"];

  useEffect(() => {
    Promise.all([
      fetch(`${API_URL}/api/settings`).then(r => r.json()),
      fetch(`${API_URL}/api/strategies`).then(r => r.json())
    ])
      .then(([settingsData, strategiesData]) => {
        setSettings({
          custom_sessions: settingsData.custom_sessions || [],
          pre_signal_enabled: settingsData.pre_signal_enabled !== false,
          active_strategy: settingsData.active_strategy || 'scalping_4_rules',
          strategy_params: settingsData.strategy_params || {},
          coin_params: settingsData.coin_params || {},
        });
        setStrategies(strategiesData.strategies || []);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const saveSettings = async (updatedSettings) => {
    setSaving(true);
    try {
      const response = await fetch(`${API_URL}/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(updatedSettings)
      });
      
      if (response.ok) {
        const data = await response.json();
        setSettings(prev => ({
          ...prev,
          custom_sessions: data.settings.custom_sessions || [],
          pre_signal_enabled: data.settings.pre_signal_enabled !== false,
          active_strategy: data.settings.active_strategy || 'scalping_4_rules',
          strategy_params: data.settings.strategy_params || {},
          coin_params: data.settings.coin_params || {},
        }));
        toast.success('Gespeichert');
      }
    } catch (error) {
      toast.error('Fehler beim Speichern');
    } finally {
      setSaving(false);
    }
  };

  const updateStrategyParam = (strategyId, paramKey, value) => {
    const v = parseFloat(value);
    if (paramCoin) {
      const cp = settings.coin_params || {};
      const stratCp = cp[strategyId] || {};
      const coinCp = { ...(stratCp[paramCoin] || {}), [paramKey]: v };
      const newCp = { ...cp, [strategyId]: { ...stratCp, [paramCoin]: coinCp } };
      setSettings({ ...settings, coin_params: newCp });
    } else {
      const currentParams = settings.strategy_params[strategyId] || {};
      const newParams = { ...settings.strategy_params, [strategyId]: { ...currentParams, [paramKey]: v } };
      setSettings({ ...settings, strategy_params: newParams });
    }
  };

  const commitParams = (strategyId) => {
    if (paramCoin) saveSettings({ coin_params: settings.coin_params });
    else saveSettings({ strategy_params: settings.strategy_params });
  };

  const resetStrategyParams = (strategyId) => {
    if (paramCoin) {
      const cp = { ...(settings.coin_params || {}) };
      if (cp[strategyId]) { delete cp[strategyId][paramCoin]; }
      setSettings({ ...settings, coin_params: cp });
      saveSettings({ coin_params: cp });
      toast.success(`${paramCoin} Parameter zurückgesetzt`);
    } else {
      const newParams = { ...settings.strategy_params };
      delete newParams[strategyId];
      setSettings({ ...settings, strategy_params: newParams });
      saveSettings({ strategy_params: newParams });
      toast.success('Parameter auf Standard zurückgesetzt');
    }
  };

  const getCurrentParamValue = (strategyId, paramKey, defaultValue) => {
    const globalVal = settings.strategy_params[strategyId]?.[paramKey];
    if (paramCoin) {
      const coinVal = settings.coin_params?.[strategyId]?.[paramCoin]?.[paramKey];
      return coinVal ?? globalVal ?? defaultValue;
    }
    return globalVal ?? defaultValue;
  };

  // Sessions handlers
  const togglePreSignal = (value) => {
    setSettings({ ...settings, pre_signal_enabled: value });
    saveSettings({ pre_signal_enabled: value });
  };

  const addSession = () => {
    const newSession = {
      start: "09:00", end: "12:00",
      name: `Session ${settings.custom_sessions.length + 1}`,
      enabled: true
    };
    const updated = [...settings.custom_sessions, newSession];
    setSettings({ ...settings, custom_sessions: updated });
    saveSettings({ custom_sessions: updated });
  };

  const removeSession = (index) => {
    const updated = settings.custom_sessions.filter((_, i) => i !== index);
    setSettings({ ...settings, custom_sessions: updated });
    saveSettings({ custom_sessions: updated });
  };

  const updateSession = (index, field, value) => {
    const updated = [...settings.custom_sessions];
    updated[index] = { ...updated[index], [field]: value };
    setSettings({ ...settings, custom_sessions: updated });
  };

  const commitSessionUpdate = () => saveSettings({ custom_sessions: settings.custom_sessions });

  const toggleSession = (index) => {
    const updated = [...settings.custom_sessions];
    updated[index] = { ...updated[index], enabled: !updated[index].enabled };
    setSettings({ ...settings, custom_sessions: updated });
    saveSettings({ custom_sessions: updated });
  };

  const enable24_7 = () => {
    setSettings({ ...settings, custom_sessions: [] });
    saveSettings({ custom_sessions: [] });
    toast.success('24/7 Modus aktiviert');
  };

  const restoreDefaults = () => {
    const defaults = [
      { start: "09:00", end: "12:00", name: "London", enabled: true },
      { start: "15:30", end: "18:30", name: "US", enabled: true }
    ];
    setSettings({ ...settings, custom_sessions: defaults });
    saveSettings({ custom_sessions: defaults });
  };

  const handleTestTelegram = async () => {
    setTesting(true);
    try {
      const response = await fetch(`${API_URL}/api/telegram/test`, { method: 'POST', headers: { ...authHeaders() } });
      if (response.ok) toast.success('Telegram Test erfolgreich!');
      else toast.error('Fehler');
    } catch {
      toast.error('Verbindungsfehler');
    } finally {
      setTesting(false);
    }
  };

  const activeStrategy = strategies.find(s => s.id === focusStrategy)
    || strategies.find(s => s.id === settings.active_strategy)
    || strategies[0];
  const is24_7 = settings.custom_sessions.length === 0;

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="settings-panel" onClick={(e) => e.stopPropagation()} data-testid="settings-panel">
        <div className="settings-header">
          <h2>EINSTELLUNGEN {saving && <span className="text-muted" style={{fontSize: '12px'}}>· Speichere...</span>}</h2>
          <button className="settings-close" onClick={onClose} data-testid="settings-close-button">
            <X size={24} weight="bold" />
          </button>
        </div>

        {/* Tab Navigation */}
        <div className="settings-tabs">
          <button 
            className={`settings-tab ${activeTab === 'strategy' ? 'active' : ''}`}
            onClick={() => setActiveTab('strategy')}
            data-testid="tab-strategy"
          >
            <ChartLineUp size={16} weight="bold" />
            Strategie
          </button>
          <button 
            className={`settings-tab ${activeTab === 'sessions' ? 'active' : ''}`}
            onClick={() => setActiveTab('sessions')}
            data-testid="tab-sessions"
          >
            <Lightning size={16} weight="bold" />
            Zeitfenster
          </button>
          <button 
            className={`settings-tab ${activeTab === 'telegram' ? 'active' : ''}`}
            onClick={() => setActiveTab('telegram')}
            data-testid="tab-telegram"
          >
            <TelegramLogo size={16} weight="bold" />
            Telegram
          </button>
        </div>

        <div className="settings-content">
          {/* STRATEGY TAB */}
          {activeTab === 'strategy' && (
            <>
              {/* Parameter Editor for the ACTIVE (selected tab) strategy only */}
              {activeStrategy && (
                <div className="settings-section">
                  <div className="section-simple-header">
                    <h3>
                      <Sliders size={18} weight="bold" style={{marginRight: '8px', display: 'inline-block', verticalAlign: 'middle'}} />
                      {activeStrategy.name}
                    </h3>
                    <div style={{ marginTop: 10, display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap' }}>
                      <span className="text-muted" style={{ fontSize: 12 }}>Gilt für:</span>
                      <select
                        value={paramCoin}
                        onChange={(e) => setParamCoin(e.target.value)}
                        data-testid="param-coin-select"
                        style={{ background: '#0A0A0A', border: '1px solid #2A2D3A', borderRadius: 8, padding: '7px 10px', color: '#fff' }}
                      >
                        <option value="">Alle Coins (Global)</option>
                        {ALL_COINS.map(c => <option key={c} value={c}>{c.replace('USDT', '')}</option>)}
                      </select>
                      {paramCoin && <span className="param-custom-badge">PRO COIN</span>}
                    </div>
                  </div>

                  <div className="params-list">
                    {Object.entries(activeStrategy.params || {}).map(([paramKey, paramMeta]) => {
                      const currentValue = getCurrentParamValue(
                        activeStrategy.id, 
                        paramKey, 
                        paramMeta.value
                      );
                      const isCustom = paramCoin
                        ? settings.coin_params?.[activeStrategy.id]?.[paramCoin]?.[paramKey] !== undefined
                        : settings.strategy_params[activeStrategy.id]?.[paramKey] !== undefined;
                      
                      return (
                        <div key={paramKey} className="param-item" data-testid={`param-${paramKey}`}>
                          <div className="param-info">
                            <div className="param-label">
                              {paramMeta.label}
                              {isCustom && <span className="param-custom-badge">CUSTOM</span>}
                            </div>
                            <div className="param-description">
                              {paramMeta.description}
                            </div>
                            <div className="param-range">
                              Min: {paramMeta.min} · Max: {paramMeta.max} · Default: {paramMeta.value}
                            </div>
                          </div>
                          <div className="param-input-wrapper">
                            <input
                              type="number"
                              className="param-input"
                              value={currentValue}
                              min={paramMeta.min}
                              max={paramMeta.max}
                              step={paramMeta.step}
                              onChange={(e) => updateStrategyParam(activeStrategy.id, paramKey, e.target.value)}
                              onBlur={() => commitParams(activeStrategy.id)}
                              data-testid={`param-input-${paramKey}`}
                            />
                          </div>
                        </div>
                      );
                    })}
                  </div>

                  <button 
                    className="btn btn-reset"
                    onClick={() => resetStrategyParams(activeStrategy.id)}
                    data-testid="reset-params-btn"
                  >
                    Alle Parameter zurücksetzen
                  </button>
                </div>
              )}

              {/* Pre-Signal Setting */}
              <div className="settings-section">
                <div className="setting-toggle">
                  <div className="toggle-info">
                    <div className="toggle-title">Pre-Signal Warnungen aktivieren</div>
                    <div className="toggle-description">
                      Frühwarnungen wenn Signal in Kürze zu erwarten ist
                    </div>
                  </div>
                  <label className="switch">
                    <input 
                      type="checkbox" 
                      checked={settings.pre_signal_enabled !== false}
                      onChange={(e) => togglePreSignal(e.target.checked)}
                      data-testid="pre-signal-toggle"
                    />
                    <span className="slider"></span>
                  </label>
                </div>
              </div>
            </>
          )}

          {/* SESSIONS TAB */}
          {activeTab === 'sessions' && (
            <>
              <div className="settings-section">
                <div className="session-mode-info">
                  {is24_7 ? (
                    <div className="mode-badge mode-badge-active">
                      ⚡ 24/7 MODUS AKTIV
                    </div>
                  ) : (
                    <div className="mode-badge">
                      📅 {settings.custom_sessions.filter(s => s.enabled).length} Zeitfenster
                    </div>
                  )}
                </div>

                <div className="sessions-list">
                  {settings.custom_sessions.map((session, index) => (
                    <div key={index} className="session-item" data-testid={`session-${index}`}>
                      <label className="switch switch-small">
                        <input 
                          type="checkbox" 
                          checked={session.enabled !== false}
                          onChange={() => toggleSession(index)}
                        />
                        <span className="slider"></span>
                      </label>
                      <input 
                        type="text" className="session-name"
                        value={session.name || ''}
                        onChange={(e) => updateSession(index, 'name', e.target.value)}
                        onBlur={commitSessionUpdate}
                      />
                      <div className="session-times">
                        <input 
                          type="time" value={session.start || '09:00'}
                          onChange={(e) => updateSession(index, 'start', e.target.value)}
                          onBlur={commitSessionUpdate}
                          className="time-input"
                        />
                        <span className="text-muted">-</span>
                        <input 
                          type="time" value={session.end || '12:00'}
                          onChange={(e) => updateSession(index, 'end', e.target.value)}
                          onBlur={commitSessionUpdate}
                          className="time-input"
                        />
                      </div>
                      <button 
                        className="btn-icon-remove"
                        onClick={() => removeSession(index)}
                      >
                        <Trash size={16} />
                      </button>
                    </div>
                  ))}
                </div>

                <div className="session-actions">
                  <button className="btn btn-add-session" onClick={addSession} data-testid="add-session-btn">
                    <Plus size={16} weight="bold" />
                    Zeitfenster hinzufügen
                  </button>
                  {!is24_7 && (
                    <button className="btn btn-24-7" onClick={enable24_7} data-testid="enable-24-7-btn">
                      <Lightning size={16} weight="bold" />
                      24/7 Modus
                    </button>
                  )}
                  {is24_7 && (
                    <button className="btn" onClick={restoreDefaults} data-testid="restore-defaults-btn">
                      Standard (London + US)
                    </button>
                  )}
                </div>
                
                <div className="info-hint">
                  💡 Alle Zeiten in deutscher Zeit (MEZ/CET). Ohne Zeitfenster → 24/7 Modus.
                </div>
              </div>
            </>
          )}

          {/* TELEGRAM TAB */}
          {activeTab === 'telegram' && (
            <>
              <div className="settings-section">
                <div className="info-box" style={{ borderColor: '#00FF66' }}>
                  <div className="info-text">
                    ✅ <strong>Bot verbunden:</strong> @Krypto_Strategy_Alert_Bot
                    <br />
                    Signale werden automatisch an dich gesendet
                  </div>
                </div>

                <button 
                  className="btn btn-long" 
                  onClick={handleTestTelegram} 
                  disabled={testing}
                  data-testid="test-telegram-button"
                >
                  {testing ? 'Teste...' : 'Test-Nachricht senden'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  );
};

export default SettingsPanel;
