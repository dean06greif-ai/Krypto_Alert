import React, { useState, useEffect, useRef, useCallback } from 'react';
import { Robot, PaperPlaneRight, X, Trash, ArrowsClockwise, Lightning, CaretDown, CaretUp, Newspaper } from '@phosphor-icons/react';
import { toast } from 'sonner';
import { authHeaders } from '../auth';
import './AITradingPanel.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const MODEL_OPTIONS = [
  { provider: 'openai', model: 'gpt-5.4', label: 'GPT-5.4 (stark)' },
  { provider: 'openai', model: 'gpt-5.4-mini', label: 'GPT-5.4 Mini (günstig)' },
  { provider: 'anthropic', model: 'claude-sonnet-4-6', label: 'Claude Sonnet 4.6' },
  { provider: 'gemini', model: 'gemini-3-flash-preview', label: 'Gemini 3 Flash' },
];

const actionClass = (a) => (a === 'LONG' ? 'ai-long' : a === 'SHORT' ? 'ai-short' : 'ai-hold');

const AITradingPanel = ({ onClose }) => {
  const [status, setStatus] = useState(null);
  const [messages, setMessages] = useState([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [streamText, setStreamText] = useState('');
  const [analyzing, setAnalyzing] = useState(false);
  const [showSetup, setShowSetup] = useState(false);
  const chatEndRef = useRef(null);
  const streamingRef = useRef(false);

  const loadStatus = useCallback(async () => {
    try {
      const data = await fetch(`${API_URL}/api/ai/status`).then(r => r.json());
      setStatus(data);
    } catch (e) { /* silent */ }
  }, []);

  const loadHistory = useCallback(async () => {
    if (streamingRef.current) return;
    try {
      const data = await fetch(`${API_URL}/api/ai/chat/history?limit=100`).then(r => r.json());
      setMessages(data.messages || []);
    } catch (e) { /* silent */ }
  }, []);

  useEffect(() => {
    loadStatus(); loadHistory();
    const iv = setInterval(() => { loadStatus(); loadHistory(); }, 12000);
    return () => clearInterval(iv);
  }, [loadStatus, loadHistory]);

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamText]);

  const cfg = status?.config || {};
  const decisions = status?.decisions || {};

  const updateConfig = async (updates) => {
    try {
      const res = await fetch(`${API_URL}/api/ai/config`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify(updates),
      });
      if (!res.ok) { toast.error('Nicht autorisiert'); return; }
      const data = await res.json();
      setStatus(prev => ({ ...prev, config: data.config }));
      if ('enabled' in updates) toast.success(`KI Trader ${updates.enabled ? 'AKTIVIERT' : 'gestoppt'}`);
    } catch (e) { toast.error('Verbindungsfehler'); }
  };

  const analyzeNow = async () => {
    setAnalyzing(true);
    try {
      const res = await fetch(`${API_URL}/api/ai/analyze`, { method: 'POST', headers: authHeaders() });
      const data = await res.json();
      if (data.status === 'ok') {
        toast.success(`Analyse fertig: ${data.decisions} Coins, ${(data.signals || []).length} Signal(e)`);
      } else {
        toast.error(data.detail || 'Analyse fehlgeschlagen');
      }
      loadStatus(); loadHistory();
    } catch (e) { toast.error('Verbindungsfehler'); }
    setAnalyzing(false);
  };

  const clearChat = async () => {
    await fetch(`${API_URL}/api/ai/chat`, { method: 'DELETE', headers: authHeaders() });
    setMessages([]);
    toast.success('Chat geleert');
  };

  const sendMessage = async () => {
    const text = input.trim();
    if (!text || streaming) return;
    setInput('');
    setMessages(prev => [...prev, { id: `local-${Date.now()}`, role: 'user', text, ts: new Date().toISOString() }]);
    setStreaming(true);
    streamingRef.current = true;
    setStreamText('');
    let acc = '';
    try {
      const res = await fetch(`${API_URL}/api/ai/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ message: text }),
      });
      if (!res.ok) {
        toast.error(res.status === 401 ? 'Admin-Login erforderlich' : 'Chat-Fehler');
      } else {
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        // eslint-disable-next-line no-constant-condition
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          let idx;
          while ((idx = buf.indexOf('\n\n')) >= 0) {
            const line = buf.slice(0, idx).trim();
            buf = buf.slice(idx + 2);
            if (!line.startsWith('data: ')) continue;
            try {
              const p = JSON.parse(line.slice(6));
              if (p.t) { acc += p.t; setStreamText(acc); }
              if (p.error) toast.error(p.error);
            } catch (e) { /* skip */ }
          }
        }
      }
    } catch (e) { toast.error('Verbindungsfehler'); }
    if (acc) {
      setMessages(prev => [...prev, { id: `local-a-${Date.now()}`, role: 'assistant', text: acc, ts: new Date().toISOString() }]);
    }
    setStreamText('');
    setStreaming(false);
    streamingRef.current = false;
  };

  const fmtTime = (ts) => {
    try { return new Date(ts).toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit', timeZone: 'Europe/Berlin' }); }
    catch { return ''; }
  };

  const renderMessage = (m) => {
    if (m.role === 'analysis') {
      return (
        <div key={m.id} className="ai-msg ai-msg-analysis" data-testid="ai-analysis-message">
          <div className="ai-analysis-head">
            <Robot size={14} weight="fill" />
            <span>MARKT-ANALYSE {m.manual ? '(manuell)' : ''}</span>
            <span className="ai-msg-time">{fmtTime(m.ts)}</span>
          </div>
          {m.text && <div className="ai-analysis-overview">{m.text}</div>}
          {(m.decisions || []).length > 0 && (
            <div className="ai-analysis-decisions">
              {m.decisions.map((d, i) => (
                <div key={i} className={`ai-decision-row ${actionClass(d.action)}`}>
                  <span className="ai-dec-sym">{d.symbol.replace('USDT', '')}</span>
                  <span className={`ai-dec-action ${actionClass(d.action)}`}>{d.action}</span>
                  <span className="ai-dec-conf">{d.confidence}%</span>
                  {d.signaled && <span className="ai-dec-signaled" title="Signal ausgelöst"><Lightning size={11} weight="fill" /></span>}
                  <span className="ai-dec-reason">{d.reasoning}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      );
    }
    const isUser = m.role === 'user';
    return (
      <div key={m.id} className={`ai-msg ${isUser ? 'ai-msg-user' : 'ai-msg-assistant'}`}
        data-testid={isUser ? 'ai-chat-user-message' : 'ai-chat-assistant-message'}>
        <div className="ai-msg-bubble">{m.text}</div>
        <div className="ai-msg-time">{fmtTime(m.ts)}</div>
      </div>
    );
  };

  const modelValue = `${cfg.provider}|${cfg.model}`;

  return (
    <div className="ai-panel-overlay" onClick={onClose} data-testid="ai-trading-panel">
      <div className="ai-panel" onClick={e => e.stopPropagation()}>
        {/* Header */}
        <div className="ai-panel-header">
          <div className="ai-panel-title">
            <div className={`ai-robot-badge ${cfg.enabled ? 'on' : ''}`}><Robot size={20} weight="fill" /></div>
            <div>
              <h2>KI TRADER</h2>
              <span className="ai-panel-sub">
                {cfg.enabled
                  ? `Aktiv · analysiert alle ${cfg.interval_min} min${status?.analyzing ? ' · analysiert gerade…' : ''}`
                  : 'Ausgeschaltet – aktiviere die KI, damit sie eigenständig analysiert & tradet'}
              </span>
            </div>
          </div>
          <div className="ai-panel-header-actions">
            <button
              className={`ai-toggle ${cfg.enabled ? 'on' : ''}`}
              onClick={() => updateConfig({ enabled: !cfg.enabled })}
              data-testid="ai-enable-toggle"
            >
              <span className="ai-toggle-knob" />
              <span className="ai-toggle-label">{cfg.enabled ? 'AN' : 'AUS'}</span>
            </button>
            <button className="ai-icon-btn" onClick={onClose} data-testid="ai-panel-close"><X size={18} /></button>
          </div>
        </div>

        {!status?.has_key && (
          <div className="ai-warning" data-testid="ai-key-warning">
            ⚠ EMERGENT_LLM_KEY fehlt in backend/.env – ohne Key kann die KI nicht arbeiten.
          </div>
        )}
        {status?.last_error && (
          <div className="ai-warning" data-testid="ai-error-banner">⚠ {status.last_error}</div>
        )}

        {/* Status row */}
        <div className="ai-status-row">
          <button className="ai-action-btn" onClick={analyzeNow} disabled={analyzing || status?.analyzing} data-testid="ai-analyze-now-btn">
            <ArrowsClockwise size={14} weight="bold" className={analyzing || status?.analyzing ? 'spin' : ''} />
            {analyzing || status?.analyzing ? 'Analysiert…' : 'Jetzt analysieren'}
          </button>
          <span className="ai-status-info">
            Letzte Analyse: <b>{status?.last_run ? fmtTime(status.last_run) : '—'}</b>
          </span>
          <button className="ai-setup-toggle" onClick={() => setShowSetup(s => !s)} data-testid="ai-setup-toggle">
            Setup {showSetup ? <CaretUp size={12} /> : <CaretDown size={12} />}
          </button>
        </div>

        {/* Setup (collapsible) */}
        {showSetup && (
          <div className="ai-setup" data-testid="ai-setup-panel">
            <label>
              <span>KI-Modell</span>
              <select
                value={modelValue}
                onChange={e => {
                  const [provider, model] = e.target.value.split('|');
                  updateConfig({ provider, model });
                }}
                data-testid="ai-model-select"
              >
                {MODEL_OPTIONS.map(o => (
                  <option key={o.model} value={`${o.provider}|${o.model}`}>{o.label}</option>
                ))}
              </select>
            </label>
            <label>
              <span>Analyse-Intervall</span>
              <select value={cfg.interval_min || 10} onChange={e => updateConfig({ interval_min: Number(e.target.value) })} data-testid="ai-interval-select">
                {[5, 10, 15, 30, 60].map(v => <option key={v} value={v}>{v} min</option>)}
              </select>
            </label>
            <label>
              <span>Min. Konfidenz</span>
              <select value={cfg.min_confidence || 65} onChange={e => updateConfig({ min_confidence: Number(e.target.value) })} data-testid="ai-confidence-select">
                {[50, 60, 65, 70, 75, 80, 90].map(v => <option key={v} value={v}>{v}%</option>)}
              </select>
            </label>
            <label>
              <span>Trade-Cooldown</span>
              <select value={cfg.cooldown_min ?? 45} onChange={e => updateConfig({ cooldown_min: Number(e.target.value) })} data-testid="ai-cooldown-select">
                {[0, 15, 30, 45, 60, 120].map(v => <option key={v} value={v}>{v === 0 ? 'aus' : `${v} min`}</option>)}
              </select>
            </label>
            <label className="ai-setup-check">
              <span><Newspaper size={13} /> News</span>
              <input type="checkbox" checked={cfg.news_enabled !== false}
                onChange={e => updateConfig({ news_enabled: e.target.checked })} data-testid="ai-news-toggle" />
            </label>
          </div>
        )}

        {/* Decision chips */}
        {Object.keys(decisions).length > 0 && (
          <div className="ai-decisions-strip" data-testid="ai-decisions-strip">
            {Object.values(decisions).map(d => (
              <div key={d.symbol} className={`ai-chip ${actionClass(d.action)}`} title={d.reasoning}>
                <span className="ai-chip-sym">{d.symbol.replace('USDT', '')}</span>
                <span className="ai-chip-action">{d.action}</span>
                <span className="ai-chip-conf">{d.confidence}%</span>
              </div>
            ))}
          </div>
        )}

        {/* Chat */}
        <div className="ai-chat-area" data-testid="ai-chat-area">
          {messages.length === 0 && !streaming && (
            <div className="ai-chat-empty">
              <Robot size={36} weight="light" />
              <p>Sag der KI, worauf sie achten soll – z.B.<br />
                <em>„Achte auf den BTC-Support bei 60k"</em> oder <em>„Sei heute defensiv, nur Longs".</em><br />
                Jede Nachricht fließt in die nächste Analyse ein.</p>
            </div>
          )}
          {messages.map(renderMessage)}
          {streaming && (
            <div className="ai-msg ai-msg-assistant">
              <div className="ai-msg-bubble">{streamText || <span className="ai-typing">KI denkt nach…</span>}</div>
            </div>
          )}
          <div ref={chatEndRef} />
        </div>

        {/* Input */}
        <div className="ai-input-row">
          <input
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter') sendMessage(); }}
            placeholder="Anweisung oder Frage an die KI…"
            disabled={streaming}
            data-testid="ai-chat-input"
          />
          <button className="ai-send-btn" onClick={sendMessage} disabled={streaming || !input.trim()} data-testid="ai-chat-send-btn">
            <PaperPlaneRight size={16} weight="fill" />
          </button>
          <button className="ai-icon-btn" onClick={clearChat} title="Chat leeren" data-testid="ai-chat-clear-btn">
            <Trash size={15} />
          </button>
        </div>
        <div className="ai-panel-footer">
          Auto-Trading pro Coin über das <Lightning size={11} weight="fill" color="#FFD60A" />-Symbol am „KI Trader"-Tab konfigurieren (Paper/Live).
        </div>
      </div>
    </div>
  );
};

export default AITradingPanel;
