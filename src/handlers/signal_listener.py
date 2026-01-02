import asyncio
import json
import logging
from typing import Callable, Awaitable

import aiohttp

logger = logging.getLogger(__name__)


class SignalSSEListener:
    """
    Listens to Signal SSE events and forwards messages to a callback.
    """

    def __init__(
        self,
        events_url: str,
        on_message: Callable[[dict], Awaitable[None]],
        reconnect_delay: float = 5.0
    ):
        self.events_url = events_url
        self.on_message = on_message
        self.reconnect_delay = reconnect_delay
        self._running = False
        self._session: aiohttp.ClientSession | None = None

    async def start(self):
        """Start listening to SSE events with automatic reconnection."""
        self._running = True
        self._session = aiohttp.ClientSession()

        while self._running:
            try:
                await self._listen()
            except aiohttp.ClientError as e:
                logger.error('SSE connection error: %s', e)
            except asyncio.CancelledError:
                logger.info('SSE listener cancelled')
                break
            except Exception as e:
                logger.exception('Unexpected error in SSE listener: %s', e)

            if self._running:
                logger.info('Reconnecting to SSE in %.1f seconds...', self.reconnect_delay)
                await asyncio.sleep(self.reconnect_delay)

        await self._session.close()

    async def _listen(self):
        """Connect to SSE endpoint and process events."""
        logger.info('Connecting to Signal SSE: %s', self.events_url)

        async with self._session.get(
            self.events_url,
            timeout=aiohttp.ClientTimeout(total=None, sock_read=None)
        ) as response:
            if response.status != 200:
                logger.error('SSE endpoint returned status %d', response.status)
                return

            logger.info('Connected to Signal SSE stream')

            async for line in response.content:
                if not self._running:
                    break

                line = line.decode('utf-8').strip()

                # SSE format: "data: {json}"
                if line.startswith('data:'):
                    data_str = line[5:].strip()
                    if data_str:
                        try:
                            event_data = json.loads(data_str)
                            await self._handle_event(event_data)
                        except json.JSONDecodeError as e:
                            logger.warning('Failed to parse SSE data: %s', e)

    async def _handle_event(self, event: dict):
        """Process a single SSE event."""
        # Extract envelope from event
        envelope = event.get('envelope', {})

        # Check if this is a data message (not sync, receipt, typing, etc.)
        data_message = envelope.get('dataMessage')
        if not data_message:
            logger.debug('Ignoring non-data message event')
            return

        # Check if this is a group message
        group_info = data_message.get('groupInfo')
        if not group_info:
            logger.debug('Ignoring direct message (not a group)')
            return

        group_id = group_info.get('groupId')
        message_text = data_message.get('message', '')

        if not group_id:
            logger.debug('No groupId in message')
            return

        # Build message info dict
        message_info = {
            'group_id': group_id,
            'message': message_text,
            'sender_name': envelope.get('sourceName', ''),
            'sender_number': envelope.get('sourceNumber', ''),
            'sender_uuid': envelope.get('sourceUuid', ''),
            'timestamp': envelope.get('timestamp', 0),
            'attachments': data_message.get('attachments', []),
        }

        logger.info('Received Signal group message in %s from %s',
                    group_id, message_info['sender_name'])

        await self.on_message(message_info)

    def stop(self):
        """Stop the SSE listener."""
        self._running = False
