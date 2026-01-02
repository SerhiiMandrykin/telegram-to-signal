import asyncio
import logging
import os

from telethon import TelegramClient

from core import queue_manager
from handlers import telegram_handlers
from core import telegram_to_signal
from core import signal_to_telegram
from handlers.signal_listener import SignalSSEListener

logging.basicConfig(format='[%(levelname)s %(asctime)s] %(name)s: %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

# Telegram settings
api_id = os.environ['TG_API_ID']
api_hash = os.environ['TG_API_HASH']
signal_json_rcp = os.environ['SIGNAL_REQUEST_URL']
enable_channels = os.environ['ENABLE_CHANNELS'] == '1'
default_group_expiration_days = int(os.environ.get('DEFAULT_GROUP_MSG_RETENTION_DAYS', '31'))
default_group_member = os.environ['DEFAULT_GROUP_MEMBER']

# Read message settings
enable_read_messages = os.environ.get('ENABLE_READ_MESSAGES', '1') == '1'
enable_read_channels = os.environ.get('ENABLE_READ_CHANNELS', '1') == '1'

# Signal to Telegram settings
enable_signal_to_telegram = os.environ.get('ENABLE_SIGNAL_TO_TELEGRAM', '0') == '1'
signal_events_url = os.environ['SIGNAL_EVENTS_URL'] if enable_signal_to_telegram else None
send_video_as_note = os.environ.get('SEND_VIDEO_AS_NOTE', '1') == '1'
signal_attachments_path = '/signal-attachments'

# Initialize event loop and client
loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)
client = TelegramClient('/tg-session/session', api_id, api_hash, loop=loop)

# Initialize queues and register handlers
queue_manager.init_queues()
telegram_handlers.register_handlers(
    client, queue_manager.queue_or_create_group, enable_channels,
    enable_read_messages, enable_read_channels
)


async def main():
    logger.info('Start listening')

    # Start Signal SSE listener if enabled
    if enable_signal_to_telegram:
        async def on_signal_message(message_info: dict):
            await signal_to_telegram.handle_signal_message(message_info, signal_json_rcp)

        signal_listener = SignalSSEListener(
            events_url=signal_events_url,
            on_message=on_signal_message
        )
        asyncio.create_task(signal_listener.start())
        logger.info('Signal-to-Telegram forwarding enabled, SSE listener started')

    await client.run_until_disconnected()


if __name__ == '__main__':
    logger.info('Starting...')
    with client:
        client.loop.create_task(telegram_to_signal.process_queue(client, signal_json_rcp))
        client.loop.create_task(telegram_to_signal.process_group_creation_queue(
            client, signal_json_rcp, default_group_member, default_group_expiration_days
        ))
        if enable_signal_to_telegram:
            client.loop.create_task(signal_to_telegram.process_telegram_send_queue(
                client, signal_attachments_path, send_video_as_note
            ))
        client.loop.run_until_complete(main())
