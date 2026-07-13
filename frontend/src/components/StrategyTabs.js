import React from 'react';
import { Plus, Gear, Lightning } from '@phosphor-icons/react';
import './StrategyTabs.css';

const StrategyTabs = ({ strategies, enabledIds, selected, signalsEnabled, onSelect, onToggleSignals, onManage, onEditParams }) => {
  const tabs = enabledIds
    .map(id => strategies.find(s => s.id === id))
    .filter(Boolean);

  return (
    <div className="strategy-tabs-bar" data-testid="strategy-tabs">
      <div className="strategy-tabs-scroll">
        {tabs.map(strat => {
          const isActive = selected === strat.id;
          const sigOn = signalsEnabled[strat.id] !== false;
          return (
            <div
              key={strat.id}
              className={`strategy-tab ${isActive ? 'strategy-tab-active' : ''}`}
              onClick={() => onSelect(strat.id)}
              data-testid={`strategy-tab-${strat.id}`}
            >
              <span className="strategy-tab-name">{strat.name}</span>
              {strat.is_custom && <span className="strategy-tab-custom">C</span>}
              <button
                className={`strategy-tab-signal ${sigOn ? 'on' : 'off'}`}
                onClick={(e) => { e.stopPropagation(); onToggleSignals(strat.id); }}
                title={sigOn ? 'Signale AN (klick zum Ausschalten)' : 'Signale AUS'}
                data-testid={`strategy-signal-toggle-${strat.id}`}
              >
                <Lightning size={13} weight={sigOn ? 'fill' : 'regular'} />
              </button>
            </div>
          );
        })}
      </div>
      <div className="strategy-tabs-actions">
        <button className="strategy-tab-action" onClick={onEditParams} title="Parameter der aktiven Strategie" data-testid="edit-strategy-params-btn">
          <Gear size={16} weight="bold" />
        </button>
        <button className="strategy-tab-action add" onClick={onManage} title="Strategien verwalten / neue erstellen" data-testid="manage-strategies-btn">
          <Plus size={16} weight="bold" />
        </button>
      </div>
    </div>
  );
};

export default StrategyTabs;
