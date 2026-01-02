import asyncio
import logging

from config import get_config

logger = logging.getLogger(__name__)

# Queue instances (initialized by init_queues)
to_send_queue: asyncio.Queue = None
group_creation_queue: asyncio.Queue = None
telegram_send_queue: asyncio.Queue = None
pending_messages: dict = None
groups_being_created: set = None

# Reference to config data
_config_data: dict = None


def init_queues() -> None:
    """Initialize all queues. Called once at startup."""
    global to_send_queue, group_creation_queue, telegram_send_queue
    global pending_messages, groups_being_created, _config_data

    to_send_queue = asyncio.Queue()
    group_creation_queue = asyncio.Queue()
    telegram_send_queue = asyncio.Queue()
    pending_messages = {}
    groups_being_created = set()
    _config_data = get_config()


def get_signal_group_id(chat_id: str) -> str | None:
    """Get Signal group ID for a Telegram chat/channel ID."""
    chats = _config_data.get('chats', {})
    channels = _config_data.get('channels', {})
    return chats.get(chat_id) or channels.get(chat_id)


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
