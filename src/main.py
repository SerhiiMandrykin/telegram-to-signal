import asyncio
import logging
import os
import uuid

import aiohttp
from telethon import TelegramClient, events

import config
import markdown_converter
from signal_group import create_signal_group
from signal_listener import SignalSSEListener

logging.basicConfig(format='[%(levelname)s %(asctime)s] %(name)s: %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

api_id = os.environ['TG_API_ID']
api_hash = os.environ['TG_API_HASH']
signal_json_rcp = os.environ['SIGNAL_REQUEST_URL']
enable_channels = os.environ['ENABLE_CHANNELS'] == '1'
default_group_expiration_days = int(os.environ.get('DEFAULT_GROUP_MSG_RETENTION_DAYS', '31'))
default_group_member = os.environ['DEFAULT_GROUP_MEMBER']

# Signal to Telegram settings
enable_signal_to_telegram = os.environ.get('ENABLE_SIGNAL_TO_TELEGRAM', '0') == '1'
signal_events_url = os.environ['SIGNAL_EVENTS_URL'] if enable_signal_to_telegram else None

# Path where signal-cli stores received attachments.
# A dedicated signal-attachments volume is mounted here (shared between signal-cli and telegram-app).
# When Signal sends an attachment, it's saved to this path and we read it to forward to Telegram.
signal_attachments_path = '/signal-attachments'

config_data = config.get_config()


def get_signal_group_id(chat_id: str) -> str | None:
    """Get Signal group ID for a Telegram chat/channel ID."""
    chats = config_data.get('chats', {})
    channels = config_data.get('channels', {})
    return chats.get(chat_id) or channels.get(chat_id)

loop = asyncio.new_event_loop()
asyncio.set_event_loop(loop)

client = TelegramClient('/tg-session/session', api_id, api_hash, loop=loop)

to_send_queue = asyncio.Queue()
group_creation_queue = asyncio.Queue()
telegram_send_queue = asyncio.Queue()  # Queue for Signal -> Telegram messages
pending_messages = {}  # chat_id -> list of (item_type, item) waiting for group creation
groups_being_created = set()  # chat_ids currently being processed for group creation


async def queue_or_create_group(chat_id: str, is_channel: bool, item: tuple):
    """Queue message for sending, or initiate group creation if chat is unmapped."""
    signal_group_id = get_signal_group_id(chat_id)

    if signal_group_id:
        logger.info('Adding item for processing (chat %s -> group %s)', chat_id, signal_group_id)
        to_send_queue.put_nowait(item)
    elif chat_id in groups_being_created:
        logger.info('Group creation in progress for chat %s, queueing message', chat_id)
        if chat_id not in pending_messages:
            pending_messages[chat_id] = []
        pending_messages[chat_id].append(item)
    else:
        logger.info('No Signal group for chat %s, initiating group creation', chat_id)
        groups_being_created.add(chat_id)
        if chat_id not in pending_messages:
            pending_messages[chat_id] = []
        pending_messages[chat_id].append(item)
        group_creation_queue.put_nowait((chat_id, is_channel))


@client.on(events.Album)
async def album_handler(event):
    # event.messages is a list of Message objects
    logger.info("Got album with %d messages", len(event.messages))

    # TODO: Channel support will be added later
    if event.is_channel and not enable_channels:
        logger.debug('Skipping album from channel (not enabled)')
        return

    chat_id = str(event.messages[0].chat_id)
    await queue_or_create_group(chat_id, event.is_channel, ('album', event))


@client.on(events.NewMessage)
async def my_event_handler(event):
    msg = event.message
    await msg.mark_read()

    # TODO: Channel support will be added later
    if event.is_channel and not enable_channels:
        logger.debug('Skipping message from channel (not enabled)')
        return

    # Skip grouped messages (albums) - handled by album_handler
    if msg.grouped_id:
        return

    chat_id = str(msg.chat_id)
    await queue_or_create_group(chat_id, event.is_channel, ('message', msg))


async def process_queue():
    while True:
        item_type, item = await to_send_queue.get()

        try:
            if item_type == 'message':
                await process_message(item)
            elif item_type == 'album':
                await process_album(item)
        except Exception as e:
            logger.exception('Error when processing %s: %s', item_type, e)
        finally:
            to_send_queue.task_done()

        await asyncio.sleep(1)


async def process_message(msg):
    logger.info('Start processing a simple message')

    attachments = []
    a_path = ''
    if msg.photo or msg.video:
        logger.info('The message has media. Downloading...')
        a_path = await client.download_media(msg, file='/media/')
        attachments.append(a_path)
        logger.info('The file was downloaded: %s', a_path)

    chat_id = str(msg.chat_id)
    signal_group_id = get_signal_group_id(chat_id)
    if not signal_group_id:
        logger.warning('No Signal group found for chat %s, skipping message', chat_id)
        return

    parsed_text, styles = markdown_converter.convert_telegram_markdown(msg.text or '')
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "send",
        "params": {
            "groupId": signal_group_id,
            "message": parsed_text,
            "attachment": attachments
        }
    }

    if styles:
        payload["params"]["textStyles"] = styles

    async with aiohttp.ClientSession() as session:
        logger.info('Sending to %s', signal_json_rcp)
        async with session.post(signal_json_rcp, json=payload) as resp:
            logger.info('Status: %d', resp.status)
            text = await resp.text()
            logger.debug('Body: %s', text)

    if a_path:
        os.remove(a_path)
        logger.info('File %s was deleted', a_path)


