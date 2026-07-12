import React, { useState, useEffect } from 'react';
import { X, TelegramLogo, Check, Warning, Lightning, ChartLineUp } from '@phosphor-icons/react';
import { toast } from 'sonner';
import './SettingsPanel.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const SettingsPanel = ({ onClose }) => {
  const [testing, setTesting] = useState(false);
  const [settings, setSettings] = useState({
    test_mode_24_7: false,
    pre_signal_enabled: true,
  });
  const [loading, setLoading] = useState(true);

  // Fetch current settings
  useEffect(() => {
    fetch(`${API_URL}/api/settings`)
      .then(r => r.json())
      .then(data => {
        setSettings(data);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, []);

  const updateSetting = async (key, value) => {
    const newSettings = { ...settings, [key]: value };
    setSettings(newSettings);
    
    try {
      const response = await fetch(`${API_URL}/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ [key]: value })
      });
      
      if (response.ok) {
        toast.success('Einstellung gespeichert');
      }
    } catch (error) {
      toast.error('Fehler beim Speichern');
      setSettings(settings);
    }
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

  return (
    <div className="settings-overlay" onClick={onClose}>
      <div className="settings-panel" onClick={(e) => e.stopPropagation()} data-testid="settings-panel">
        <div className="settings-header">
          <h2>EINSTELLUNGEN</h2>
          <button className="settings-close" onClick={onClose} data-testid="settings-close-button">
            <X size={24} weight="bold" />
          </button>
        </div>

        <div className="settings-content">
          {/* Scanner Settings */}
          <div className="settings-section">
            <div className="section-header">
              <Lightning size={24} weight="bold" className="text-warning" />
              <div>
                <h3>Scanner Einstellungen</h3>
                <p className="section-description">Steuere wann und wie Signale erkannt werden</p>
              </div>
            </div>

            <div className="setting-toggle">
              <div className="toggle-info">
                <div className="toggle-title">24/7 Test Mode</div>
                <div className="toggle-description">
                  Signale auch außerhalb der Trading-Sessions (zum Testen)
                </div>
              </div>
              <label className="switch">
                <input 
                  type="checkbox" 
                  checked={settings.test_mode_24_7 || false}
                  onChange={(e) => updateSetting('test_mode_24_7', e.target.checked)}
                  data-testid="test-mode-toggle"
                />
                <span className="slider"></span>
              </label>
            </div>

            <div className="setting-toggle">
              <div className="toggle-info">
                <div className="toggle-title">Pre-Signal Warnings</div>
                <div className="toggle-description">
                  Frühwarnungen wenn 3 von 4 Regeln erfüllt und 4. steht bevor
                </div>
              </div>
              <label className="switch">
                <input 
                  type="checkbox" 
                  checked={settings.pre_signal_enabled !== false}
                  onChange={(e) => updateSetting('pre_signal_enabled', e.target.checked)}
                  data-testid="pre-signal-toggle"
                />
                <span className="slider"></span>
              </label>
            </div>
          </div>

          {/* Analytics Info */}
          <div className="settings-section">
            <div className="section-header">
              <ChartLineUp size={24} weight="bold" className="text-long" />
              <div>
                <h3>Zeit-basierte Analytics</h3>
                <p className="section-description">
                  Neue Feature: Sieh welche Coins zu welchen Uhrzeiten am besten performen
                </p>
              </div>
            </div>

            <div className="info-box">
              <div className="info-text">
                📊 <strong>Wo finde ich das?</strong>
                <br />
                Öffne die Performance Analytics im rechten Panel - dort werden nun 
                pro Coin die besten und schlechtesten Uhrzeiten angezeigt basierend 
                auf historischen Signals.
                <br /><br />
                <strong>Was gemessen wird:</strong>
                <ul style={{ marginTop: '8px', paddingLeft: '20px' }}>
                  <li>Uhrzeit (0-23 Uhr)</li>
                  <li>Wochentag</li>
                  <li>Anzahl Signale (Long/Short)</li>
                  <li>Win-Rate pro Zeitfenster</li>
                  <li>Durchschnittliches CRV</li>
                </ul>
              </div>
            </div>
          </div>

          {/* Telegram Setup */}
          <div className="settings-section">
            <div className="section-header">
              <TelegramLogo size={24} weight="bold" />
              <div>
                <h3>Telegram Bot</h3>
                <p className="section-description">Deine Handy-Alerts sind aktiv!</p>
              </div>
            </div>

            <div className="info-box" style={{ borderColor: '#00FF66' }}>
              <div className="info-text">
                ✅ <strong>Bot verbunden:</strong> @Krypto_Strategy_Alert_Bot
                <br />
                Du bekommst automatisch Nachrichten bei jedem Signal!
              </div>
            </div>

            <button 
              className="btn btn-long" 
              onClick={handleTestTelegram} 
              disabled={testing}
              data-testid="test-telegram-button"
            >
              {testing ? 'Teste...' : 'Telegram Test-Nachricht senden'}
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
                  <strong>Pre-Signals</strong> haben nur 3 von 4 Regeln erfüllt - 
                  handeln nur wenn 4. Regel folgt!
                </li>
                <li>
                  <strong>24/7 Test Mode:</strong> Nutze nur zum Testen, sonst Sessions einhalten!
                </li>
                <li>
                  <strong>Bitunix API läuft READ-ONLY</strong> (kein Auto-Trading)
                </li>
                <li>
                  <strong>Trading Sessions:</strong> London 9-12 Uhr, US 15:30-18:30 Uhr
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
