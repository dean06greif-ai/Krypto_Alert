"""
Strategy Registry - built-in + dynamically loaded custom strategies.
"""
from typing import Dict, List, Optional
from strategies.scalping_strategy import ScalpingStrategy
from strategies.rsi_only_strategy import RSIOnlyStrategy
from strategies.custom_strategy import CustomStrategy
from strategies.base_strategy import BaseStrategy


class StrategyRegistry:
    def __init__(self):
        self._strategies: Dict[str, BaseStrategy] = {}
        self._custom_ids = set()
        self._register_defaults()

    def _register_defaults(self):
        self.register(ScalpingStrategy())
        self.register(RSIOnlyStrategy())

    def register(self, strategy: BaseStrategy):
        self._strategies[strategy.STRATEGY_ID] = strategy

    def get(self, strategy_id: str) -> Optional[BaseStrategy]:
        return self._strategies.get(strategy_id)

    def list_all(self) -> List[Dict]:
        return [s.get_metadata() for s in self._strategies.values()]

    def list_ids(self) -> List[str]:
        return list(self._strategies.keys())

    def get_default(self) -> BaseStrategy:
        return self._strategies["scalping_4_rules"]

    # ---- custom strategies ----
    def load_custom(self, definitions: List[Dict]):
        # remove existing custom
        for cid in list(self._custom_ids):
            self._strategies.pop(cid, None)
        self._custom_ids.clear()
        for d in definitions:
            strat = CustomStrategy(d)
            self.register(strat)
            self._custom_ids.add(strat.STRATEGY_ID)

    def upsert_custom(self, definition: Dict):
        strat = CustomStrategy(definition)
        self.register(strat)
        self._custom_ids.add(strat.STRATEGY_ID)

    def remove_custom(self, strategy_id: str):
        if strategy_id in self._custom_ids:
            self._strategies.pop(strategy_id, None)
            self._custom_ids.discard(strategy_id)


registry = StrategyRegistry()
