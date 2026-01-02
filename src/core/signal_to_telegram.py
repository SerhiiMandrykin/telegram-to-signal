import asyncio
import logging
import os
import uuid

import aiohttp

import config
from core import queue_manager
from media.converter import convert_m4a_to_ogg_opus, cleanup_files

logger = logging.getLogger(__name__)


async def send_read_receipt(signal_json_rpc: str, recipient: str, timestamp: int):
    """Send a read receipt to Signal for a received message."""
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "sendReceipt",
        "params": {
            "recipient": recipient,
            "targetTimestamp": timestamp,
            "type": "read"
        }
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(signal_json_rpc, json=payload) as resp:
                if resp.status == 200:
                    logger.debug('Sent read receipt for message from %s at %d', recipient, timestamp)
                else:
                    text = await resp.text()
                    logger.warning('Failed to send read receipt: status=%d, body=%s', resp.status, text)
    except Exception as e:
        logger.warning('Error sending read receipt: %s', e)


async def handle_signal_message(message_info: dict, signal_json_rpc: str):
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

    # Queue for sending to Telegram (include info for read receipt)
    queue_manager.telegram_send_queue.put_nowait({
        'chat_id': int(chat_id),
        'is_channel': is_channel,
        'message': message,
        'sender_name': message_info.get('sender_name', ''),
        'attachments': attachments,
        # Info for sending read receipt after forwarding
        'signal_json_rpc': signal_json_rpc,
        'sender_number': message_info.get('sender_number', ''),
        'sender_uuid': message_info.get('sender_uuid', ''),
        'timestamp': message_info.get('timestamp', 0),
    })
    logger.info('Queued Signal message for Telegram chat %s (attachments: %d)', chat_id, len(attachments))


async def process_telegram_send_queue(client, signal_attachments_path: str, send_video_as_note: bool):
    """Process queue of messages to send to Telegram."""
    while True:
        item = await queue_manager.telegram_send_queue.get()
        signal_attachment_paths = []  # Track original Signal attachments for cleanup
        converted_voice_paths = []  # Track converted voice files for cleanup

        try:
            chat_id = item['chat_id']
            message = item['message']
            attachments = item.get('attachments', [])

            # Build list of attachment file paths and track voice notes.
            # Signal-cli stores attachments with their ID as filename in the attachments directory.
            # The attachment object contains 'id' field which is the filename (e.g., "S0Fy8qTlVSanwJ1UJB7n.jpeg").
            # Voice notes are detected by .m4a extension (Signal uses this format for voice messages).
            attachment_paths = []
            voice_note_paths = []
            video_note_paths = []
            for attachment in attachments:
                attachment_id = attachment.get('id')
                if attachment_id:
                    file_path = os.path.join(signal_attachments_path, attachment_id)
                    if os.path.exists(file_path):
                        signal_attachment_paths.append(file_path)
                        # Detect voice notes by .m4a extension (Signal voice notes use this format)
                        is_voice_note = attachment_id.endswith('.m4a')
                        is_video = attachment_id.endswith('.mp4')
                        if is_voice_note:
                            # Convert M4A to OGG Opus for Telegram compatibility
                            converted_path = convert_m4a_to_ogg_opus(file_path)
                            if converted_path:
                                voice_note_paths.append(converted_path)
                                converted_voice_paths.append(converted_path)
                            else:
                                # Fallback to original if conversion fails
                                voice_note_paths.append(file_path)
                            logger.info('Found Signal voice note: %s', file_path)
                        elif is_video and send_video_as_note:
                            video_note_paths.append(file_path)
                            logger.info('Found Signal video (will send as video note): %s', file_path)
                        else:
                            attachment_paths.append(file_path)
                            logger.info('Found Signal attachment: %s', file_path)
                    else:
                        logger.warning('Signal attachment not found: %s', file_path)

            # Send voice notes as voice messages
            for voice_path in voice_note_paths:
                await client.send_file(chat_id, voice_path, voice_note=True)
                logger.info('Sent voice note to Telegram chat %s', chat_id)

            # Send videos as round video notes
            for video_path in video_note_paths:
                await client.send_file(chat_id, video_path, video_note=True)
                logger.info('Sent video note to Telegram chat %s', chat_id)

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

            # Send read receipt to Signal after successfully forwarding
            signal_json_rpc = item.get('signal_json_rpc')
            sender = item.get('sender_uuid') or item.get('sender_number')
            timestamp = item.get('timestamp')
            if signal_json_rpc and sender and timestamp:
                await send_read_receipt(signal_json_rpc, sender, timestamp)

        except Exception as e:
            logger.exception('Error sending to Telegram: %s', e)
        finally:
            # Clean up converted voice files
            cleanup_files(*converted_voice_paths)
            # Clean up original Signal attachments
            cleanup_files(*signal_attachment_paths)
            queue_manager.telegram_send_queue.task_done()

        await asyncio.sleep(0.5)
