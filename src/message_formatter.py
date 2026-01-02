import logging

logger = logging.getLogger(__name__)


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
