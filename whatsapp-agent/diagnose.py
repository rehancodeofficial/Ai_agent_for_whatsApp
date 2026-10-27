"""
WhatsApp Agent - Full Diagnostic Script
Run: python diagnose.py   (from whatsapp-agent/ directory)
"""
import os, sys, asyncio

OK   = "\033[92m✅"
FAIL = "\033[91m❌"
WARN = "\033[93m⚠️ "
INFO = "\033[94mℹ️ "
RST  = "\033[0m"

def ok(msg):   print(f"{OK}  {msg}{RST}")
def fail(msg): print(f"{FAIL}  {msg}{RST}")
def warn(msg): print(f"{WARN}  {msg}{RST}")
def info(msg): print(f"{INFO}  {msg}{RST}")

print("\n" + "="*60)
print("  WhatsApp Agent - Full Diagnostic")
print("="*60 + "\n")

# STEP 1 - .env file
print("── STEP 1: .env file ────────────────────────────────────")
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
if os.path.exists(env_path):
    ok(f".env file found")
else:
    fail(".env NOT FOUND — copy .env.example to .env and fill your keys!")

try:
    from dotenv import load_dotenv
    load_dotenv(env_path)
    ok("dotenv loaded")
except Exception as e:
    warn(f"dotenv not available: {e}")

# STEP 2 - Settings
print("\n── STEP 2: Settings / API Keys ──────────────────────────")
try:
    from app.config import settings
    ok(f"Settings loaded | ENV={settings.env}")

    placeholder_values = {
        "your_gemini_api_key_here", "your_whatsapp_token_here",
        "your_phone_number_id_here", "your_app_secret_here", ""
    }

    for key, val in [
        ("GEMINI_API_KEY",       settings.gemini_api_key),
        ("GEMINI_MODEL",         settings.gemini_model),
        ("WA_TOKEN",             settings.wa_token),
        ("WA_PHONE_NUMBER_ID",   settings.wa_phone_number_id),
        ("WA_APP_SECRET",        settings.wa_app_secret),
        ("WA_VERIFY_TOKEN",      settings.wa_verify_token),
    ]:
        if val in placeholder_values:
            fail(f"{key} = NOT SET ← fill this in .env")
        else:
            masked = val[:6] + "***" if len(val) > 6 else "***"
            ok(f"{key} = {masked}")
except Exception as e:
    fail(f"Settings error: {e}")
    sys.exit(1)

# STEP 3 - Knowledge Base
print("\n── STEP 3: Knowledge Base ───────────────────────────────")
try:
    from app.prompts import load_knowledge_base, build_system_prompt
    kb = load_knowledge_base()
    if "No additional" in kb:
        fail("knowledge.md NOT loaded - file not found")
    else:
        ok(f"knowledge.md loaded ({len(kb)} chars)")
    prompt = build_system_prompt(settings.business_name)
    ok(f"System prompt built ({len(prompt)} chars)")
    if "RESPONSE WORKFLOW" in prompt:
        ok("Response workflow instructions present")
except Exception as e:
    fail(f"Prompts error: {e}")

# STEP 4 - Gemini API Live Test
print("\n── STEP 4: Gemini API (live call) ───────────────────────")
async def test_gemini():
    try:
        from app.llm import GeminiLLMClient
        client = GeminiLLMClient()
        info(f"Model: {client.model}")
        resp = await asyncio.wait_for(
            client.chat(
                system="Reply with exactly: DIAG_OK",
                messages=[{"role": "user", "content": "Say DIAG_OK"}],
                tools=[], max_tokens=30,
            ),
            timeout=20.0,
        )
        if resp.text:
            ok(f"Gemini API working! Response: '{resp.text.strip()}'")
        else:
            fail("Gemini API returned EMPTY response — check API key or model name")
    except asyncio.TimeoutError:
        fail("Gemini API TIMEOUT — network or wrong API key")
    except Exception as e:
        fail(f"Gemini API error: {type(e).__name__}: {e}")
asyncio.run(test_gemini())

# STEP 5 - Product Search
print("\n── STEP 5: Product Search ───────────────────────────────")
async def test_search():
    try:
        from app.tools import ToolRegistry
        reg = ToolRegistry()
        for q in ["iphone 15", "samsung", "mobile chahiye"]:
            r = await reg.search_product(q)
            c = r.get("count", 0)
            if c > 0:
                ok(f"'{q}' → {c} products found")
            else:
                warn(f"'{q}' → 0 results")
    except Exception as e:
        fail(f"Search error: {e}")
asyncio.run(test_search())

# STEP 6 - WhatsApp Config
print("\n── STEP 6: WhatsApp Config ──────────────────────────────")
if settings.wa_token and settings.wa_phone_number_id:
    ok("WA_TOKEN set")
    ok(f"WA_PHONE_NUMBER_ID = {settings.wa_phone_number_id}")
    if settings.wa_app_secret:
        ok("WA_APP_SECRET set (signature verification active)")
    else:
        warn("WA_APP_SECRET not set — signature verification SKIPPED (dev mode OK)")
else:
    fail("WA_TOKEN or WA_PHONE_NUMBER_ID missing — messages CANNOT be sent!")

# STEP 7 - Database
print("\n── STEP 7: Database ─────────────────────────────────────")
async def test_db():
    try:
        from app import db
        await asyncio.wait_for(db.init_db_pool(), timeout=4.0)
        cust = await db.get_or_create_customer("+923099999999", "Diag Test")
        ok(f"DB working — customer ID={cust['id']}")
        await db.close_db_pool()
    except asyncio.TimeoutError:
        warn("DB timeout — not running (agent uses in-memory fallback, OK for testing)")
    except Exception as e:
        warn(f"DB unavailable: {type(e).__name__} — in-memory fallback will be used")
asyncio.run(test_db())

# STEP 8 - Full Agent E2E
print("\n── STEP 8: Full Agent End-to-End ────────────────────────")
async def test_agent():
    try:
        from app.agent import run_agent
        from unittest.mock import patch, AsyncMock
        fake_customer = {"id": 1, "phone": "+923001234567", "name": "Test User", "bot_active": True}
        with patch("app.db.save_message", new_callable=AsyncMock, return_value=True), \
             patch("app.db.is_bot_active", new_callable=AsyncMock, return_value=True), \
             patch("app.db.get_recent_messages", new_callable=AsyncMock, return_value=[]):
            reply = await asyncio.wait_for(
                run_agent(fake_customer, "iPhone 15 ki price kya hai?"),
                timeout=30.0,
            )
        if reply and len(reply) > 10:
            ok(f"Agent replied! ({len(reply)} chars)")
            print(f"\n   AGENT REPLY:\n   {'─'*50}")
            for line in reply.strip().split("\n")[:10]:
                print(f"   {line}")
            print(f"   {'─'*50}\n")
        elif reply == "":
            warn("Agent returned empty string — check DB dedup or bot_active flag")
        else:
            fail(f"Short reply: '{reply}'")
    except asyncio.TimeoutError:
        fail("Agent timed out — Gemini API issue")
    except Exception as e:
        fail(f"Agent error: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
asyncio.run(test_agent())

print("\n" + "="*60)
print("  Diagnostic Complete!")
print("="*60)
print("""
FIXES:
 1. No .env?  →  cp .env.example .env  then fill real values
 2. Gemini fail?  →  check GEMINI_API_KEY at aistudio.google.com
 3. WA fail?  →  check WA_TOKEN & WA_PHONE_NUMBER_ID in Meta Console
 4. DB fail?  →  OK for local testing (in-memory fallback used)
""")
