import React, { useEffect, useRef } from 'react';
import { createChart, CandlestickSeries, LineSeries } from 'lightweight-charts';
import './MainChart.css';

const MainChart = ({ symbol, candleData }) => {
  const chartContainerRef = useRef(null);
  const chartRef = useRef(null);
  const candlestickSeriesRef = useRef(null);
  const ema9SeriesRef = useRef(null);
  const ema50SeriesRef = useRef(null);

  useEffect(() => {
    if (!chartContainerRef.current) return;

    // Create chart
    const chart = createChart(chartContainerRef.current, {
      layout: {
        background: { color: '#121212' },
        textColor: '#A1A4B0',
      },
      grid: {
        vertLines: { color: '#2A2D3A' },
        horzLines: { color: '#2A2D3A' },
      },
      timeScale: {
        timeVisible: true,
        secondsVisible: true,
        borderColor: '#2A2D3A',
      },
      rightPriceScale: {
        borderColor: '#2A2D3A',
      },
      crosshair: {
        mode: 1,
        vertLine: {
          color: '#5C6070',
          width: 1,
          style: 2,
        },
        horzLine: {
          color: '#5C6070',
          width: 1,
          style: 2,
        },
      },
    });

    chartRef.current = chart;

    // Add candlestick series (Heikin Ashi will be calculated server-side)
    const candlestickSeries = chart.addSeries(CandlestickSeries, {
      upColor: '#00FF66',
      downColor: '#FF3366',
      borderUpColor: '#00FF66',
      borderDownColor: '#FF3366',
      wickUpColor: '#00FF66',
      wickDownColor: '#FF3366',
    });

    candlestickSeriesRef.current = candlestickSeries;

    // Add EMA 9 line
    const ema9Series = chart.addSeries(LineSeries, {
      color: '#FFD700',
      lineWidth: 2,
      title: 'EMA 9',
    });

    ema9SeriesRef.current = ema9Series;

    // Add EMA 50 line
    const ema50Series = chart.addSeries(LineSeries, {
      color: '#00A8FF',
      lineWidth: 2,
      title: 'EMA 50',
    });

    ema50SeriesRef.current = ema50Series;

    // Fit content
    chart.timeScale().fitContent();

    // Handle resize
    const handleResize = () => {
      if (chartContainerRef.current) {
        chart.applyOptions({
          width: chartContainerRef.current.clientWidth,
          height: chartContainerRef.current.clientHeight,
        });
      }
    };

    window.addEventListener('resize', handleResize);

    return () => {
      window.removeEventListener('resize', handleResize);
      chart.remove();
    };
  }, []);

  // Update data when symbol changes
  useEffect(() => {
    // In production, fetch historical data for the symbol
    // For now, we'll rely on WebSocket updates
  }, [symbol]);

  // Update with new candle data
  useEffect(() => {
    if (candleData && candlestickSeriesRef.current) {
      const candle = {
        time: Math.floor(candleData.timestamp / 1000),
        open: candleData.open,
        high: candleData.high,
        low: candleData.low,
        close: candleData.close,
      };

      candlestickSeriesRef.current.update(candle);
    }
  }, [candleData]);

  return (
    <div className="main-chart" data-testid="main-chart">
      <div className="chart-header">
        <div className="chart-title">
          <span className="mono">{symbol}</span>
          <span className="chart-subtitle">1MIN HEIKIN ASHI</span>
        </div>
        <div className="chart-indicators">
          <div className="indicator-label">
            <div className="indicator-dot" style={{ background: '#FFD700' }}></div>
            <span>EMA 9</span>
          </div>
          <div className="indicator-label">
            <div className="indicator-dot" style={{ background: '#00A8FF' }}></div>
            <span>EMA 50</span>
          </div>
        </div>
      </div>
      <div ref={chartContainerRef} className="chart-container" />
    </div>
  );
};

export default MainChart;
