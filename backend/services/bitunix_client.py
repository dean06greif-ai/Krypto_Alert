import asyncio
import json
import websockets
from typing import Callable, Dict, List
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

class BitunixWebSocketClient:
    """WebSocket client for Bitunix exchange"""
    
    def __init__(self):
        self.ws_url = "wss://fapi.bitunix.com/public/"
        self.connections = {}
        self.callbacks = {}
        self.running = False
    
    async def connect_symbol(self, symbol: str, callback: Callable):
        """
        Connect to a symbol's 1-minute kline stream
        Args:
            symbol: Trading pair (e.g., 'BTCUSDT')
            callback: Function to call with new candle data
        """
        self.callbacks[symbol] = callback
        
        try:
            async with websockets.connect(self.ws_url) as websocket:
                self.connections[symbol] = websocket
                
                # Subscribe to 1-minute kline
                subscribe_message = {
                    "op": "subscribe",
                    "args": [
                        {
                            "ch": "market_kline_1min",
                            "symbol": symbol
                        }
                    ]
                }
                
                await websocket.send(json.dumps(subscribe_message))
                logger.info(f"Subscribed to {symbol} 1min kline")
                
                # Listen for messages
                while self.running:
                    try:
                        message = await asyncio.wait_for(websocket.recv(), timeout=30.0)
                        data = json.loads(message)
                        
                        # Process kline data
                        if 'data' in data and 'k' in data['data']:
                            kline = data['data']['k']
                            candle = {
                                'timestamp': int(kline['t']),
                                'open': float(kline['o']),
                                'high': float(kline['h']),
                                'low': float(kline['l']),
                                'close': float(kline['c']),
                                'volume': float(kline['v'])
                            }
                            
                            await callback(symbol, candle)
                    
                    except asyncio.TimeoutError:
                        # Send ping to keep connection alive
                        await websocket.send(json.dumps({"op": "ping"}))
                    except Exception as e:
                        logger.error(f"Error processing message for {symbol}: {e}")
                        break
        
        except Exception as e:
            logger.error(f"WebSocket connection error for {symbol}: {e}")
            # Attempt reconnection after delay
            await asyncio.sleep(5)
            if self.running:
                await self.connect_symbol(symbol, callback)
    
    async def start(self, symbols: List[str], callback: Callable):
        """
        Start WebSocket connections for multiple symbols
        Args:
            symbols: List of trading pairs
            callback: Callback function for candle updates
        """
        self.running = True
        tasks = []
        
        for symbol in symbols:
            task = asyncio.create_task(self.connect_symbol(symbol, callback))
            tasks.append(task)
        
        await asyncio.gather(*tasks, return_exceptions=True)
    
    async def stop(self):
        """Stop all WebSocket connections"""
        self.running = False
        
        for symbol, ws in self.connections.items():
            try:
                await ws.close()
                logger.info(f"Closed connection for {symbol}")
            except Exception as e:
                logger.error(f"Error closing connection for {symbol}: {e}")
        
        self.connections.clear()
        self.callbacks.clear()
