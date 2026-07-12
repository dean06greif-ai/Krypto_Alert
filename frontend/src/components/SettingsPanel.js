import React, { useState } from 'react';
import { X, TelegramLogo, Check, Warning } from '@phosphor-icons/react';
import { toast } from 'sonner';
import './SettingsPanel.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const SettingsPanel = ({ onClose }) => {
  const [telegramToken, setTelegramToken] = useState('');
  const [telegramChatId, setTelegramChatId] = useState('');
  const [testing, setTesting] = useState(false);

  const handleTestTelegram = async () => {
    setTesting(true);
    
    try {
      const response = await fetch(`${API_URL}/api/telegram/test`, {
        method: 'POST',
      });

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
          <div className="settings-section">
            <div className="section-header">
              <TelegramLogo size={24} weight="bold" />
              <div>
                <h3>Telegram Bot Setup</h3>
                <p className="section-description">
                  Erhalten Sie Trading-Signale direkt auf Ihr Handy
                </p>
              </div>
            </div>

            <div className="setup-steps">
              <div className="step-item">
                <div className="step-number">1</div>
                <div className="step-content">
                  <div className="step-title">Bot erstellen</div>
                  <div className="step-description">
                    Sende <code>/newbot</code> an <strong>@BotFather</strong> auf Telegram
                  </div>
                </div>
              </div>

              <div className="step-item">
                <div className="step-number">2</div>
                <div className="step-content">
                  <div className="step-title">Bot Token kopieren</div>
                  <div className="step-description">
                    BotFather gibt dir einen Token. Trage ihn in der <code>backend/.env</code> Datei ein:
                    <br />
                    <code>TELEGRAM_BOT_TOKEN="dein_token_hier"</code>
                  </div>
                </div>
              </div>

              <div className="step-item">
                <div className="step-number">3</div>
                <div className="step-content">
                  <div className="step-title">Chat ID erhalten</div>
                  <div className="step-description">
                    Sende eine Nachricht an deinen Bot, dann öffne:
                    <br />
                    <code>https://api.telegram.org/bot&lt;TOKEN&gt;/getUpdates</code>
                    <br />
                    Finde deine Chat ID und trage sie ein:
                    <br />
                    <code>TELEGRAM_CHAT_ID="deine_chat_id"</code>
                  </div>
                </div>
              </div>

              <div className="step-item">
                <div className="step-number">4</div>
                <div className="step-content">
                  <div className="step-title">Backend neu starten</div>
                  <div className="step-description">
                    Nach dem Speichern der .env Datei:
                    <br />
                    <code>sudo supervisorctl restart backend</code>
                  </div>
                </div>
              </div>
            </div>

            <button 
              className="btn btn-long" 
              onClick={handleTestTelegram} 
              disabled={testing}
              data-testid="test-telegram-button"
            >
              {testing ? 'Teste...' : 'Telegram Verbindung testen'}
            </button>
          </div>

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
                  <strong>Bitunix API Keys:</strong> Bereits in <code>backend/.env</code> gespeichert (nur Read-Only)
                </li>
                <li>
                  <strong>Trading Sessions:</strong> Scanner ist nur aktiv während London (9-12 Uhr) und US (15:30-18:30 Uhr) Sessions
                </li>
                <li>
                  <strong>Heikin Ashi:</strong> Charts verwenden Heikin Ashi Candles, nicht normale Candles
                </li>
                <li>
                  <strong>Keine Auto-Trading:</strong> Diese App sendet nur Signale, sie handelt NICHT automatisch
                </li>
              </ul>
            </div>
          </div>

          <div className="settings-section">
            <div className="section-header">
              <Check size={24} weight="bold" className="text-long" />
              <div>
                <h3>Deployment auf Render</h3>
              </div>
            </div>

            <div className="info-box">
              <ol className="info-list">
                <li>Erstelle ein neues Web Service auf Render.com</li>
                <li>Verbinde dein Git Repository</li>
                <li>Environment Variables hinzufügen:
                  <ul style={{ marginTop: '8px', paddingLeft: '20px' }}>
                    <li><code>MONGO_URL</code></li>
                    <li><code>DB_NAME</code></li>
                    <li><code>TELEGRAM_BOT_TOKEN</code></li>
                    <li><code>TELEGRAM_CHAT_ID</code></li>
                  </ul>
                </li>
                <li>Deploy starten - App läuft dann 24/7!</li>
              </ol>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default SettingsPanel;
