import React, { useState, useEffect } from 'react';
import { X, Plus, Trash, FloppyDisk } from '@phosphor-icons/react';
import { toast } from 'sonner';
import './StrategyBuilder.css';

const API_URL = process.env.REACT_APP_BACKEND_URL;

const OP_LABELS = {
  '<': 'kleiner <', '>': 'größer >', '<=': '≤', '>=': '≥',
  cross_above: 'kreuzt über', cross_below: 'kreuzt unter',
};
const IND_LABELS = {
  rsi: 'RSI', ema_fast: 'EMA Fast', ema_slow: 'EMA Slow', price: 'Preis',
  ha_color: 'HA Farbe (1=grün)', ema_gap_pct: 'EMA Abstand %',
};

const emptyRule = () => ({ indicator: 'rsi', op: '<', valueType: 'number', value: 30, label: '' });

const StrategyBuilder = ({ strategies, enabledIds, onClose, onChanged }) => {
  const [options, setOptions] = useState({ indicators: [], operators: [] });
  const [enabled, setEnabled] = useState(enabledIds);
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [emaFast, setEmaFast] = useState(9);
  const [emaSlow, setEmaSlow] = useState(50);
  const [rsiP, setRsiP] = useState(14);
  const [longRules, setLongRules] = useState([emptyRule()]);
  const [shortRules, setShortRules] = useState([{ ...emptyRule(), op: '>', value: 70 }]);
  const [slMode, setSlMode] = useState('structure');
  const [slPercent, setSlPercent] = useState(1.5);
  const [slTicks, setSlTicks] = useState(4);
  const [crv, setCrv] = useState(2);

  useEffect(() => {
    fetch(`${API_URL}/api/strategies/builder-options`).then(r => r.json()).then(setOptions);
  }, []);

  const toggleTab = async (id) => {
    const next = enabled.includes(id) ? enabled.filter(x => x !== id) : [...enabled, id];
    setEnabled(next);
    await fetch(`${API_URL}/api/settings`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled_strategies: next }),
    });
    onChanged && onChanged();
  };

  const updRule = (list, setList, i, field, val) => {
    const copy = [...list];
    copy[i] = { ...copy[i], [field]: val };
    setList(copy);
  };

  const serializeRules = (list) => list.map(r => ({
    indicator: r.indicator, op: r.op,
    value: r.valueType === 'indicator' ? r.value : parseFloat(r.value),
    label: r.label || `${IND_LABELS[r.indicator]} ${OP_LABELS[r.op]} ${r.value}`,
  }));

  const create = async () => {
    if (!name.trim()) { toast.error('Name fehlt'); return; }
    if (!longRules.length && !shortRules.length) { toast.error('Mind. eine Regel'); return; }
    const def = {
      name, description,
      indicators: { ema_fast_period: emaFast, ema_slow_period: emaSlow, rsi_period: rsiP },
      long_rules: serializeRules(longRules),
      short_rules: serializeRules(shortRules),
      sl_mode: slMode, sl_percent: slPercent, sl_ticks: slTicks, structure_lookback: 10, crv_target: crv,
    };
    const res = await fetch(`${API_URL}/api/strategies/custom`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(def),
    });
    if (res.ok) {
      toast.success(`Strategie "${name}" erstellt`);
      setName(''); setDescription(''); setLongRules([emptyRule()]);
      onChanged && onChanged();
    } else toast.error('Fehler beim Erstellen');
  };

  const deleteCustom = async (id) => {
    await fetch(`${API_URL}/api/strategies/custom/${id}`, { method: 'DELETE' });
    toast.success('Strategie gelöscht');
    onChanged && onChanged();
  };

  const RuleEditor = ({ list, setList, color }) => (
    <div className="sb-rules">
      {list.map((r, i) => (
        <div className="sb-rule" key={i} data-testid={`rule-row-${color}-${i}`}>
          <select value={r.indicator} onChange={e => updRule(list, setList, i, 'indicator', e.target.value)}>
            {(options.indicators || []).map(ind => <option key={ind} value={ind}>{IND_LABELS[ind] || ind}</option>)}
          </select>
          <select value={r.op} onChange={e => updRule(list, setList, i, 'op', e.target.value)}>
            {(options.operators || []).map(op => <option key={op} value={op}>{OP_LABELS[op] || op}</option>)}
          </select>
          <select value={r.valueType} onChange={e => updRule(list, setList, i, 'valueType', e.target.value)}>
            <option value="number">Zahl</option>
            <option value="indicator">Indikator</option>
          </select>
          {r.valueType === 'indicator' ? (
            <select value={r.value} onChange={e => updRule(list, setList, i, 'value', e.target.value)}>
              {(options.indicators || []).map(ind => <option key={ind} value={ind}>{IND_LABELS[ind] || ind}</option>)}
            </select>
          ) : (
            <input type="number" value={r.value} onChange={e => updRule(list, setList, i, 'value', e.target.value)} />
          )}
          <button className="sb-rule-del" onClick={() => setList(list.filter((_, x) => x !== i))}><Trash size={14} /></button>
        </div>
      ))}
      <button className="sb-add-rule" style={{ color }} onClick={() => setList([...list, color === 'long' ? emptyRule() : { ...emptyRule(), op: '>', value: 70 }])} data-testid={`add-rule-${color}`}>
        <Plus size={13} weight="bold" /> Regel
      </button>
    </div>
  );

  const customs = strategies.filter(s => s.is_custom);

  return (
    <div className="sb-overlay" onClick={onClose}>
      <div className="sb-panel" onClick={e => e.stopPropagation()} data-testid="strategy-builder">
        <div className="sb-header">
          <h2>STRATEGIEN VERWALTEN</h2>
          <button className="sb-close" onClick={onClose} data-testid="builder-close"><X size={22} weight="bold" /></button>
        </div>

        <div className="sb-content">
          <div className="sb-section">
            <h3>Reiter im Dashboard</h3>
            <div className="sb-tab-toggles">
              {strategies.map(s => (
                <label key={s.id} className={`sb-tab-toggle ${enabled.includes(s.id) ? 'on' : ''}`} data-testid={`tab-toggle-${s.id}`}>
                  <input type="checkbox" checked={enabled.includes(s.id)} onChange={() => toggleTab(s.id)} />
                  <span>{s.name}</span>
                  {s.is_custom && <span className="sb-badge">CUSTOM</span>}
                </label>
              ))}
            </div>
          </div>

          <div className="sb-section">
            <h3>Neue Custom-Strategie erstellen</h3>
            <div className="sb-form-row">
              <input className="sb-input" placeholder="Name" value={name} onChange={e => setName(e.target.value)} data-testid="custom-name" />
              <input className="sb-input" placeholder="Beschreibung" value={description} onChange={e => setDescription(e.target.value)} data-testid="custom-desc" />
            </div>
            <div className="sb-form-row indicators">
              <label>EMA Fast<input type="number" value={emaFast} onChange={e => setEmaFast(parseInt(e.target.value))} /></label>
              <label>EMA Slow<input type="number" value={emaSlow} onChange={e => setEmaSlow(parseInt(e.target.value))} /></label>
              <label>RSI Periode<input type="number" value={rsiP} onChange={e => setRsiP(parseInt(e.target.value))} /></label>
            </div>

            <div className="sb-rule-group">
              <div className="sb-rule-label long">LONG Regeln (alle müssen zutreffen)</div>
              <RuleEditor list={longRules} setList={setLongRules} color="long" />
            </div>
            <div className="sb-rule-group">
              <div className="sb-rule-label short">SHORT Regeln (alle müssen zutreffen)</div>
              <RuleEditor list={shortRules} setList={setShortRules} color="short" />
            </div>

            <div className="sb-form-row">
              <label className="sb-sl">Stop Loss
                <select value={slMode} onChange={e => setSlMode(e.target.value)}>
                  <option value="structure">Struktur</option>
                  <option value="percent">Fest %</option>
                </select>
              </label>
              {slMode === 'percent'
                ? <label className="sb-sl">SL %<input type="number" step={0.1} value={slPercent} onChange={e => setSlPercent(parseFloat(e.target.value))} /></label>
                : <label className="sb-sl">SL Ticks<input type="number" value={slTicks} onChange={e => setSlTicks(parseInt(e.target.value))} /></label>}
              <label className="sb-sl">CRV Ziel<input type="number" step={0.1} value={crv} onChange={e => setCrv(parseFloat(e.target.value))} /></label>
            </div>

            <button className="sb-create" onClick={create} data-testid="create-strategy-btn">
              <FloppyDisk size={16} weight="bold" /> Strategie erstellen
            </button>
          </div>

          {customs.length > 0 && (
            <div className="sb-section">
              <h3>Meine Custom-Strategien</h3>
              {customs.map(s => (
                <div key={s.id} className="sb-custom-item" data-testid={`custom-item-${s.id}`}>
                  <div><b>{s.name}</b><span className="sb-custom-desc">{s.description}</span></div>
                  <button className="sb-del" onClick={() => deleteCustom(s.id)} data-testid={`delete-custom-${s.id}`}><Trash size={15} /></button>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};

export default StrategyBuilder;
