"""
Base class for all trading strategies.
Add new strategies by inheriting from BaseStrategy.
"""
from abc import ABC, abstractmethod
from typing import Dict, List, Optional
from datetime import datetime, timezone
from services.technical_indicators import TechnicalIndicators


class BaseStrategy(ABC):
    """Base class for all trading strategies"""
    
    # Strategy metadata (override in subclass)
    STRATEGY_ID = "base"
    STRATEGY_NAME = "Base Strategy"
    STRATEGY_DESCRIPTION = "Override this in subclass"
    STRATEGY_TIMEFRAME = "1m"
    
    def __init__(self):
        self.indicators = TechnicalIndicators()
    
    @abstractmethod
    def check_signal(self, candles: List[Dict], symbol: str, settings: Dict) -> Optional[Dict]:
        """
        Check for signal on given candles.
        
        Args:
            candles: List of OHLC candles (oldest to newest)
            symbol: Trading pair (e.g., 'BTCUSDT')
            settings: User settings dict
        
        Returns:
            Signal dict if found, None otherwise.
            Signal dict must contain:
            - type: 'LONG' or 'SHORT'
            - signal_class: 'SIGNAL' or 'PRE_SIGNAL'
            - entry_price, stop_loss, take_profit_1, take_profit_full, crv
            - rules_met_count
            - indicators dict
        """
        pass
    
    def get_metadata(self) -> Dict:
        """Return strategy metadata"""
        return {
            "id": self.STRATEGY_ID,
            "name": self.STRATEGY_NAME,
            "description": self.STRATEGY_DESCRIPTION,
            "timeframe": self.STRATEGY_TIMEFRAME,
        }
