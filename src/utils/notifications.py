import logging
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

        # mute_until > 0 means notifications are muted
        # mute_until can be a timestamp or special values like 2147483647 (forever)
        is_muted = settings.mute_until is not None and settings.mute_until > 0

        logger.debug('Channel %s mute status: %s (mute_until=%s)',
                     channel_id, is_muted, settings.mute_until)
        return is_muted
    except Exception as e:
        logger.warning('Failed to get notification settings for channel %s: %s', channel_id, e)
        # Default to not muted if we can't determine status
        return False
