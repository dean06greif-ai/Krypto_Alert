import numpy as np
import pandas as pd
from typing import List, Dict, Optional

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

    # ------------------------------------------------------------------
    # NEW: Volatility / Volume / Smart-Money indicators
    # ------------------------------------------------------------------
    @staticmethod
    def calculate_atr(candles: List[Dict], period: int = 14) -> List[float]:
        """Average True Range (Wilder) - volatility measure for dynamic stops."""
        n = len(candles)
        if n < period + 1:
            return [None] * n
        trs = [0.0]
        for i in range(1, n):
            h = candles[i]['high']
            l = candles[i]['low']
            pc = candles[i - 1]['close']
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        atr = [None] * n
        atr[period] = float(np.mean(trs[1:period + 1]))
        for i in range(period + 1, n):
            atr[i] = (atr[i - 1] * (period - 1) + trs[i]) / period
        return atr

    @staticmethod
    def calculate_vwap(candles: List[Dict]) -> List[float]:
        """Volume Weighted Average Price (cumulative over buffer) - institutional fair value."""
        out = []
        cum_pv = 0.0
        cum_v = 0.0
        for c in candles:
            tp = (c['high'] + c['low'] + c['close']) / 3
            v = c.get('volume', 0) or 0
            cum_pv += tp * v
            cum_v += v
            out.append(cum_pv / cum_v if cum_v > 0 else tp)
        return out

    @staticmethod
    def volume_sma(candles: List[Dict], period: int = 20) -> Optional[float]:
        """Average volume over the last `period` candles."""
        vols = [c.get('volume', 0) or 0 for c in candles]
        if len(vols) < period:
            return None
        return float(np.mean(vols[-period:]))

    @staticmethod
    def relative_volume(candles: List[Dict], period: int = 20) -> Optional[float]:
        """Current volume vs. its moving average (>1 = above-average participation)."""
        avg = TechnicalIndicators.volume_sma(candles, period)
        if not avg or avg <= 0:
            return None
        return (candles[-1].get('volume', 0) or 0) / avg

    @staticmethod
    def find_swings(candles: List[Dict], left: int = 2, right: int = 2):
        """Return fractal swing highs/lows as (index, price) - market structure points."""
        highs, lows = [], []
        n = len(candles)
        for i in range(left, n - right):
            window = candles[i - left:i + right + 1]
            hi = candles[i]['high']
            lo = candles[i]['low']
            if hi >= max(c['high'] for c in window):
                highs.append((i, hi))
            if lo <= min(c['low'] for c in window):
                lows.append((i, lo))
        return highs, lows

    @staticmethod
    def liquidity_sweep(candles: List[Dict], left: int = 2, right: int = 2):
        """
        Smart-Money liquidity grab: the last CLOSED candle spikes beyond a recent
        swing level (taking stops) then closes back inside -> reversal fuel.
        Returns 'bullish' | 'bearish' | None.
        """
        if len(candles) < left + right + 5:
            return None
        prior = candles[:-1]
        highs, lows = TechnicalIndicators.find_swings(prior, left, right)
        last = candles[-1]
        recent_low = min((l for _, l in lows[-3:]), default=None)
        recent_high = max((h for _, h in highs[-3:]), default=None)
        if recent_low is not None and last['low'] < recent_low and last['close'] > recent_low:
            return 'bullish'
        if recent_high is not None and last['high'] > recent_high and last['close'] < recent_high:
            return 'bearish'
        return None

    @staticmethod
    def range_position(candles: List[Dict], lookback: int = 20) -> float:
        """
        Where price sits inside the recent range: 0=range low (discount),
        1=range high (premium). Smart money buys discount / sells premium.
        """
        seg = candles[-lookback:]
        if not seg:
            return 0.5
        hi = max(c['high'] for c in seg)
        lo = min(c['low'] for c in seg)
        if hi <= lo:
            return 0.5
        return (candles[-1]['close'] - lo) / (hi - lo)
    
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
