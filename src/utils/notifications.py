import logging
from datetime import datetime, timezone

from telethon.tl.functions.account import GetNotifySettingsRequest
from telethon.tl.types import InputNotifyPeer

logger = logging.getLogger(__name__)


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
        if mute_until is None:
            is_muted = False
        elif isinstance(mute_until, datetime):
            # Telethon converts the timestamp to datetime
            # Check if mute is still active (mute_until in the future)
            is_muted = mute_until > datetime.now(timezone.utc)
        else:
            # Fallback for integer timestamp (0 = not muted)
            is_muted = mute_until > 0

        logger.debug('Channel %s mute status: %s (mute_until=%s)',
                     channel_id, is_muted, mute_until)
        return is_muted
    except Exception as e:
        logger.warning('Failed to get notification settings for channel %s: %s', channel_id, e)
        # Default to not muted if we can't determine status
        return False
