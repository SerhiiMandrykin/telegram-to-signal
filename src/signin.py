import asyncio
import os

from telethon import TelegramClient

api_id = os.environ['TG_API_ID']
api_hash = os.environ['TG_API_HASH']


async def main():
    client = TelegramClient('/tg-session/session', api_id, api_hash)

    print('Connecting to Telegram...')
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print(f'Already signed in as: {me.first_name} (@{me.username})')
    else:
        print('Starting interactive sign-in...')
        await client.start()
        me = await client.get_me()
        print(f'Successfully signed in as: {me.first_name} (@{me.username})')

    print('Session saved to /tg-session/session.session')
    await client.disconnect()


if __name__ == '__main__':
    asyncio.run(main())
