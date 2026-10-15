import base64
import hashlib
import hmac
import html as html_module
import logging
import re
import time
from email.utils import parseaddr
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com"

# Svix tolerates a 5 minute clock skew between sender and receiver.
WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS = 5 * 60

# Keep the model's context (and the bill) down on long reply chains.
MAX_EMAIL_BODY_CHARS = 8000


def verify_webhook_signature(raw_body: bytes, headers: Dict[str, str]) -> bool:
    """
    Verify a Resend inbound webhook, which is signed Svix-style:
    HMAC-SHA256 over "{svix-id}.{svix-timestamp}.{body}" with the base64-decoded
    secret, compared against one of the space-separated "v1,<sig>" entries.
    """
    secret = settings.resend_webhook_secret
    if not secret:
        logger.warning("RESEND_WEBHOOK_SECRET is not configured. Skipping verification in dev mode.")
        return True

    lower = {k.lower(): v for k, v in headers.items()}
    # Svix sends these as svix-* and, for some plans, as webhook-*.
    msg_id = lower.get("svix-id") or lower.get("webhook-id")
    timestamp = lower.get("svix-timestamp") or lower.get("webhook-timestamp")
    signature_header = lower.get("svix-signature") or lower.get("webhook-signature")

    if not (msg_id and timestamp and signature_header):
        logger.error("Missing Svix signature headers on inbound email webhook")
        return False

    try:
        sent_at = int(timestamp)
    except ValueError:
        logger.error("Invalid svix-timestamp header: %s", timestamp)
        return False

    if abs(time.time() - sent_at) > WEBHOOK_TIMESTAMP_TOLERANCE_SECONDS:
        logger.error("Inbound email webhook timestamp outside tolerance (replay protection)")
        return False

    try:
        key = base64.b64decode(secret.split("_", 1)[1] if secret.startswith("whsec_") else secret)
    except Exception:
        logger.error("RESEND_WEBHOOK_SECRET is not valid base64")
        return False

    signed_content = b"%s.%s.%s" % (msg_id.encode("utf-8"), timestamp.encode("utf-8"), raw_body)
    expected = base64.b64encode(hmac.new(key, signed_content, hashlib.sha256).digest()).decode("utf-8")

    for entry in signature_header.split(" "):
        _, _, candidate = entry.partition(",")
        if candidate and hmac.compare_digest(expected, candidate):
            return True

    logger.error("Inbound email webhook signature mismatch")
    return False


def split_address(address: str) -> tuple:
    """Split 'Ahmed Raza <a@b.com>' into ('Ahmed Raza', 'a@b.com')."""
    name, email_address = parseaddr(address or "")
    return name.strip(), email_address.strip().lower()


def html_to_text(html: str) -> str:
    """Crude HTML to text, used only when an email carries no plain text part."""
    if not html:
        return ""
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    text = html_module.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


# Quoted history markers: Gmail/Outlook style attributions and signature separators.
QUOTED_REPLY_PATTERNS = (
    re.compile(r"(?im)^\s*on .{0,120}\bwrote:\s*$"),
    re.compile(r"(?im)^\s*-{2,}\s*original message\s*-{2,}\s*$"),
    re.compile(r"(?im)^\s*_{5,}\s*$"),
    re.compile(r"(?im)^\s*from:\s.+\bsent:\s"),
)


def strip_quoted_reply(text: str) -> str:
    """Drop the quoted history so only what the customer just wrote reaches the model."""
    if not text:
        return ""

    cut = len(text)
    for pattern in QUOTED_REPLY_PATTERNS:
        match = pattern.search(text)
        if match and match.start() < cut:
            cut = match.start()

    body = text[:cut]
    # Anything still prefixed with ">" is quoted material.
    lines = [line for line in body.splitlines() if not line.lstrip().startswith(">")]
    return "\n".join(lines).strip()


