"""
Check the Resend email agent configuration and say exactly what is missing.

    python scripts/check_email_config.py

Reads .env through app.config, calls the Resend API read-only, and never
prints a secret: keys are shown only as a length and a short fingerprint.
"""
import asyncio
import hashlib
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx

from app.config import settings
from app.email_client import RESEND_API_URL, split_address

OK, BAD, WARN = "  OK  ", " FAIL ", " WARN "


def fingerprint(value: str) -> str:
    return f"{len(value)} chars, fp {hashlib.sha256(value.encode()).hexdigest()[:8]}"


def line(status: str, name: str, detail: str) -> None:
    print(f"[{status}] {name:<22} {detail}")


async def main() -> int:
    print("Resend email agent configuration\n" + "-" * 70)
    failures = 0

    # 1. EMAIL_AGENT_ENABLED
    if settings.email_agent_enabled:
        line(OK, "EMAIL_AGENT_ENABLED", "true - /webhook/email is active")
    else:
        line(WARN, "EMAIL_AGENT_ENABLED", "false - /webhook/email returns 404 until set to true")

    # 2. RESEND_API_KEY
    key = settings.resend_api_key
    if not key:
        line(BAD, "RESEND_API_KEY", "missing")
        failures += 1
    else:
        line(OK, "RESEND_API_KEY", f"set ({fingerprint(key)})")

    # 3. RESEND_WEBHOOK_SECRET
    secret = settings.resend_webhook_secret
    if not secret:
        line(BAD, "RESEND_WEBHOOK_SECRET", "missing - ANY request would be accepted unverified")
        failures += 1
    elif not secret.startswith("whsec_"):
        line(WARN, "RESEND_WEBHOOK_SECRET", "does not start with whsec_ - is this the signing secret?")
    else:
        import base64
        try:
            decoded = base64.b64decode(secret.split("_", 1)[1])
            line(OK, "RESEND_WEBHOOK_SECRET", f"valid ({len(decoded)} bytes)")
        except Exception:
            line(BAD, "RESEND_WEBHOOK_SECRET", "not valid base64")
            failures += 1

    # 4. RESEND_FROM_EMAIL
    name, sender = split_address(settings.resend_from_email)
    domain = sender.split("@")[-1] if "@" in sender else ""
    if not sender:
        line(BAD, "RESEND_FROM_EMAIL", "missing - nothing can be sent")
        failures += 1
    elif domain in ("gmail.com", "outlook.com", "hotmail.com", "yahoo.com", "icloud.com"):
        line(BAD, "RESEND_FROM_EMAIL", f"{sender} - a public mailbox domain can never be verified in Resend")
        failures += 1
    else:
        line(OK, "RESEND_FROM_EMAIL", f"{sender}" + (f" (display name: {name})" if name else ""))

    # 5. RESEND_REPLY_TO (optional)
    if settings.resend_reply_to:
        line(OK, "RESEND_REPLY_TO", settings.resend_reply_to)
    else:
        line(OK, "RESEND_REPLY_TO", "empty - replies go to RESEND_FROM_EMAIL (fine)")

    # 6. STAFF_ALERT_EMAIL (optional)
    if settings.staff_alert_email:
        line(OK, "STAFF_ALERT_EMAIL", settings.staff_alert_email)
    else:
        line(WARN, "STAFF_ALERT_EMAIL", "empty - handoff alerts go to WhatsApp only")

    if not key:
        print("-" * 70)
        print("Cannot query Resend without an API key.")
        return 1

    # --- Live checks against the Resend API ---
    print("-" * 70)
    headers = {"Authorization": f"Bearer {key}"}
    async with httpx.AsyncClient(timeout=20) as client:
        res = await client.get(f"{RESEND_API_URL}/domains", headers=headers)
        if res.status_code == 401 and "restricted" in res.text:
            line(BAD, "API key permission", "send-only. Inbound email cannot be read. Create a Full access key.")
            failures += 1
        elif res.status_code >= 400:
            line(BAD, "API key permission", f"{res.status_code} {res.text[:80]}")
            failures += 1
        else:
            rows = res.json().get("data", [])
            line(OK, "API key permission", f"full access ({len(rows)} domain(s) visible)")
            match = [d for d in rows if d.get("name") == domain]
            if not match:
                names = ", ".join(d.get("name", "?") for d in rows) or "none"
                line(BAD, "Domain", f"'{domain}' is not in this account. Found: {names}")
                failures += 1
            else:
                d = match[0]
                status = d.get("status")
                if status == "verified":
                    line(OK, "Domain", f"{domain} is verified")
                else:
                    line(BAD, "Domain", f"{domain} status is '{status}' - finish DNS verification")
                    failures += 1

    print("-" * 70)
    print("All checks passed." if failures == 0 else f"{failures} problem(s) to fix.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