async def process_album(event):
    logger.info('Start processing an album')

    attachments = []
    message_text = ''
    chat_id = None

    msg_list = event if isinstance(event, list) else event.messages
    for msg in msg_list:
        await msg.mark_read()
        if not message_text and msg.text:
            message_text = msg.text

        if not chat_id:
            chat_id = str(msg.chat_id)

        if msg.photo or msg.video:
            logger.info('The message has media. Downloading...')
            a_path = await client.download_media(msg, file='/media/')
            attachments.append(a_path)
            logger.info('The file was downloaded: %s', a_path)

    signal_group_id = get_signal_group_id(chat_id)
    if not signal_group_id:
        logger.warning('No Signal group found for chat %s, skipping album', chat_id)
        return

    parsed_text, styles = markdown_converter.convert_telegram_markdown(message_text or '')
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "send",
        "params": {
            "groupId": signal_group_id,
            "message": parsed_text,
            "attachment": attachments
        }
    }

    if styles:
        payload["params"]["textStyles"] = styles

    async with aiohttp.ClientSession() as session:
        logger.info('Sending to %s', signal_json_rcp)
        async with session.post(signal_json_rcp, json=payload) as resp:
            logger.info('Status: %d', resp.status)
            text = await resp.text()
            logger.debug('Body: %s', text)

    for a in attachments:
        os.remove(a)


async def process_group_creation_queue():
    """Process the group creation queue."""
    while True:
        chat_id, is_channel = await group_creation_queue.get()

        try:
            group_id = await create_signal_group(
                client,
                chat_id,
                is_channel,
                signal_json_rcp,
                default_group_member,
                default_group_expiration_days
            )

            if group_id:
                # Update config with new mapping
                config_key = 'channels' if is_channel else 'chats'
                if config_key not in config_data:
                    config_data[config_key] = {}
                config_data[config_key][chat_id] = group_id
                config.save_config()
                logger.info('Saved mapping: %s[%s] = %s', config_key, chat_id, group_id)

                # Re-queue pending messages for this chat
                if chat_id in pending_messages:
                    for item in pending_messages[chat_id]:
                        to_send_queue.put_nowait(item)
                    logger.info('Re-queued %d pending messages for chat %s',
                                len(pending_messages[chat_id]), chat_id)
                    del pending_messages[chat_id]
            else:
                logger.error('Failed to create group for chat %s, discarding pending messages', chat_id)
                if chat_id in pending_messages:
                    del pending_messages[chat_id]

        except Exception as e:
            logger.exception('Error in group creation for chat %s: %s', chat_id, e)
        finally:
            groups_being_created.discard(chat_id)
            group_creation_queue.task_done()

        await asyncio.sleep(1)


