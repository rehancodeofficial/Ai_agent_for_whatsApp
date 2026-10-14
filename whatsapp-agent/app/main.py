import time
import logging
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional
from fastapi import FastAPI, Request, Response, BackgroundTasks, HTTPException, Header, Depends, status
from starlette.background import BackgroundTask
from fastapi.responses import PlainTextResponse, JSONResponse

from app.config import settings
from app import db
from app.agent import run_agent
from app.whatsapp import verify_signature, parse_incoming, mark_as_read, send_text
from app.handoff import pause_bot_for_customer, resume_bot_for_customer

# settings.log_level was previously defined but never applied, so the app's own
# INFO logs were swallowed by the root logger's default WARNING level.
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

logger = logging.getLogger(__name__)

# Simple in-memory rate limiting tracker: {phone: [timestamp1, timestamp2]}
RATE_LIMIT_TRACKER: Dict[str, List[float]] = {}
MAX_MESSAGES_PER_MINUTE = 20


def is_rate_limited(phone: str) -> bool:
    """Rate limit per phone number to prevent spam and cost spikes (max 20 msgs/min)."""
    now = time.time()
    timestamps = RATE_LIMIT_TRACKER.get(phone, [])
    # Keep timestamps within the last 60 seconds
    valid_timestamps = [ts for ts in timestamps if now - ts < 60]
    RATE_LIMIT_TRACKER[phone] = valid_timestamps

    if len(valid_timestamps) >= MAX_MESSAGES_PER_MINUTE:
        return True

    valid_timestamps.append(now)
    return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifecycle manager to handle DB pool initialization and teardown."""
    logger.info("Starting WhatsApp AI Agent FastAPI application")
    settings.validate_required_settings()
    try:
        await db.init_db_pool()
    except Exception as e:
        logger.warning("Could not initialize DB pool at startup (may be running in test mode): %s", str(e))
    yield
    logger.info("Shutting down FastAPI application")
    await db.close_db_pool()


app = FastAPI(
    title="WhatsApp AI Customer Support Agent",
    version="1.0.0",
    lifespan=lifespan,
)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "ok", "service": "whatsapp-ai-agent", "environment": settings.env}


@app.get("/webhook")
async def verify_webhook(request: Request):
    """Meta WhatsApp Webhook verification endpoint (GET)."""
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode and token:
        if mode == "subscribe" and token == settings.wa_verify_token:
            logger.info("Meta Webhook verification succeeded!")
            return PlainTextResponse(content=challenge, status_code=200)
        else:
            logger.error("Webhook verification failed: token mismatch")
            raise HTTPException(status_code=403, detail="Verification token mismatch")
    raise HTTPException(status_code=400, detail="Missing verification parameters")


async def process_incoming_message(payload: Dict[str, Any]):
    """
    Background task entry point. Never raises: a background task that blows up
    would silently drop the customer's message with no visible error.
    """
    try:
        await _handle_incoming_message(payload)
    except Exception:
        logger.exception("Unhandled error while processing incoming WhatsApp message")


async def _handle_incoming_message(payload: Dict[str, Any]):
    """Incoming WhatsApp message processing."""
    parsed = parse_incoming(payload)
    if not parsed:
        return

    phone, name, wa_message_id, msg_type, text = parsed
    logger.info("Incoming '%s' message %s from %s", msg_type, wa_message_id, phone)

    # 1. Rate limiting check
    if is_rate_limited(phone):
        logger.warning("Rate limit exceeded for phone %s. Dropping message.", phone)
        await send_text(phone, "Aap bohot ziada messages bhej rahe hain. Bara-e-karam thora waqfa kijiye.")
        return

    # 2. Get or create customer in DB
    customer = await db.get_or_create_customer(phone, name)
    customer_id = customer["id"]

    # 3. Check if bot is active for this customer
    if not await db.is_bot_active(customer_id):
        logger.info("Bot is disabled for customer %s (%s). Message ignored.", customer_id, phone)
        return

    # 4. Handle non-text messages
    if msg_type != "text" and not (msg_type == "interactive" and text):
        logger.info("Received non-text message (%s) from %s", msg_type, phone)
        await db.save_message(customer_id, "user", f"[{msg_type} message]", wa_message_id=wa_message_id)
        non_text_reply = (
            "Aap ka shukriya! Filhal hum sirf text messages process kar sakte hain. "
            "Bara-e-karam apna sawal text mein likh kar bhejiye."
        )
        await send_text(phone, non_text_reply)
        await db.save_message(customer_id, "assistant", non_text_reply)
        return

    # 5. Mark message as read
    if wa_message_id:
        await mark_as_read(wa_message_id)

    # 6. Execute Claude AI Agent loop
    reply = await run_agent(customer, text, wa_message_id=wa_message_id)

    # 7. Send reply back to user
    if not reply:
        logger.info("Agent produced no reply for %s. Nothing sent.", phone)
        return
    if not await send_text(phone, reply):
        logger.error("Agent reply could not be delivered to %s", phone)


@app.post("/webhook")
async def webhook_handler(request: Request):
    """Meta WhatsApp Webhook handler endpoint (POST). Returns 200 immediately."""
    raw_body = await request.body()
    signature_header = request.headers.get("X-Hub-Signature-256")

    # Verify Meta HMAC signature
    if not verify_signature(raw_body, signature_header):
        raise HTTPException(status_code=403, detail="Invalid signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # Return 200 OK immediately to Meta, process in BackgroundTask
    logger.info("Webhook POST accepted (%d bytes)", len(raw_body))
    return JSONResponse(
        status_code=200,
        content={"status": "received"},
        background=BackgroundTask(process_incoming_message, payload),
    )


# --- Admin Endpoints ---

def verify_admin_token(authorization: Optional[str] = Header(None)):
    """Dependency to check admin bearer token."""
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = authorization.replace("Bearer ", "").strip()
    if token != settings.admin_token:
        raise HTTPException(status_code=403, detail="Invalid Admin token")


@app.post("/admin/pause/{phone}", dependencies=[Depends(verify_admin_token)])
async def admin_pause_bot(phone: str):
    """Admin endpoint to pause bot for a customer."""
    res = await pause_bot_for_customer(phone)
    return res


@app.post("/admin/resume/{phone}", dependencies=[Depends(verify_admin_token)])
async def admin_resume_bot(phone: str):
    """Admin endpoint to resume bot for a customer."""
    res = await resume_bot_for_customer(phone)
    return res