def parse_inbound(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Parse an 'email.received' webhook. The payload carries metadata only,
    so the body has to be fetched separately with fetch_received_email().
    """
    try:
        if payload.get("type") != "email.received":
            logger.info("Ignoring Resend webhook of type %s", payload.get("type"))
            return None

        data = payload.get("data") or {}
        email_id = data.get("email_id")
        if not email_id:
            logger.warning("Inbound email webhook has no email_id")
            return None

        sender_name, sender_email = split_address(data.get("from", ""))
        if not sender_email:
            logger.warning("Inbound email %s has no usable sender address", email_id)
            return None

        return {
            "email_id": email_id,
            "from_name": sender_name,
            "from_email": sender_email,
            "to": data.get("to") or [],
            "subject": (data.get("subject") or "").strip(),
            "attachments": data.get("attachments") or [],
        }
    except (AttributeError, KeyError, TypeError) as e:
        logger.warning("Could not parse inbound email payload: %s", str(e))
        return None


async def fetch_received_email(email_id: str, timeout: int = 20) -> Optional[Dict[str, Any]]:
    """Fetch a received email's body and headers from the Resend receiving API."""
    api_key = settings.resend_api_key
    if not api_key:
        logger.warning("RESEND_API_KEY not set. Cannot fetch received email %s.", email_id)
        return None

    url = f"{RESEND_API_URL}/emails/receiving/{email_id}"
    headers = {"Authorization": f"Bearer {api_key}"}

    try:
        async with httpx.AsyncClient(timeout=timeout) as http_client:
            res = await http_client.get(url, headers=headers)
            if res.status_code >= 400:
                logger.error("Could not fetch received email %s. Status: %s, Body: %s", email_id, res.status_code, res.text)
                return None
            return res.json()
    except Exception as e:
        logger.error("Error fetching received email %s: %s", email_id, str(e))
        return None


def extract_body(email: Dict[str, Any]) -> str:
    """Plain text of a received email, quoted history removed and length capped."""
    text = (email.get("text") or "").strip() or html_to_text(email.get("html") or "")
    body = strip_quoted_reply(text) or text.strip()
    if len(body) > MAX_EMAIL_BODY_CHARS:
        logger.info("Truncating long email body from %d chars", len(body))
        body = body[:MAX_EMAIL_BODY_CHARS] + "\n\n[...]"
    return body


def get_header(email: Dict[str, Any], name: str) -> str:
    """Read one header. Resend may return headers as a dict or as a list of {name, value}."""
    headers = email.get("headers")
    wanted = name.lower()

    if isinstance(headers, dict):
        for key, value in headers.items():
            if str(key).lower() == wanted:
                return str(value)
    elif isinstance(headers, list):
        for item in headers:
            if isinstance(item, dict) and str(item.get("name", "")).lower() == wanted:
                return str(item.get("value", ""))
    return ""


# Headers that mark a message as machine-generated. Replying to one risks a mail loop.
AUTO_REPLY_HEADERS = ("auto-submitted", "x-autoreply", "x-autorespond", "list-id", "list-unsubscribe")
BULK_PRECEDENCE = ("bulk", "junk", "auto_reply", "list")


def is_auto_reply(email: Dict[str, Any]) -> bool:
    """
    Whether this looks like an out-of-office, bounce, newsletter or other
    machine-generated mail. Answering one can bounce back and forth forever.
    """
    for header in AUTO_REPLY_HEADERS:
        value = get_header(email, header).strip().lower()
        if header == "auto-submitted":
            if value and value != "no":
                return True
        elif value:
            return True

    if get_header(email, "precedence").strip().lower() in BULK_PRECEDENCE:
        return True

    _, sender = split_address(email.get("from", ""))
    local_part = sender.split("@")[0] if "@" in sender else ""
    if local_part in ("mailer-daemon", "postmaster", "no-reply", "noreply", "donotreply"):
        return True

    return False


def is_own_address(address: str) -> bool:
    """Whether an address is the agent's own sender, so it never replies to itself."""
    _, sender = split_address(settings.resend_from_email)
    _, other = split_address(address)
    return bool(sender) and sender == other


def build_reply_subject(subject: str) -> str:
    """Prefix with Re: unless the subject already carries one."""
    subject = (subject or "").strip() or "Your enquiry"
    if re.match(r"(?i)^re\s*:", subject):
        return subject
    return f"Re: {subject}"


def text_to_html(text: str) -> str:
    """Wrap a plain text reply in minimal HTML so it renders properly in mail clients."""
    escaped = html_module.escape(text or "")
    paragraphs = [p.replace("\n", "<br>") for p in escaped.split("\n\n") if p.strip()]
    return "".join(f"<p>{p}</p>" for p in paragraphs) or "<p></p>"


async def send_email(
    to_email: str,
    subject: str,
    body_text: str,
    in_reply_to: Optional[str] = None,
    timeout: int = 20,
) -> bool:
    """
    Send an email through the Resend API. When in_reply_to is given, the
    In-Reply-To/References headers keep the reply in the customer's thread.
    """
    if not body_text:
        return False

    api_key = settings.resend_api_key
    sender = settings.resend_from_email
    if not api_key or not sender:
        logger.warning(
            "RESEND_API_KEY or RESEND_FROM_EMAIL not set. Logging email instead: %s -> %s",
            to_email, subject,
        )
        return True

    payload: Dict[str, Any] = {
        "from": sender,
        "to": [to_email],
        "subject": subject,
        "text": body_text,
        "html": text_to_html(body_text),
    }
    if settings.resend_reply_to:
        payload["reply_to"] = settings.resend_reply_to
    if in_reply_to:
        payload["headers"] = {"In-Reply-To": in_reply_to, "References": in_reply_to}

    try:
        async with httpx.AsyncClient(timeout=timeout) as http_client:
            res = await http_client.post(
                f"{RESEND_API_URL}/emails",
                json=payload,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            )
            if res.status_code >= 400:
                logger.error("Failed to send email to %s. Status: %s, Body: %s", to_email, res.status_code, res.text)
                return False
            logger.info("Sent email to %s (id: %s)", to_email, res.json().get("id"))
            return True
    except Exception as e:
        logger.error("HTTP error sending email to %s: %s", to_email, str(e))
        return False