async def handle_signal_message(message_info: dict):
    """Handle incoming Signal message and queue for Telegram sending."""
    group_id = message_info['group_id']
    message = message_info.get('message', '')
    attachments = message_info.get('attachments', [])

    # Skip messages with no content (no text and no attachments)
    if not message and not attachments:
        logger.debug('Ignoring empty Signal message (no text, no attachments)')
        return

    # Look up corresponding Telegram chat
    lookup = config.get_telegram_chat_id(group_id)
    if not lookup:
        logger.debug('No Telegram mapping for Signal group %s', group_id)
        return

    chat_id, is_channel = lookup

    # Queue for sending to Telegram
    telegram_send_queue.put_nowait({
        'chat_id': int(chat_id),
        'is_channel': is_channel,
        'message': message,
        'sender_name': message_info.get('sender_name', ''),
        'attachments': attachments,
    })
    logger.info('Queued Signal message for Telegram chat %s (attachments: %d)', chat_id, len(attachments))


async def process_telegram_send_queue():
    """Process queue of messages to send to Telegram."""
    while True:
        item = await telegram_send_queue.get()

        try:
            chat_id = item['chat_id']
            message = item['message']
            attachments = item.get('attachments', [])

            # Build list of attachment file paths.
            # Signal-cli stores attachments with their ID as filename in the attachments directory.
            # The attachment object contains 'id' field which is the filename (e.g., "S0Fy8qTlVSanwJ1UJB7n.jpeg").
            attachment_paths = []
            for attachment in attachments:
                attachment_id = attachment.get('id')
                if attachment_id:
                    file_path = os.path.join(signal_attachments_path, attachment_id)
                    if os.path.exists(file_path):
                        attachment_paths.append(file_path)
                        logger.info('Found Signal attachment: %s', file_path)
                    else:
                        logger.warning('Signal attachment not found: %s', file_path)

            # Send to Telegram with or without attachments
            if attachment_paths:
                # If multiple attachments, send as album (media group)
                if len(attachment_paths) > 1:
                    await client.send_file(chat_id, attachment_paths, caption=message or None)
                    logger.info('Sent album with %d files to Telegram chat %s', len(attachment_paths), chat_id)
                else:
                    # Single attachment
                    await client.send_file(chat_id, attachment_paths[0], caption=message or None)
                    logger.info('Sent file to Telegram chat %s', chat_id)
            elif message:
                # Text-only message (no attachments)
                await client.send_message(chat_id, message)
                logger.info('Sent message to Telegram chat %s', chat_id)

        except Exception as e:
            logger.exception('Error sending to Telegram: %s', e)
        finally:
            telegram_send_queue.task_done()

        await asyncio.sleep(0.5)


async def main():
    logger.info('Start listening')

    # Start Signal SSE listener if enabled
    if enable_signal_to_telegram:
        signal_listener = SignalSSEListener(
            events_url=signal_events_url,
            on_message=handle_signal_message
        )
        asyncio.create_task(signal_listener.start())
        logger.info('Signal-to-Telegram forwarding enabled, SSE listener started')

    await client.run_until_disconnected()


if __name__ == '__main__':
    logger.info('Starting...')
    with client:
        client.loop.create_task(process_queue())
        client.loop.create_task(process_group_creation_queue())
        if enable_signal_to_telegram:
            client.loop.create_task(process_telegram_send_queue())
        client.loop.run_until_complete(main())