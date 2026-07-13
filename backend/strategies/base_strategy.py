"""
Base class for all trading strategies.
Add new strategies by inheriting from BaseStrategy.
Each strategy has configurable parameters.
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from services.technical_indicators import TechnicalIndicators


class BaseStrategy(ABC):
    """Base class for all trading strategies"""
    
    STRATEGY_ID = "base"
    STRATEGY_NAME = "Base Strategy"
    STRATEGY_DESCRIPTION = "Override this in subclass"
    STRATEGY_TIMEFRAME = "1m"
    
    # Parameters that can be adjusted by user
    # Format: {param_key: {"value": default, "min": x, "max": y, "step": z, "label": "...", "description": "..."}}
    DEFAULT_PARAMS = {}
    
    def __init__(self):
        self.indicators = TechnicalIndicators()
    
    @abstractmethod
    def check_signal(self, candles: List[Dict], symbol: str, settings: Dict) -> Optional[Dict]:
        """Check for signal - implement in subclass"""
        pass
    
    def get_params(self, settings: Dict) -> Dict:
        """Get parameter values (user override or defaults)"""
        user_params = settings.get("strategy_params", {}).get(self.STRATEGY_ID, {})
        result = {}
        for key, meta in self.DEFAULT_PARAMS.items():
            result[key] = user_params.get(key, meta["value"])
        return result
    
    def get_metadata(self) -> Dict:
        """Return strategy metadata including default parameters"""
        return {
            "id": self.STRATEGY_ID,
            "name": self.STRATEGY_NAME,
            "description": self.STRATEGY_DESCRIPTION,
            "timeframe": self.STRATEGY_TIMEFRAME,
            "params": self.DEFAULT_PARAMS,
        }
