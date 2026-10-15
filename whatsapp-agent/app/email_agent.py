import logging
from typing import Any, Dict

from app import db
from app.agent import run_agent
from app.email_client import (
    build_reply_subject,
    extract_body,
    fetch_received_email,
    get_header,
    is_auto_reply,
    is_own_address,
    parse_inbound,
    send_email,
)
from app.ratelimit import is_rate_limited

logger = logging.getLogger(__name__)

RATE_LIMIT_REPLY = (
    "We have received a lot of messages from this address in a short time.\n\n"
    "Please give us a few minutes before sending another email, and we will get back to you."
)

EMPTY_BODY_REPLY = (
    "Thank you for writing to us.\n\n"
    "We could not read any text in your email. Please reply with your question in the "
    "body of the message and we will help you right away."
)


async def process_incoming_email(payload: Dict[str, Any]) -> None:
    """
    Background task entry point for the Resend inbound webhook. Never raises,
    for the same reason as the WhatsApp handler: a failure here would silently
    drop the customer's email.
    """
    try:
        await _handle_incoming_email(payload)
    except Exception:
        logger.exception("Unhandled error while processing incoming email")


async def _handle_incoming_email(payload: Dict[str, Any]) -> None:
    """Inbound email processing, sharing the agent, tools and DB with WhatsApp."""
    parsed = parse_inbound(payload)
    if not parsed:
        return

    email_id = parsed["email_id"]
    from_email = parsed["from_email"]
    from_name = parsed["from_name"] or None
    subject = parsed["subject"]
    logger.info("Incoming email %s from %s (subject: %r)", email_id, from_email, subject)

    # 1. Never reply to ourselves: that is an immediate mail loop.
    if is_own_address(from_email):
        logger.info("Email %s is from our own sender address. Ignoring.", email_id)
        return

    # 2. Rate limiting, shared with the WhatsApp channel
    if is_rate_limited(from_email):
        logger.warning("Rate limit exceeded for %s. Dropping email.", from_email)
        return

    # 3. The webhook carries metadata only, so fetch the body
    email = await fetch_received_email(email_id)
    if not email:
        logger.error("Could not fetch body of email %s from %s", email_id, from_email)
        return

    # 4. Out-of-office replies, bounces and newsletters must not be answered
    if is_auto_reply(email):
        logger.info("Email %s looks machine-generated. Not replying.", email_id)
        return

    # 5. Get or create the customer, keyed by email address
    customer = await db.get_or_create_customer_by_email(from_email, from_name)
    customer_id = customer["id"]

    # 6. Respect the same human-handoff pause switch as WhatsApp
    if not await db.is_bot_active(customer_id):
        logger.info("Bot is disabled for customer %s (%s). Email ignored.", customer_id, from_email)
        return

    reply_subject = build_reply_subject(subject or email.get("subject", ""))
    # RFC Message-ID of the incoming mail, so our reply threads under it.
    in_reply_to = (email.get("message_id") or get_header(email, "message-id")).strip() or None

    body = extract_body(email)
    if not body:
        logger.info("Email %s has no readable body.", email_id)
        await db.save_message(customer_id, "user", "[empty email]", email_message_id=email_id)
        await send_email(from_email, reply_subject, EMPTY_BODY_REPLY, in_reply_to=in_reply_to)
        await db.save_message(customer_id, "assistant", EMPTY_BODY_REPLY)
        return

    # The subject carries real intent in email, so give it to the model.
    user_text = f"Subject: {subject}\n\n{body}" if subject else body

    # 7. Same agent loop, same Gemini model, same Odoo tools as WhatsApp
    reply = await run_agent(customer, user_text, channel="email", message_id=email_id)

    if not reply:
        logger.info("Agent produced no reply for email %s. Nothing sent.", email_id)
        return

    if not await send_email(from_email, reply_subject, reply, in_reply_to=in_reply_to):
        logger.error("Agent reply could not be delivered to %s", from_email)
