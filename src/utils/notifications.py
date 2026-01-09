import logging
import time

from telethon.tl.functions.account import GetNotifySettingsRequest
from telethon.tl.types import InputNotifyPeer

logger = logging.getLogger(__name__)

# Special value meaning "muted forever" in Telegram
MUTE_FOREVER = 2147483647


async def is_channel_muted(client, channel_id: int) -> bool:
    """
    Check if a channel is muted based on user's notification settings.

    Returns True if the channel is muted, False if notifications are enabled.
    """
    try:
        entity = await client.get_input_entity(channel_id)
        settings = await client(GetNotifySettingsRequest(
            peer=InputNotifyPeer(peer=entity)
        ))

        mute_until = settings.mute_until
        if mute_until is None or mute_until == 0:
            is_muted = False
        elif mute_until == MUTE_FOREVER:
            is_muted = True
        else:
            # Temporary mute - check if it's still active
            is_muted = mute_until > int(time.time())

        logger.debug('Channel %s mute status: %s (mute_until=%s)',
                     channel_id, is_muted, mute_until)
        return is_muted
    except Exception as e:
        logger.warning('Failed to get notification settings for channel %s: %s', channel_id, e)
        # Default to not muted if we can't determine status
        return False
