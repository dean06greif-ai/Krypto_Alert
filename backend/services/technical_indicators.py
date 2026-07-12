import numpy as np
import pandas as pd
from typing import List, Dict

class TechnicalIndicators:
    """Calculate technical indicators for trading strategy - Pure Python/Numpy (no TA-Lib)"""
    
    @staticmethod
    def calculate_heikin_ashi(candles: List[Dict]) -> List[Dict]:
        """
        Convert regular candles to Heikin Ashi candles
        """
        if len(candles) < 2:
            return candles
            
        ha_candles = []
        
        for i, candle in enumerate(candles):
            if i == 0:
                ha_close = (candle['open'] + candle['high'] + candle['low'] + candle['close']) / 4
                ha_open = (candle['open'] + candle['close']) / 2
                ha_high = candle['high']
                ha_low = candle['low']
            else:
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
        Calculate Exponential Moving Average using pure numpy
        """
        if len(prices) < period:
            return [None] * len(prices)
        
        prices_array = np.array(prices, dtype=float)
        ema = np.full(len(prices_array), np.nan)
        
        # SMA for first EMA value
        ema[period - 1] = np.mean(prices_array[:period])
        
        # Multiplier
        multiplier = 2 / (period + 1)
        
        # Calculate EMA
        for i in range(period, len(prices_array)):
            ema[i] = (prices_array[i] - ema[i-1]) * multiplier + ema[i-1]
        
        # Convert NaN to None
        return [None if np.isnan(x) else float(x) for x in ema]
    
    @staticmethod
    def calculate_rsi(prices: List[float], period: int = 14) -> List[float]:
        """
        Calculate Relative Strength Index using pure numpy
        """
        if len(prices) < period + 1:
            return [None] * len(prices)
        
        prices_array = np.array(prices, dtype=float)
        deltas = np.diff(prices_array)
        
        rsi = np.full(len(prices_array), np.nan)
        
        # Initial averages
        gains = np.where(deltas > 0, deltas, 0)
        losses = np.where(deltas < 0, -deltas, 0)
        
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])
        
        if avg_loss == 0:
            rsi[period] = 100
        else:
            rs = avg_gain / avg_loss
            rsi[period] = 100 - (100 / (1 + rs))
        
        # Wilder's smoothing
        for i in range(period + 1, len(prices_array)):
            avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
            
            if avg_loss == 0:
                rsi[i] = 100
            else:
                rs = avg_gain / avg_loss
                rsi[i] = 100 - (100 / (1 + rs))
        
        return [None if np.isnan(x) else float(x) for x in rsi]
    
    @staticmethod
    def get_recent_low(candles: List[Dict], lookback: int = 10) -> float:
        """Get the most recent low from candles"""
        if not candles:
            return None
        recent_candles = candles[-lookback:]
        return min(c['low'] for c in recent_candles)
    
    @staticmethod
    def get_recent_high(candles: List[Dict], lookback: int = 10) -> float:
        """Get the most recent high from candles"""
        if not candles:
            return None
        recent_candles = candles[-lookback:]
        return max(c['high'] for c in recent_candles)
    
    @staticmethod
    def calculate_crv(entry: float, stop_loss: float, take_profit: float) -> float:
        """Calculate Cost-to-Reward Ratio"""
        if entry == stop_loss:
            return 0
        
        risk = abs(entry - stop_loss)
        reward = abs(take_profit - entry)
        
        if risk == 0:
            return 0
        
        return reward / risk
