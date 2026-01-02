import logging
import os
import uuid

import aiohttp

logger = logging.getLogger(__name__)

# Optional prefix to prepend to Signal group names (e.g., "(Telegram)" -> "(Telegram) My Chat").
# When set, helps users identify which Signal groups are bridged from Telegram.
# If empty or unset, the original Telegram chat name is used as-is.
GROUP_NAME_PREFIX = os.environ.get('GROUP_NAME_PREFIX', '').strip()


async def create_signal_group(
    client,
    chat_id: str,
    is_channel: bool,
    signal_json_rcp: str,
    default_group_member: str,
    default_group_expiration_days: int
) -> str | None:
    """Create a Signal group for a Telegram chat/channel and return the group ID."""
    logger.info('Creating Signal group for Telegram chat %s (is_channel=%s)', chat_id, is_channel)

    # Get chat entity to retrieve title and profile photo
    try:
        entity = await client.get_entity(int(chat_id))
    except Exception as e:
        logger.exception('Failed to get entity for chat %s: %s', chat_id, e)
        return None

    chat_title = getattr(entity, 'title', None) or getattr(entity, 'first_name', 'Unknown Chat')
    chat_username = getattr(entity, 'username', None)

    # Apply optional prefix to group name (e.g., "(Telegram) My Chat").
    # This helps users distinguish bridged groups from native Signal groups.
    if GROUP_NAME_PREFIX:
        group_name = f'{GROUP_NAME_PREFIX} {chat_title}'
    else:
        group_name = chat_title

    # Download profile photo if exists
    profile_pic_path = None
    try:
        photos = await client.get_profile_photos(entity, limit=1)
        if photos:
            profile_pic_path = await client.download_media(photos[0], file='/media/')
            logger.info('Downloaded profile photo: %s', profile_pic_path)
    except Exception as e:
        logger.warning('Failed to download profile photo: %s', e)

    # Build Telegram link for description
    if chat_username:
        channel_link = f'https://t.me/{chat_username}'
    else:
        channel_link = chat_title

    # Create Signal group
    payload = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "updateGroup",
        "params": {
            "name": group_name,
            "members": [default_group_member],
            "link": "enabled",
            "setPermissionEditDetails": "everyMember",
            "setPermissionSendMessages": "everyMember",
            "setPermissionAddMember": "everyMember",
            "description": f"Telegram: {channel_link}",
            "expiration": default_group_expiration_days * 86400
        }
    }

    if profile_pic_path:
        payload["params"]["avatar"] = profile_pic_path

    group_id = None
    try:
        async with aiohttp.ClientSession() as session:
            logger.info('Creating Signal group via %s', signal_json_rcp)
            async with session.post(signal_json_rcp, json=payload) as resp:
                logger.info('Status: %d', resp.status)
                if resp.status != 200:
                    logger.error('Failed to create group: %s', await resp.text())
                    return None
                response = await resp.json()
                group_id = response['result']['groupId']
                logger.info('Created Signal group with ID: %s', group_id)
    except Exception as e:
        logger.exception('Error creating Signal group: %s', e)
        return None
    finally:
        if profile_pic_path and os.path.exists(profile_pic_path):
            os.remove(profile_pic_path)

    if not group_id:
        return None

    return group_id
