import asyncio
import logging
import os
import subprocess
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
send_video_as_note = os.environ.get('SEND_VIDEO_AS_NOTE', '1') == '1'

# Path where signal-cli stores received attachments.
# A dedicated signal-attachments volume is mounted here (shared between signal-cli and telegram-app).
# When Signal sends an attachment, it's saved to this path and we read it to forward to Telegram.
signal_attachments_path = '/signal-attachments'

config_data = config.get_config()


def convert_ogg_to_m4a(input_path: str) -> str | None:
    """Convert OGG voice file to M4A for Signal iOS compatibility."""
    output_path = input_path.rsplit('.', 1)[0] + '.m4a'
    try:
        subprocess.run([
            'ffmpeg', '-y', '-i', input_path,
            '-c:a', 'aac', '-b:a', '64k',
            output_path
        ], check=True, capture_output=True)
        logger.info('Converted %s to %s', input_path, output_path)
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error('Failed to convert OGG to M4A: %s', e.stderr.decode())
        return None
    except FileNotFoundError:
        logger.error('ffmpeg not found, cannot convert voice message')
        return None


def convert_m4a_to_ogg_opus(input_path: str) -> str | None:
    """Convert M4A voice file to OGG Opus for Telegram voice note compatibility."""
    basename = os.path.basename(input_path)
    name_without_ext = basename.rsplit('.', 1)[0] if '.' in basename else basename
    output_path = f'/media/{name_without_ext}.ogg'
    try:
        subprocess.run([
            'ffmpeg', '-y', '-i', input_path,
            '-c:a', 'libopus', '-b:a', '64k',
            output_path
        ], check=True, capture_output=True)
        logger.info('Converted %s to %s', input_path, output_path)
        return output_path
    except subprocess.CalledProcessError as e:
        logger.error('Failed to convert M4A to OGG Opus: %s', e.stderr.decode())
        return None
    except FileNotFoundError:
        logger.error('ffmpeg not found, cannot convert voice message')
        return None


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


async def get_sender_name(msg) -> str | None:
    """Get the sender's display name for group chat messages."""
    if not msg.is_group:
        return None

    sender = await msg.get_sender()
    if not sender:
        return None

    # Use first_name + last_name, or username, or fallback to user id
    if hasattr(sender, 'first_name') and sender.first_name:
        name = sender.first_name
        if hasattr(sender, 'last_name') and sender.last_name:
            name += f' {sender.last_name}'
        return name
    elif hasattr(sender, 'username') and sender.username:
        return sender.username
    elif hasattr(sender, 'id'):
        return f'User {sender.id}'
    return None


def format_message_with_sender(text: str, sender_name: str | None) -> str:
    """Format message with sender name prefix for group chats."""
    if not sender_name:
        return text or ''

    if text:
        return f'{sender_name}:\n{text}'
    else:
        return f'{sender_name}:'


async def process_message(msg):
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
    signal_group_id = get_signal_group_id(chat_id)
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
    if a_path and os.path.exists(a_path):
        os.remove(a_path)
        logger.info('File %s was deleted', a_path)
    if converted_path and os.path.exists(converted_path):
        os.remove(converted_path)
        logger.info('File %s was deleted', converted_path)


async def process_album(event):
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

    signal_group_id = get_signal_group_id(chat_id)
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

        except Exception as e:
            logger.exception('Error sending to Telegram: %s', e)
        finally:
            # Clean up converted voice files
            for path in converted_voice_paths:
                if os.path.exists(path):
                    os.remove(path)
                    logger.info('Cleaned up converted voice file: %s', path)
            # Clean up original Signal attachments
            for path in signal_attachment_paths:
                if os.path.exists(path):
                    os.remove(path)
                    logger.info('Cleaned up Signal attachment: %s', path)
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