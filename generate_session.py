"""Run locally only. Keep the printed session string private."""
import asyncio
import os
from telethon import TelegramClient
from telethon.sessions import StringSession

async def main():
    api_id = os.environ.get("TG_API_ID") or input("Telegram API ID: ").strip()
    api_hash = os.environ.get("TG_API_HASH") or input("Telegram API Hash: ").strip()
    async with TelegramClient(StringSession(), int(api_id), api_hash) as client:
        print("\nSESSION STRING (KEEP PRIVATE; DO NOT SHARE):\n")
        print(client.session.save())
        print("\nCopy it to Railway variable TG_SESSION_STRING. Do not upload it to GitHub.")

if __name__ == "__main__":
    asyncio.run(main())
