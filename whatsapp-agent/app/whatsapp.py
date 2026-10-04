import hmac
import hashlib
import logging
from typing import Any, Dict, List, Optional, Tuple
import httpx

from app.config import settings

logger = logging.getLogger(__name__)

GRAPH_API_URL = "https://graph.facebook.com/v21.0"


def verify_signature(raw_body: bytes, signature_header: Optional[str], app_secret: Optional[str] = None) -> bool:
    """
    Verify Meta WhatsApp webhook signature (X-Hub-Signature-256).
    """
    secret = app_secret or settings.wa_app_secret
    if not secret:
        logger.warning("WA_APP_SECRET is not configured. Skipping signature verification in dev mode.")
        return True

    if not signature_header or not signature_header.startswith("sha256="):
        logger.error("Missing or invalid X-Hub-Signature-256 header")
        return False

    expected_hash = signature_header.split("sha256=")[1]
    computed_hash = hmac.new(
        secret.encode("utf-8"),
        raw_body,
        hashlib.sha256
    ).hexdigest()

    is_valid = hmac.compare_digest(expected_hash, computed_hash)
    if not is_valid:
        logger.error("WhatsApp signature mismatch! Expected: %s, Computed: %s", expected_hash, computed_hash)
    return is_valid


def split_message(text: str, max_length: int = 1500) -> List[str]:
    """Split text into chunks of at most max_length characters without breaking words if possible."""
    if len(text) <= max_length:
        return [text]

    chunks = []
    current_chunk = ""
    for line in text.split("\n"):
        if len(current_chunk) + len(line) + 1 <= max_length:
            current_chunk += (line + "\n")
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = line + "\n"
    if current_chunk:
        chunks.append(current_chunk.strip())
    return chunks


async def send_text(to_phone: str, text: str, timeout: int = 10) -> bool:
    """Send a text message via Meta WhatsApp Graph API."""
    if not text:
        return False

    token = settings.wa_token
    phone_id = settings.wa_phone_number_id
    if not token or not phone_id:
        logger.warning("WA_TOKEN or WA_PHONE_NUMBER_ID not set. Logging message instead: %s -> %s", to_phone, text)
        return True

    url = f"{GRAPH_API_URL}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    chunks = split_message(text, max_length=1500)
    success_all = True

    async with httpx.AsyncClient(timeout=timeout) as http_client:
        for chunk in chunks:
            payload = {
                "messaging_product": "whatsapp",
                "recipient_type": "individual",
                "to": to_phone,
                "type": "text",
                "text": {"body": chunk},
            }
            try:
                res = await http_client.post(url, json=payload, headers=headers)
                if res.status_code >= 400:
                    logger.error("Failed to send WhatsApp message to %s. Status: %s, Body: %s", to_phone, res.status_code, res.text)
                    success_all = False
                else:
                    logger.info("Successfully sent WhatsApp message chunk to %s", to_phone)
            except Exception as e:
                logger.error("HTTP error sending WhatsApp message: %s", str(e))
                success_all = False

    return success_all


async def mark_as_read(wa_message_id: str, timeout: int = 10) -> bool:
    """Mark incoming WhatsApp message as read."""
    token = settings.wa_token
    phone_id = settings.wa_phone_number_id
    if not token or not phone_id:
        return True

    url = f"{GRAPH_API_URL}/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "status": "read",
        "message_id": wa_message_id,
    }

    try:
        async with httpx.AsyncClient(timeout=timeout) as http_client:
            res = await http_client.post(url, json=payload, headers=headers)
            return res.status_code < 400
    except Exception as e:
        logger.error("Failed to mark message %s as read: %s", wa_message_id, str(e))
        return False


def parse_incoming(payload: Dict[str, Any]) -> Optional[Tuple[str, str, str, str, str]]:
    """
    Parse Meta webhook payload.
    Returns (phone, profile_name, wa_message_id, msg_type, text_content) or None if status/empty payload.
    """
    try:
        entry = payload.get("entry", [])[0]
        changes = entry.get("changes", [])[0]
        value = changes.get("value", {})

        messages = value.get("messages")
        if not messages:
            # Status update or empty payload
            return None

        msg = messages[0]
        wa_message_id = msg.get("id", "")
        msg_type = msg.get("type", "unknown")
        from_phone = msg.get("from", "")

        contacts = value.get("contacts", [])
        profile_name = "Customer"
        if contacts:
            profile_name = contacts[0].get("profile", {}).get("name", "Customer")

        text_content = ""
        if msg_type == "text":
            text_content = msg.get("text", {}).get("body", "").strip()
        elif msg_type == "interactive":
            interactive = msg.get("interactive", {})
            if interactive.get("type") == "button_reply":
                text_content = interactive.get("button_reply", {}).get("title", "")
            elif interactive.get("type") == "list_reply":
                text_content = interactive.get("list_reply", {}).get("title", "")
        else:
            text_content = f"[Media/Non-text message: {msg_type}]"

        if not from_phone:
            return None

        return (from_phone, profile_name, wa_message_id, msg_type, text_content)
    except (IndexError, KeyError, TypeError) as e:
        logger.warning("Could not parse WhatsApp payload: %s", str(e))
        return None
