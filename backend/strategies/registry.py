"""
Strategy Registry - Manages all available trading strategies.
"""
from typing import Dict, List, Optional
from strategies.scalping_strategy import ScalpingStrategy
from strategies.rsi_only_strategy import RSIOnlyStrategy
from strategies.base_strategy import BaseStrategy


class StrategyRegistry:
    """Registry for all available strategies"""
    
    def __init__(self):
        # Register all available strategies here
        self._strategies: Dict[str, BaseStrategy] = {}
        self._register_defaults()
    
    def _register_defaults(self):
        """Register default strategies"""
        self.register(ScalpingStrategy())
        self.register(RSIOnlyStrategy())
    
    def register(self, strategy: BaseStrategy):
        """Register a new strategy"""
        self._strategies[strategy.STRATEGY_ID] = strategy
    
    def get(self, strategy_id: str) -> Optional[BaseStrategy]:
        """Get a strategy by ID"""
        return self._strategies.get(strategy_id)
    
    def list_all(self) -> List[Dict]:
        """List all available strategies with metadata"""
        return [s.get_metadata() for s in self._strategies.values()]
    
    def get_default(self) -> BaseStrategy:
        """Get the default (scalping) strategy"""
        return self._strategies["scalping_4_rules"]


# Global registry instance
registry = StrategyRegistry()
