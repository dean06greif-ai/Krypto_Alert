import React, { useState, useEffect, useRef } from 'react';
import './App.css';
import Header from './components/Header';
import CoinSidebar from './components/CoinSidebar';
import MainChart from './components/MainChart';
import SignalPanel from './components/SignalPanel';
import PerformanceAnalytics from './components/PerformanceAnalytics';
import AlertModal from './components/AlertModal';
import SettingsPanel from './components/SettingsPanel';
import { Toaster, toast } from 'sonner';

const API_URL = process.env.REACT_APP_BACKEND_URL;

function App() {
  const [selectedCoin, setSelectedCoin] = useState('BTCUSDT');
  const [signals, setSignals] = useState([]);
  const [performance, setPerformance] = useState([]);
  const [sessionActive, setSessionActive] = useState(false);
  const [currentSession, setCurrentSession] = useState('');
  const [customSessions, setCustomSessions] = useState([]);
  const [activeStrategy, setActiveStrategy] = useState(null);
  const [strategyParams, setStrategyParams] = useState({});
  const [showAlert, setShowAlert] = useState(false);
  const [currentAlert, setCurrentAlert] = useState(null);
  const [showSettings, setShowSettings] = useState(false);
  const [candleData, setCandleData] = useState({});
  const [notifications, setNotifications] = useState({});
  const wsRef = useRef(null);
  const audioRef = useRef(null);
  const notificationsRef = useRef({});

  // keep a ref in sync so the websocket handler always reads the latest map
  useEffect(() => {
    notificationsRef.current = notifications;
  }, [notifications]);

  // WebSocket connection
  useEffect(() => {
    const wsUrl = API_URL.replace('http', 'ws') + '/ws';
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;

    ws.onopen = () => {
      console.log('WebSocket connected');
      toast.success('Connected to scanner');
    };

    ws.onmessage = (event) => {
      const message = JSON.parse(event.data);

      if (message.type === 'signal') {
        const signal = message.data;
        setSignals(prev => [signal, ...prev]);

        // Respect per-instrument notification toggle (default = enabled)
        const notifyEnabled = notificationsRef.current[signal.symbol] !== false;
        if (!notifyEnabled) {
          return;
        }

        // Show alert
        setCurrentAlert(signal);
        setShowAlert(true);
        
        // Play sound
        playAlertSound();
        
        // Show toast notification
        toast.success(`${signal.type} Signal für ${signal.symbol}!`, {
          description: `Entry: $${signal.entry_price}`,
          duration: 5000
        });
        
        // Request browser notification
        if (Notification.permission === 'granted') {
          new Notification(`${signal.type} Signal!`, {
            body: `${signal.symbol} - Entry: $${signal.entry_price}`,
            icon: '/favicon.ico'
          });
        }
      } else if (message.type === 'candle') {
        setCandleData(prev => ({
          ...prev,
          [message.symbol]: message.data
        }));
      }
    };

    ws.onerror = (error) => {
      console.error('WebSocket error:', error);
      toast.error('Connection error');
    };

    ws.onclose = () => {
      console.log('WebSocket disconnected');
      toast.warning('Disconnected from scanner');
    };

    return () => {
      ws.close();
    };
  }, []);

  // Request notification permission
  useEffect(() => {
    if (Notification.permission === 'default') {
      Notification.requestPermission();
    }
  }, []);

  // Fetch strategies + settings for active strategy display
  useEffect(() => {
    const fetchStrategies = async () => {
      try {
        const response = await fetch(`${API_URL}/api/strategies`);
        const data = await response.json();
        const activeId = data.active;
        const strategy = data.strategies.find(s => s.id === activeId);
        setActiveStrategy(strategy);
        setStrategyParams(strategy?.current_params || {});
      } catch (error) {
        console.error('Error fetching strategies:', error);
      }
    };

    fetchStrategies();
    const interval = setInterval(fetchStrategies, 15000); // Update every 15s

    return () => clearInterval(interval);
  }, [showSettings]);

  // Fetch session status
  useEffect(() => {
    const fetchSessionStatus = async () => {
      try {
        const response = await fetch(`${API_URL}/api/session/status`);
        const data = await response.json();
        setSessionActive(data.is_active);
        setCurrentSession(data.current_session || '');
        setCustomSessions(data.custom_sessions || []);
      } catch (error) {
        console.error('Error fetching session status:', error);
      }
    };

    fetchSessionStatus();
    const interval = setInterval(fetchSessionStatus, 30000); // Update every 30s

    return () => clearInterval(interval);
  }, [showSettings]);

  // Fetch per-instrument notification settings
  useEffect(() => {
    const fetchNotifications = async () => {
      try {
        const response = await fetch(`${API_URL}/api/settings`);
        const data = await response.json();
        setNotifications(data.notifications || {});
      } catch (error) {
        console.error('Error fetching notification settings:', error);
      }
    };
    fetchNotifications();
  }, [showSettings]);

  const toggleNotification = async (symbol) => {
    const current = notifications[symbol] !== false; // default enabled
    const updated = { ...notifications, [symbol]: !current };
    setNotifications(updated);
    try {
      await fetch(`${API_URL}/api/settings`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ notifications: updated })
      });
      toast.success(`${symbol.replace('USDT', '')}: Alerts ${!current ? 'AN' : 'AUS'}`);
    } catch (error) {
      toast.error('Fehler beim Speichern');
    }
  };

  // Fetch signals
  useEffect(() => {
    const fetchSignals = async () => {
      try {
        const response = await fetch(`${API_URL}/api/signals?limit=20`);
        const data = await response.json();
        setSignals(data.signals || []);
      } catch (error) {
        console.error('Error fetching signals:', error);
      }
    };

    fetchSignals();
  }, []);

  // Fetch performance
  useEffect(() => {
    const fetchPerformance = async () => {
      try {
        const response = await fetch(`${API_URL}/api/performance`);
        const data = await response.json();
        setPerformance(data.performance || []);
      } catch (error) {
        console.error('Error fetching performance:', error);
      }
    };

    fetchPerformance();
    const interval = setInterval(fetchPerformance, 300000); // Update every 5 minutes

    return () => clearInterval(interval);
  }, []);

  const playAlertSound = () => {
    if (audioRef.current) {
      audioRef.current.play().catch(e => console.error('Error playing sound:', e));
    }
  };

  return (
    <div className="App">
      <Toaster position="top-right" theme="dark" richColors />
      <audio ref={audioRef} src="/alert.mp3" preload="auto" />
      
      <Header 
        sessionActive={sessionActive}
        currentSession={currentSession}
        customSessions={customSessions}
        activeStrategy={activeStrategy}
        onSettingsClick={() => setShowSettings(true)}
      />
      
      <div className="app-layout">
        <CoinSidebar 
          selectedCoin={selectedCoin}
          onSelectCoin={setSelectedCoin}
          performance={performance}
          notifications={notifications}
          onToggleNotification={toggleNotification}
        />
        
        <div className="main-content">
          <MainChart 
            symbol={selectedCoin}
            candleData={candleData[selectedCoin]}
          />
          
          <SignalPanel 
            symbol={selectedCoin}
            signals={signals.filter(s => s.symbol === selectedCoin)}
            activeStrategy={activeStrategy}
            strategyParams={strategyParams}
          />
        </div>
        
        <div className="right-panel">
          <PerformanceAnalytics 
            performance={performance}
            signals={signals}
            selectedCoin={selectedCoin}
          />
        </div>
      </div>
      
      {showAlert && currentAlert && (
        <AlertModal 
          signal={currentAlert}
          onClose={() => setShowAlert(false)}
        />
      )}
      
      {showSettings && (
        <SettingsPanel 
          onClose={() => setShowSettings(false)}
        />
      )}
    </div>
  );
}

export default App;
