import asyncio
import logging
import os
import uuid

import aiohttp
from telethon import TelegramClient, events

import config
import markdown_converter
from signal_group import create_signal_group

logging.basicConfig(format='[%(levelname)s %(asctime)s] %(name)s: %(message)s',
                    level=logging.INFO)
logger = logging.getLogger(__name__)

api_id = os.environ['TG_API_ID']
api_hash = os.environ['TG_API_HASH']
signal_json_rcp = os.environ['SIGNAL_REQUEST_URL']
enable_channels = os.environ['ENABLE_CHANNELS'] == '1'
default_group_expiration_days = int(os.environ.get('DEFAULT_GROUP_MSG_RETENTION_DAYS', '31'))
default_group_member = os.environ['DEFAULT_GROUP_MEMBER']

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


async def main():
    logger.info('Start listening')
    await client.run_until_disconnected()


if __name__ == '__main__':
    logger.info('Starting...')
    with client:
        client.loop.create_task(process_queue())
        client.loop.create_task(process_group_creation_queue())
        client.loop.run_until_complete(main())