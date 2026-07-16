import React, { useState, useEffect, useRef, useCallback } from 'react';
import './App.css';
import './components/extra.css';
import Header from './components/Header';
import CoinSidebar from './components/CoinSidebar';
import MainChart from './components/MainChart';
import SignalPanel from './components/SignalPanel';
import StrategyTabs from './components/StrategyTabs';
import PerformanceAnalytics from './components/PerformanceAnalytics';
import AlertModal from './components/AlertModal';
import SettingsPanel from './components/SettingsPanel';
import StrategyBuilder from './components/StrategyBuilder';
import AutoTradeModal from './components/AutoTradeModal';
import ErrorBoundary from './components/ErrorBoundary';
import AdminLogin from './components/AdminLogin';
import { Toaster, toast } from 'sonner';
import { isAdmin as isAdminFn, clearToken, authHeaders } from './auth';

const API_URL = process.env.REACT_APP_BACKEND_URL;

function App() {
  const [selectedCoin, setSelectedCoin] = useState('BTCUSDT');
  const [strategies, setStrategies] = useState([]);
  const [enabledIds, setEnabledIds] = useState([]);
  const [signalsEnabled, setSignalsEnabled] = useState({});
  const [selectedStrategy, setSelectedStrategy] = useState(null);
  const [signals, setSignals] = useState([]);
  const [performance, setPerformance] = useState([]);
  const [ruleStates, setRuleStates] = useState({});
  const [candleData, setCandleData] = useState({});
  const [notifications, setNotifications] = useState({});
  const [autotradeCoins, setAutotradeCoins] = useState({});
  const [sessionActive, setSessionActive] = useState(false);
  const [currentSession, setCurrentSession] = useState('');
  const [customSessions, setCustomSessions] = useState([]);
  const [berlinTime, setBerlinTime] = useState('');
  const [showAlert, setShowAlert] = useState(false);
  const [currentAlert, setCurrentAlert] = useState(null);
  const [showSettings, setShowSettings] = useState(false);
  const [showBuilder, setShowBuilder] = useState(false);
  const [autoTradeSymbol, setAutoTradeSymbol] = useState(null);
  const [adminAuthed, setAdminAuthed] = useState(isAdminFn());
  const [showLogin, setShowLogin] = useState(false);
  const [controlState, setControlState] = useState({ trades_paused: false, signals_paused: false });

  const wsRef = useRef(null);
  const audioRef = useRef(null);
  const notificationsRef = useRef({});
  const reconnectRef = useRef(null);

  useEffect(() => { notificationsRef.current = notifications; }, [notifications]);

  // ---- data loaders ----
  const loadStrategies = useCallback(async () => {
    try {
      const data = await fetch(`${API_URL}/api/strategies`).then(r => r.json());
      setStrategies(data.strategies || []);
      setEnabledIds(data.enabled || []);
      setSignalsEnabled(data.signals_enabled || {});
      setSelectedStrategy(prev => {
        if (prev && (data.enabled || []).includes(prev)) return prev;
        return (data.enabled || [])[0] || null;
      });
    } catch (e) { console.error(e); }
  }, []);

  const loadAutotrade = useCallback(async () => {
    try {
      const data = await fetch(`${API_URL}/api/autotrade/config`).then(r => r.json());
      setAutotradeCoins((data.config && data.config.coins) || {});
    } catch (e) { console.error(e); }
  }, []);

  const loadSignals = useCallback(async () => {
    try {
      const data = await fetch(`${API_URL}/api/signals?limit=80`).then(r => r.json());
      setSignals(data.signals || []);
    } catch (e) { console.error(e); }
  }, []);

  const loadPerformance = useCallback(async () => {
    try {
      const data = await fetch(`${API_URL}/api/performance`).then(r => r.json());
      setPerformance(data.performance || []);
    } catch (e) { console.error(e); }
  }, []);

  const loadControlState = useCallback(async () => {
    try {
      const data = await fetch(`${API_URL}/api/control/state`).then(r => r.json());
      setControlState({
        trades_paused: !!data.trades_paused,
        signals_paused: !!data.signals_paused,
      });
    } catch (e) { console.error(e); }
  }, []);

  // ---- WebSocket with auto-reconnect ----
  const connectWS = useCallback(() => {
    try {
      const wsUrl = API_URL.replace('http', 'ws') + '/api/ws';
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => { toast.success('Verbunden mit Scanner'); };
      ws.onmessage = (event) => {
        let message;
        try { message = JSON.parse(event.data); } catch { return; }
        if (message.type === 'signal') {
          const signal = message.data;
          setSignals(prev => [signal, ...prev].slice(0, 120));
          const notifyEnabled = notificationsRef.current[signal.symbol] !== false;
          if (notifyEnabled && signal.signal_class !== 'PRE_SIGNAL') {
            setCurrentAlert(signal); setShowAlert(true);
            if (audioRef.current) audioRef.current.play().catch(() => {});
            toast.success(`${signal.type} · ${signal.symbol.replace('USDT','')}`, {
              description: `${signal.strategy_name} · Entry $${signal.entry_price}`, duration: 5000,
            });
          }
        } else if (message.type === 'candle') {
          setCandleData(prev => ({ ...prev, [message.symbol]: message.data }));
        } else if (message.type === 'rule_states') {
          setRuleStates(prev => ({ ...prev, [message.symbol]: message.data }));
        } else if (message.type === 'daily_reset') {
          setSignals([]);
          toast.info('Täglicher Reset: Signale zurückgesetzt, Analyse bleibt erhalten');
          loadPerformance();
        } else if (message.type === 'analytics_cleared') {
          loadSignals();
          loadPerformance();
        } else if (message.type === 'control_state') {
          setControlState({
            trades_paused: !!message.data?.trades_paused,
            signals_paused: !!message.data?.signals_paused,
          });
        }
      };
      ws.onerror = () => {};
      ws.onclose = () => {
        if (reconnectRef.current) clearTimeout(reconnectRef.current);
        reconnectRef.current = setTimeout(connectWS, 3000);
      };
    } catch (e) { console.error('WS connect failed', e); }
  }, [loadPerformance, loadSignals]);

  useEffect(() => {
    connectWS();
    try {
      if (typeof window !== 'undefined' && 'Notification' in window && window.Notification.permission === 'default') {
        window.Notification.requestPermission().catch(() => {});
      }
    } catch (_) { /* iOS Safari & private mode: Notification API not available */ }
    return () => {
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      if (wsRef.current) { wsRef.current.onclose = null; wsRef.current.close(); }
    };
  }, [connectWS]);

  useEffect(() => { loadStrategies(); loadAutotrade(); loadSignals(); loadPerformance(); loadControlState(); }, [loadStrategies, loadAutotrade, loadSignals, loadPerformance, loadControlState]);
  useEffect(() => {
    const iv = setInterval(loadPerformance, 60000);
    return () => clearInterval(iv);
  }, [loadPerformance]);

  // session + notifications
  useEffect(() => {
    const fetchSession = async () => {
      try {
        const data = await fetch(`${API_URL}/api/session/status`).then(r => r.json());
        setSessionActive(data.is_active);
        setCurrentSession(data.current_session || '');
        setCustomSessions(data.custom_sessions || []);
        setBerlinTime(data.berlin_time || '');
      } catch (e) { console.error(e); }
    };
    const fetchNotif = async () => {
      try {
        const data = await fetch(`${API_URL}/api/settings`).then(r => r.json());
        setNotifications(data.notifications || {});
      } catch (e) { console.error(e); }
    };
    fetchSession(); fetchNotif();
    const iv = setInterval(fetchSession, 30000);
    return () => clearInterval(iv);
  }, [showSettings]);

  // ---- admin gate ----
  const requireAdmin = (action) => {
    if (isAdminFn()) { action(); }
    else { setShowLogin(true); toast.info('Admin-Login erforderlich'); }
  };
  const handleAdminClick = () => {
    if (adminAuthed) { clearToken(); setAdminAuthed(false); toast.success('Admin abgemeldet'); }
    else { setShowLogin(true); }
  };

  // ---- actions ----
  const toggleNotification = async (symbol) => {
    const current = notifications[symbol] !== false;
    const updated = { ...notifications, [symbol]: !current };
    setNotifications(updated);
    await fetch(`${API_URL}/api/settings`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ notifications: updated }) });
    toast.success(`${symbol.replace('USDT','')}: Alerts ${!current ? 'AN' : 'AUS'}`);
  };

  const toggleSignals = async (strategyId) => {
    const current = signalsEnabled[strategyId] !== false;
    const updated = { ...signalsEnabled, [strategyId]: !current };
    setSignalsEnabled(updated);
    await fetch(`${API_URL}/api/settings`, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify({ strategy_signals_enabled: updated }) });
    toast.success(`Signale ${!current ? 'AN' : 'AUS'}`);
  };

  const strategyMeta = strategies.find(s => s.id === selectedStrategy);
  const ruleState = ruleStates[selectedCoin]?.[selectedStrategy];
  const latestSignal = signals.find(s => s.symbol === selectedCoin && s.strategy_id === selectedStrategy);

  return (
    <ErrorBoundary onReset={() => window.location.reload()}>
    <div className="App">
      <Toaster position="top-right" theme="dark" richColors />
      <audio ref={audioRef} src="/alert.mp3" preload="auto" />

      <Header
        sessionActive={sessionActive}
        currentSession={currentSession}
        customSessions={customSessions}
        activeStrategy={strategyMeta}
        berlinTime={berlinTime}
        isAdmin={adminAuthed}
        onAdminClick={handleAdminClick}
        onSettingsClick={() => requireAdmin(() => setShowSettings(true))}
        controlState={controlState}
        onControlChanged={loadControlState}
        onRequireAdmin={() => requireAdmin(() => {})}
      />

      <div className="app-layout">
        <CoinSidebar
          selectedCoin={selectedCoin}
          onSelectCoin={setSelectedCoin}
          performance={performance}
          notifications={notifications}
          onToggleNotification={toggleNotification}
          ruleStates={ruleStates}
          selectedStrategy={selectedStrategy}
          autotradeCoins={autotradeCoins}
          onOpenAutoTrade={(sym) => setAutoTradeSymbol(sym)}
        />

        <div className="main-content">
          <StrategyTabs
            strategies={strategies}
            enabledIds={enabledIds}
            selected={selectedStrategy}
            signalsEnabled={signalsEnabled}
            onSelect={setSelectedStrategy}
            onToggleSignals={toggleSignals}
            onManage={() => setShowBuilder(true)}
            onEditParams={() => setShowSettings(true)}
          />
          <ErrorBoundary onReset={() => setSelectedCoin('BTCUSDT')}>
            <MainChart symbol={selectedCoin} candleData={candleData[selectedCoin]} />
          </ErrorBoundary>
          <SignalPanel
            symbol={selectedCoin}
            ruleState={ruleState}
            latestSignal={latestSignal}
            strategyMeta={strategyMeta}
          />
        </div>

        <div className="right-panel">
          <ErrorBoundary>
            <PerformanceAnalytics
              performance={performance}
              strategies={strategies}
              signals={signals}
              selectedCoin={selectedCoin}
              selectedStrategy={selectedStrategy}
              isAdmin={adminAuthed}
              onNeedAdmin={() => requireAdmin(() => {})}
              onCleared={() => { loadSignals(); loadPerformance(); }}
            />
          </ErrorBoundary>
        </div>
      </div>

      {showAlert && currentAlert && (
        <AlertModal signal={currentAlert} onClose={() => setShowAlert(false)} />
      )}
      {showSettings && (
        <SettingsPanel onClose={() => { setShowSettings(false); loadStrategies(); }} focusStrategy={selectedStrategy} />
      )}
      {showBuilder && (
        <StrategyBuilder
          strategies={strategies}
          enabledIds={enabledIds}
          onClose={() => setShowBuilder(false)}
          onChanged={loadStrategies}
        />
      )}
      {autoTradeSymbol && (
        <AutoTradeModal symbol={autoTradeSymbol} onClose={() => { setAutoTradeSymbol(null); loadAutotrade(); }} />
      )}
      {showLogin && (
        <AdminLogin
          onClose={() => setShowLogin(false)}
          onSuccess={() => setAdminAuthed(true)}
        />
      )}
    </div>
    </ErrorBoundary>
  );
}

export default App;
