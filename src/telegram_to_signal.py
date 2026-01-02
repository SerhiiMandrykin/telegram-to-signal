import asyncio
import logging
import uuid

import aiohttp

import config
import markdown_converter
import queue_manager
from media import convert_ogg_to_m4a, cleanup_files
from message_formatter import get_sender_name, format_message_with_sender
from signal_group import create_signal_group

logger = logging.getLogger(__name__)


async def process_queue(client, signal_json_rcp: str):
    """Main queue processor for Telegram -> Signal messages."""
    while True:
        item_type, item = await queue_manager.to_send_queue.get()

        try:
            if item_type == 'message':
                await process_message(item, client, signal_json_rcp)
            elif item_type == 'album':
                await process_album(item, client, signal_json_rcp)
        except Exception as e:
            logger.exception('Error when processing %s: %s', item_type, e)
        finally:
            queue_manager.to_send_queue.task_done()

        await asyncio.sleep(1)


async def process_message(msg, client, signal_json_rcp: str):
    """Process and send a single Telegram message to Signal."""
    logger.info('Start processing a simple message')

    attachments = []
    a_path = ''
    converted_path = ''
    if msg.photo or msg.video or msg.voice:
        media_type = 'voice' if msg.voice else ('photo' if msg.photo else 'video')
        logger.info('The message has %s. Downloading...', media_type)
        a_path = await client.download_media(msg, file='/media/')
        logger.info('The file was downloaded: %s', a_path)

        # Convert voice message from OGG to M4A for Signal iOS compatibility
        if msg.voice:
            converted_path = convert_ogg_to_m4a(a_path)
            if converted_path:
                attachments.append(converted_path)
            else:
                # Fallback to original if conversion fails
                attachments.append(a_path)
        else:
            attachments.append(a_path)

    chat_id = str(msg.chat_id)
    signal_group_id = queue_manager.get_signal_group_id(chat_id)
    if not signal_group_id:
        logger.warning('No Signal group found for chat %s, skipping message', chat_id)
        return

    # Get sender name for group chats
    sender_name = await get_sender_name(msg)
    message_text = format_message_with_sender(msg.text or '', sender_name)

    parsed_text, styles = markdown_converter.convert_telegram_markdown(message_text)
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

    # Clean up downloaded and converted files
    cleanup_files(a_path, converted_path)


async def process_album(event, client, signal_json_rcp: str):
    """Process and send a Telegram album to Signal."""
    logger.info('Start processing an album')

    attachments = []
    message_text = ''
    chat_id = None
    first_msg = None

    msg_list = event if isinstance(event, list) else event.messages
    for msg in msg_list:
        await msg.mark_read()
        if not message_text and msg.text:
            message_text = msg.text

        if not chat_id:
            chat_id = str(msg.chat_id)

        if not first_msg:
            first_msg = msg

        if msg.photo or msg.video:
            logger.info('The message has media. Downloading...')
            a_path = await client.download_media(msg, file='/media/')
            attachments.append(a_path)
            logger.info('The file was downloaded: %s', a_path)

    signal_group_id = queue_manager.get_signal_group_id(chat_id)
    if not signal_group_id:
        logger.warning('No Signal group found for chat %s, skipping album', chat_id)
        return

    # Get sender name for group chats (use first message to get sender)
    sender_name = await get_sender_name(first_msg) if first_msg else None
    message_text = format_message_with_sender(message_text or '', sender_name)

    parsed_text, styles = markdown_converter.convert_telegram_markdown(message_text)
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

    cleanup_files(*attachments)


async def process_group_creation_queue(
    client,
    signal_json_rcp: str,
    default_group_member: str,
    default_group_expiration_days: int
):
    """Process the group creation queue."""
    config_data = config.get_config()

    while True:
        chat_id, is_channel = await queue_manager.group_creation_queue.get()

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
                if chat_id in queue_manager.pending_messages:
                    for item in queue_manager.pending_messages[chat_id]:
                        queue_manager.to_send_queue.put_nowait(item)
                    logger.info('Re-queued %d pending messages for chat %s',
                                len(queue_manager.pending_messages[chat_id]), chat_id)
                    del queue_manager.pending_messages[chat_id]
            else:
                logger.error('Failed to create group for chat %s, discarding pending messages', chat_id)
                if chat_id in queue_manager.pending_messages:
                    del queue_manager.pending_messages[chat_id]

        except Exception as e:
            logger.exception('Error in group creation for chat %s: %s', chat_id, e)
        finally:
            queue_manager.groups_being_created.discard(chat_id)
            queue_manager.group_creation_queue.task_done()

        await asyncio.sleep(1)
