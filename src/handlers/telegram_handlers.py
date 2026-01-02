import logging

from telethon import events

logger = logging.getLogger(__name__)


def register_handlers(
    client,
    queue_or_create_group_fn,
    enable_channels: bool,
    enable_read_messages: bool = True,
    enable_read_channels: bool = True
):
    """Register Telegram event handlers on the client."""

    @client.on(events.Album)
    async def album_handler(event):
        logger.info("Got album with %d messages", len(event.messages))

        is_channel = event.is_channel

        if is_channel and not enable_channels:
            logger.debug('Skipping album from channel (not enabled)')
            return

        # Mark album messages as read based on settings
        should_mark_read = (is_channel and enable_read_channels) or (not is_channel and enable_read_messages)
        if should_mark_read:
            for msg in event.messages:
                await msg.mark_read()

        chat_id = str(event.messages[0].chat_id)
        await queue_or_create_group_fn(chat_id, is_channel, ('album', event))

    @client.on(events.NewMessage)
    async def message_handler(event):
        msg = event.message
        is_channel = event.is_channel

        # Mark message as read based on settings
        should_mark_read = (is_channel and enable_read_channels) or (not is_channel and enable_read_messages)
        if should_mark_read:
            await msg.mark_read()

        if is_channel and not enable_channels:
            logger.debug('Skipping message from channel (not enabled)')
            return

        # Skip grouped messages (albums) - handled by album_handler
        if msg.grouped_id:
            return

        chat_id = str(msg.chat_id)
        await queue_or_create_group_fn(chat_id, is_channel, ('message', msg))
