import numpy as np
import pandas as pd
from typing import List, Dict
import talib

class TechnicalIndicators:
    """Calculate technical indicators for trading strategy"""
    
    @staticmethod
    def calculate_heikin_ashi(candles: List[Dict]) -> List[Dict]:
        """
        Convert regular candles to Heikin Ashi candles
        Args:
            candles: List of OHLC candles
        Returns:
            List of Heikin Ashi candles
        """
        if len(candles) < 2:
            return candles
            
        ha_candles = []
        
        for i, candle in enumerate(candles):
            if i == 0:
                # First candle
                ha_close = (candle['open'] + candle['high'] + candle['low'] + candle['close']) / 4
                ha_open = (candle['open'] + candle['close']) / 2
                ha_high = candle['high']
                ha_low = candle['low']
            else:
                # Subsequent candles
                ha_close = (candle['open'] + candle['high'] + candle['low'] + candle['close']) / 4
                ha_open = (ha_candles[i-1]['open'] + ha_candles[i-1]['close']) / 2
                ha_high = max(candle['high'], ha_open, ha_close)
                ha_low = min(candle['low'], ha_open, ha_close)
            
            ha_candles.append({
                'timestamp': candle['timestamp'],
                'open': ha_open,
                'high': ha_high,
                'low': ha_low,
                'close': ha_close,
                'volume': candle['volume'],
                'is_green': ha_close > ha_open
            })
        
        return ha_candles
    
    @staticmethod
    def calculate_ema(prices: List[float], period: int) -> List[float]:
        """
        Calculate Exponential Moving Average
        Args:
            prices: List of closing prices
            period: EMA period (9 or 50)
        Returns:
            List of EMA values
        """
        if len(prices) < period:
            return [None] * len(prices)
        
        prices_array = np.array(prices)
        ema = talib.EMA(prices_array, timeperiod=period)
        return ema.tolist()
    
    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 14) -> List[float]:
        """
        Calculate Relative Strength Index
        Args:
            prices: List of closing prices
            period: RSI period (default 14)
        Returns:
            List of RSI values
        """
        if len(prices) < period:
            return [None] * len(prices)
        
        prices_array = np.array(prices)
        rsi = talib.RSI(prices_array, timeperiod=period)
        return rsi.tolist()
    
    @staticmethod
    def get_recent_low(candles: List[Dict], lookback: int = 10) -> float:
        """
        Get the most recent low from candles
        Args:
            candles: List of candles
            lookback: Number of candles to look back
        Returns:
            Recent low price
        """
        if not candles:
            return None
        
        recent_candles = candles[-lookback:]
        return min(c['low'] for c in recent_candles)
    
    @staticmethod
    def get_recent_high(candles: List[Dict], lookback: int = 10) -> float:
        """
        Get the most recent high from candles
        Args:
            candles: List of candles
            lookback: Number of candles to look back
        Returns:
            Recent high price
        """
        if not candles:
            return None
        
        recent_candles = candles[-lookback:]
        return max(c['high'] for c in recent_candles)
    
    @staticmethod
    def calculate_crv(entry: float, stop_loss: float, take_profit: float) -> float:
        """
        Calculate Cost-to-Reward Ratio
        Args:
            entry: Entry price
            stop_loss: Stop loss price
            take_profit: Take profit price
        Returns:
            CRV ratio
        """
        if entry == stop_loss:
            return 0
        
        risk = abs(entry - stop_loss)
        reward = abs(take_profit - entry)
        
        if risk == 0:
            return 0
        
        return reward / risk
