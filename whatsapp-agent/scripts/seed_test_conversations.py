#!/usr/bin/env python3
"""
Automated test suite script with 30 sample questions in English, Urdu, and Roman Urdu
(including misspelled queries and angry-customer handoff scenarios).
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.agent import run_agent

SAMPLE_QUESTIONS = [
    # English Queries (Catalog & Orders)
    "Hi, do you have cotton shirts in stock?",
    "What is the price of white sneakers?",
    "Can I get a discount if I buy 5 items?",
    "I want to check my order status for order SO001.",
    "Where are you located and what are your opening hours?",
    "Do you deliver to Karachi?",
    "Can you deliver my order tomorrow morning positively?",
    "I want to place an order for 2 cotton shirts.",
    "Please cancel my order immediately!",
    "Your service is terrible! I am very angry!",

    # Roman Urdu Queries (Inquiries & Buying interest)
    "Salam, shirt ki price kya hai?",
    "Bhai 2000 rupees wali shirt in stock hai?",
    "Kya mujhey discount mil sakta hai 10%?",
    "Mera order status batao please.",
    "Aap ki dukan kab khulti hai?",
    "Delivery charges kitne hain Lahore ke?",
    "Mujhey 3 cotton shirts khareedni hain quote bana do.",
    "Mujhe ghalat saman bheja hai aap logo ne! Boht bekaar service hai!",
    "Bhai payment cash on delivery ho sakti hai?",
    "Aap ka phone number kya hai customer care ka?",

    # Urdu Script Queries
    "السلام علیکم، کیا سوتی شرٹ دستیاب ہے؟",
    "قیمت کیا ہے شرٹ کی؟",
    "کیا مجھے ڈسکاؤنٹ مل سکتا ہے؟",
    "میرے آرڈر کی صورتحال کیا ہے؟",
    "آپ کی دکان کا پتہ کیا ہے؟",
    "کیا کراچی میں ڈیلیوری ہوتی ہے؟",
    "مجھے ٢ شرٹس خریدنی ہیں کوٹیشن بنا دیں",
    "آپ کی سروس بہت خراب ہے! مجھے میرا پیسہ واپس چاہیے!",
    "کیا کیش آن ڈیلیوری کی سہولت ہے؟",
    "شکریہ، مجھے بات کرنی ہے انسانی نمائندے سے"
]


async def run_seed_tests():
    print("=" * 70)
    print("🧪 Running 30 Sample Conversation Tests across Urdu, Roman Urdu & English")
    print("=" * 70)

    customer = {"id": 999, "phone": "+923009998877", "name": "Seed Tester", "bot_active": True}

    for idx, question in enumerate(SAMPLE_QUESTIONS, start=1):
        print(f"\n--- [Test #{idx}] Customer: {question}")
        try:
            reply = await run_agent(customer, question)
            print(f"🤖 Bot Reply:\n{reply}")
        except Exception as e:
            print(f"❌ Error executing test #{idx}: {e}")

    print("\n" + "=" * 70)
    print("✅ Sample conversation test run completed!")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(run_seed_tests())
