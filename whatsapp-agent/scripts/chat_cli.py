#!/usr/bin/env python3
"""
Interactive CLI chat runner for testing the WhatsApp AI agent locally in the terminal.
"""
import asyncio
import os
import sys
from unittest.mock import patch

# Ensure whatsapp-agent root is in Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app import db
from app.agent import run_agent
from app.config import settings


async def main():
    print("=" * 60)
    print("💬 WhatsApp AI Agent Local CLI Chat Simulator")
    print(f"Business Name: {settings.business_name}")
    print("Type 'exit' or 'quit' to end chat.")
    print("=" * 60)

    phone = "+923001234567"
    name = "CLI Tester"
    has_db = False

    try:
        await db.init_db_pool()
        customer = await db.get_or_create_customer(phone, name)
        has_db = True
        print("✅ Connected to Postgres database.")
    except Exception as e:
        print(f"⚠️ Postgres DB not running ({e}). Running in standalone terminal mode...")
        customer = {"id": 999, "phone": phone, "name": name, "bot_active": True}

    chat_history = []

    while True:
        try:
            user_input = input("\n👤 Customer: ").strip()
            if not user_input:
                continue
            if user_input.lower() in ("exit", "quit"):
                print("👋 Exiting CLI Chat.")
                break

            print("🤖 Agent thinking...")
            if has_db:
                reply = await run_agent(customer, user_input)
            else:
                chat_history.append({"role": "user", "content": user_input})
                with patch("app.db.save_message", return_value=True), \
                     patch("app.db.is_bot_active", return_value=True), \
                     patch("app.db.get_recent_messages", return_value=chat_history[-10:]):
                    reply = await run_agent(customer, user_input)
                if reply:
                    chat_history.append({"role": "assistant", "content": reply})

            print(f"🤖 Bot: {reply}")

        except KeyboardInterrupt:
            print("\n👋 Exiting CLI Chat.")
            break
        except Exception as e:
            print(f"❌ Error: {e}")

    if has_db:
        await db.close_db_pool()


if __name__ == "__main__":
    asyncio.run(main())
