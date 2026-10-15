import logging
from typing import Any, Dict, List, Optional
from app import db
from app.config import settings
from app.odoo_client import OdooClient
from app.whatsapp import send_text
from app.email_client import send_email

logger = logging.getLogger(__name__)


async def notify_staff(
    customer: Dict[str, Any],
    reason: str,
    recent_messages: List[Dict[str, str]],
    odoo_client: Optional[OdooClient] = None,
) -> bool:
    """
    Alert staff via WhatsApp on STAFF_ALERT_PHONE with customer details, handoff reason,
    and a short conversation summary. Also optionally logs a note on Odoo partner chatter.
    """
    staff_phone = settings.staff_alert_phone
    c_phone = customer.get("phone", "Unknown")
    c_name = customer.get("name") or "Valued Customer"

    # Generate 3-line summary from recent messages
    summary_lines = []
    for msg in recent_messages[-3:]:
        role_label = "Cust" if msg.get("role") == "user" else "Bot"
        summary_lines.append(f"- {role_label}: {msg.get('content', '')[:60]}")
    summary_text = "\n".join(summary_lines) if summary_lines else "No recent messages."

    alert_message = (
        f"🚨 *HUMAN HANDOFF ALERT*\n\n"
        f"👤 *Customer*: {c_name}\n"
        f"📞 *Phone*: {c_phone}\n"
        f"⚠️ *Reason*: {reason}\n\n"
        f"📝 *Recent Chat Summary*:\n{summary_text}\n\n"
        f"Bot has been paused for this customer. Please contact them directly."
    )

    logger.info("Notifying staff (%s) of handoff for customer %s", staff_phone, c_phone)
    success = await send_text(staff_phone, alert_message)

    # Log note to Odoo chatter if partner exists
    odoo_partner_id = customer.get("odoo_partner_id")
    if odoo_partner_id:
        client = odoo_client or OdooClient()
        try:
            chatter_body = (
                f"<p><b>WhatsApp Human Handoff Triggered</b></p>"
                f"<p><b>Reason:</b> {reason}</p>"
                f"<p><b>Recent Messages:</b><br/>{summary_text.replace(chr(10), '<br/>')}</p>"
            )
            await client.execute_kw_async(
                "res.partner",
                "message_post",
                [odoo_partner_id],
                {"body": chatter_body, "message_type": "comment"},
            )
            logger.info("Posted handoff note to Odoo partner chatter (ID: %s)", odoo_partner_id)
        except Exception as e:
            logger.warning("Failed to post note to Odoo chatter: %s", str(e))

    return success


async def notify_staff_by_email(
    customer: Dict[str, Any],
    reason: str,
    recent_messages: List[Dict[str, str]],
) -> bool:
    """Email the staff alert address, when one is configured, for email-channel handoffs."""
    staff_email = settings.staff_alert_email
    if not staff_email:
        return False

    summary_lines = [
        f"- {'Cust' if m.get('role') == 'user' else 'Bot'}: {m.get('content', '')[:80]}"
        for m in recent_messages[-3:]
    ]
    summary_text = "\n".join(summary_lines) if summary_lines else "No recent messages."

    body = (
        "HUMAN HANDOFF ALERT\n\n"
        f"Customer: {customer.get('name') or 'Valued Customer'}\n"
        f"Email: {customer.get('email') or 'Unknown'}\n"
        f"Phone: {customer.get('phone') or 'Not provided'}\n"
        f"Reason: {reason}\n\n"
        f"Recent conversation:\n{summary_text}\n\n"
        "The bot has been paused for this customer. Please contact them directly."
    )

    logger.info("Emailing staff (%s) about handoff for %s", staff_email, customer.get("email"))
    return await send_email(staff_email, "Human handoff required", body)


async def pause_bot_for_customer(phone: str) -> Dict[str, Any]:
    """Admin helper: Pause bot for customer by phone number."""
    cust = await db.get_or_create_customer(phone)
    await db.set_bot_active(cust["id"], False)
    logger.info("Admin paused bot for customer %s", phone)
    return {"status": "success", "message": f"Bot paused for customer {phone}"}


async def resume_bot_for_customer(phone: str) -> Dict[str, Any]:
    """Admin helper: Resume bot for customer by phone number."""
    cust = await db.get_or_create_customer(phone)
    await db.set_bot_active(cust["id"], True)
    logger.info("Admin resumed bot for customer %s", phone)
    return {"status": "success", "message": f"Bot resumed for customer {phone}"}


async def pause_bot_for_email(email: str) -> Dict[str, Any]:
    """Admin helper: Pause bot for a customer identified by email address."""
    cust = await db.get_or_create_customer_by_email(email)
    await db.set_bot_active(cust["id"], False)
    logger.info("Admin paused bot for email customer %s", email)
    return {"status": "success", "message": f"Bot paused for customer {email}"}


async def resume_bot_for_email(email: str) -> Dict[str, Any]:
    """Admin helper: Resume bot for a customer identified by email address."""
    cust = await db.get_or_create_customer_by_email(email)
    await db.set_bot_active(cust["id"], True)
    logger.info("Admin resumed bot for email customer %s", email)
    return {"status": "success", "message": f"Bot resumed for customer {email}"}
