import React, { useState, useEffect } from 'react';
import { X, TelegramLogo, Warning, Lightning, ChartLineUp, Plus, Trash } from '@phosphor-icons/react';
import { toast } from 'sonner';
import './SettingsPanel.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const SettingsPanel = ({ onClose }) => {
  const [testing, setTesting] = useState(false);
  const [saving, setSaving] = useState(false);
  const [settings, setSettings] = useState({
    custom_sessions: [],
    pre_signal_enabled: true,
  });
  const [loading, setLoading] = useState(true);

  // Fetch current settings
  useEffect(() => {
    fetch(`${API_URL}/api/settings`)
      .then(r => r.json())
      .then(data => {
        setSettings({
          custom_sessions: data.custom_sessions || [],
          pre_signal_enabled: data.pre_signal_enabled !== false,
        });
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const saveSettings = async (updatedSettings) => {
    setSaving(true);
    try {
      const response = await fetch(`${API_URL}/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(updatedSettings)
      });
      
      if (response.ok) {
        const data = await response.json();
        setSettings({
          custom_sessions: data.settings.custom_sessions || [],
          pre_signal_enabled: data.settings.pre_signal_enabled !== false,
        });
        toast.success('Einstellungen gespeichert');
      } else {
        toast.error('Fehler beim Speichern');
      }
    } catch (error) {
      toast.error('Verbindungsfehler');
    } finally {
      setSaving(false);
    }
  };

  const togglePreSignal = (value) => {
    const updated = { ...settings, pre_signal_enabled: value };
    setSettings(updated);
    saveSettings({ pre_signal_enabled: value });
  };

  const addSession = () => {
    const newSession = {
      start: "09:00",
      end: "12:00",
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

  const commitSessionUpdate = () => {
    saveSettings({ custom_sessions: settings.custom_sessions });
  };

  const toggleSession = (index) => {
    const updated = [...settings.custom_sessions];
    updated[index] = { ...updated[index], enabled: !updated[index].enabled };
    setSettings({ ...settings, custom_sessions: updated });
    saveSettings({ custom_sessions: updated });
  };

  const enable24_7 = () => {
    setSettings({ ...settings, custom_sessions: [] });
    saveSettings({ custom_sessions: [] });
    toast.success('24/7 Modus aktiviert - Scanner läuft rund um die Uhr!');
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
      const response = await fetch(`${API_URL}/api/telegram/test`, { method: 'POST' });
      if (response.ok) {
        toast.success('Telegram Test erfolgreich!');
      } else {
        const data = await response.json();
        toast.error(`Fehler: ${data.detail || 'Telegram nicht konfiguriert'}`);
      }
    } catch (error) {
      toast.error('Verbindungsfehler');
    } finally {
      setTesting(false);
    }
  };

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

        <div className="settings-content">
          {/* Trading Zeitfenster */}
          <div className="settings-section">
            <div className="section-header">
              <Lightning size={24} weight="bold" className="text-warning" />
              <div>
                <h3>Trading Zeitfenster</h3>
                <p className="section-description">
                  Wann soll der Scanner Signale generieren?
                </p>
              </div>
            </div>

            <div className="session-mode-info">
              {is24_7 ? (
                <div className="mode-badge mode-badge-active">
                  ⚡ 24/7 MODUS AKTIV - Scanner läuft rund um die Uhr
                </div>
              ) : (
                <div className="mode-badge">
                  📅 {settings.custom_sessions.filter(s => s.enabled).length} Zeitfenster konfiguriert
                </div>
              )}
            </div>

            {/* Sessions List */}
            <div className="sessions-list">
              {settings.custom_sessions.map((session, index) => (
                <div key={index} className="session-item" data-testid={`session-${index}`}>
                  <label className="switch switch-small">
                    <input 
                      type="checkbox" 
                      checked={session.enabled !== false}
                      onChange={() => toggleSession(index)}
                      data-testid={`session-toggle-${index}`}
                    />
                    <span className="slider"></span>
                  </label>
                  
                  <input 
                    type="text" 
                    className="session-name"
                    value={session.name || ''}
                    onChange={(e) => updateSession(index, 'name', e.target.value)}
                    onBlur={commitSessionUpdate}
                    placeholder="Name"
                    data-testid={`session-name-${index}`}
                  />
                  
                  <div className="session-times">
                    <input 
                      type="time" 
                      value={session.start || '09:00'}
                      onChange={(e) => updateSession(index, 'start', e.target.value)}
                      onBlur={commitSessionUpdate}
                      className="time-input"
                      data-testid={`session-start-${index}`}
                    />
                    <span className="text-muted">-</span>
                    <input 
                      type="time" 
                      value={session.end || '12:00'}
                      onChange={(e) => updateSession(index, 'end', e.target.value)}
                      onBlur={commitSessionUpdate}
                      className="time-input"
                      data-testid={`session-end-${index}`}
                    />
                  </div>
                  
                  <button 
                    className="btn-icon-remove"
                    onClick={() => removeSession(index)}
                    data-testid={`session-remove-${index}`}
                  >
                    <Trash size={16} />
                  </button>
                </div>
              ))}
            </div>

            <div className="session-actions">
              <button 
                className="btn btn-add-session"
                onClick={addSession}
                data-testid="add-session-btn"
              >
                <Plus size={16} weight="bold" />
                Zeitfenster hinzufügen
              </button>
              
              {!is24_7 && (
                <button 
                  className="btn btn-24-7"
                  onClick={enable24_7}
                  data-testid="enable-24-7-btn"
                >
                  <Lightning size={16} weight="bold" />
                  24/7 Modus (alle löschen)
                </button>
              )}
              
              {is24_7 && (
                <button 
                  className="btn"
                  onClick={restoreDefaults}
                  data-testid="restore-defaults-btn"
                >
                  Standard wiederherstellen (London + US)
                </button>
              )}
            </div>
            
            <div className="info-hint">
              💡 <strong>Tipp:</strong> Alle Zeiten sind in deutscher Zeit (MEZ/CET). 
              Standard: London 09:00-12:00, US 15:30-18:30. 
              <br />
              Ohne Zeitfenster → 24/7 Modus aktiv.
            </div>
          </div>

          {/* Pre-Signal Settings */}
          <div className="settings-section">
            <div className="section-header">
              <ChartLineUp size={24} weight="bold" className="text-warning" />
              <div>
                <h3>Pre-Signal Warnings</h3>
                <p className="section-description">Frühwarnungen bevor alle 4 Regeln erfüllt sind</p>
              </div>
            </div>

            <div className="setting-toggle">
              <div className="toggle-info">
                <div className="toggle-title">Pre-Signals aktivieren</div>
                <div className="toggle-description">
                  Erhalte Warnungen wenn 3 von 4 Regeln erfüllt sind und die 4. bald folgt
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

          {/* Telegram */}
          <div className="settings-section">
            <div className="section-header">
              <TelegramLogo size={24} weight="bold" />
              <div>
                <h3>Telegram Bot</h3>
                <p className="section-description">Handy-Alerts sind aktiv!</p>
              </div>
            </div>

            <div className="info-box" style={{ borderColor: '#00FF66' }}>
              <div className="info-text">
                ✅ <strong>Bot verbunden:</strong> @Krypto_Strategy_Alert_Bot
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

          {/* Info */}
          <div className="settings-section">
            <div className="section-header">
              <Warning size={24} weight="bold" className="text-warning" />
              <div>
                <h3>Wichtige Hinweise</h3>
              </div>
            </div>

            <div className="info-box">
              <ul className="info-list">
                <li>
                  <strong>Zeitfenster:</strong> Signale werden nur in aktivierten Zeitfenstern gesendet
                </li>
                <li>
                  <strong>Pre-Signals</strong> haben 3 von 4 Regeln - trade erst wenn 4. folgt!
                </li>
                <li>
                  <strong>Deutsche Zeit:</strong> Alle Zeitangaben sind in MEZ/CET
                </li>
                <li>
                  <strong>Bitunix API läuft READ-ONLY</strong> - kein Auto-Trading
                </li>
              </ul>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SettingsPanel;
