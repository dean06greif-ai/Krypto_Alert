import os
from telegram import Bot
from telegram.constants import ParseMode
import logging
from typing import Dict

logger = logging.getLogger(__name__)

class TelegramNotifier:
    """Send trading signals via Telegram"""
    
    def __init__(self):
        self.bot_token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.chat_id = os.getenv('TELEGRAM_CHAT_ID')
        self.bot = None
        
        if self.bot_token:
            try:
                self.bot = Bot(token=self.bot_token)
                logger.info("Telegram bot initialized")
            except Exception as e:
                logger.error(f"Failed to initialize Telegram bot: {e}")
    
    def format_signal_message(self, signal: Dict) -> str:
        """
        Format signal data into Telegram message
        Args:
            signal: Signal dictionary
        Returns:
            Formatted message string
        """
        signal_type = signal['type']
        emoji = "🟢" if signal_type == "LONG" else "🔴"
        
        message = f"""{emoji} *{signal_type} SIGNAL DETECTED* {emoji}

*Coin:* {signal['symbol']}
*Entry:* ${signal['entry_price']}
*Stop Loss:* ${signal['stop_loss']}
*TP1 (40%):* ${signal['take_profit_1']}
*TP Full:* ${signal['take_profit_full']}
*CRV:* {signal['crv']}

*Indicators:*
• RSI: {signal['rsi']}
• EMA 9: ${signal['ema_9']}
• EMA 50: ${signal['ema_50']}

⏰ Time: {signal['timestamp']}

*Action Required:* Check chart and enter trade within 2 candles!
"""
        return message
    
    async def send_signal(self, signal: Dict) -> bool:
        """
        Send signal notification via Telegram
        Args:
            signal: Signal dictionary
        Returns:
            True if sent successfully, False otherwise
        """
        if not self.bot or not self.chat_id:
            logger.warning("Telegram not configured, skipping notification")
            return False
        
        try:
            message = self.format_signal_message(signal)
            
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN
            )
            
            logger.info(f"Telegram notification sent for {signal['symbol']} {signal['type']}")
            return True
        
        except Exception as e:
            logger.error(f"Failed to send Telegram notification: {e}")
            return False
    
    async def send_test_message(self) -> bool:
        """
        Send test message to verify bot setup
        Returns:
            True if sent successfully
        """
        if not self.bot or not self.chat_id:
            return False
        
        try:
            await self.bot.send_message(
                chat_id=self.chat_id,
                text="✅ Crypto Scalping Scanner connected successfully!",
                parse_mode=ParseMode.MARKDOWN
            )
            return True
        except Exception as e:
            logger.error(f"Failed to send test message: {e}")
            return False
