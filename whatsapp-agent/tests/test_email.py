import base64
import hashlib
import hmac
import time
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.email_client import (
    build_reply_subject,
    extract_body,
    fetch_received_email,
    get_header,
    html_to_text,
    is_auto_reply,
    is_own_address,
    parse_inbound,
    send_email,
    split_address,
    strip_quoted_reply,
    text_to_html,
    verify_webhook_signature,
)

SECRET_BYTES = b"0123456789abcdef0123456789abcdef"
WHSEC = "whsec_" + base64.b64encode(SECRET_BYTES).decode()


def sign(body: bytes, msg_id: str = "msg_1", timestamp: int = None) -> dict:
    timestamp = timestamp if timestamp is not None else int(time.time())
    signed = b"%s.%s.%s" % (msg_id.encode(), str(timestamp).encode(), body)
    sig = base64.b64encode(hmac.new(SECRET_BYTES, signed, hashlib.sha256).digest()).decode()
    return {"svix-id": msg_id, "svix-timestamp": str(timestamp), "svix-signature": f"v1,{sig}"}


def inbound_payload(**overrides) -> dict:
    data = {
        "email_id": "em_123",
        "from": "Ahmed Raza <Ahmed@Example.com>",
        "to": ["support@shop.com"],
        "subject": "Question about my order",
        "attachments": [],
    }
    data.update(overrides)
    return {"type": "email.received", "created_at": "2026-02-22T23:41:12.126Z", "data": data}


# --- Svix signature verification ---

def test_verify_signature_accepts_valid_signature():
    body = b'{"type":"email.received"}'
    with patch("app.email_client.settings") as s:
        s.resend_webhook_secret = WHSEC
        assert verify_webhook_signature(body, sign(body)) is True


def test_verify_signature_rejects_tampered_body():
    with patch("app.email_client.settings") as s:
        s.resend_webhook_secret = WHSEC
        headers = sign(b'{"type":"email.received"}')
        assert verify_webhook_signature(b'{"type":"evil"}', headers) is False


def test_verify_signature_rejects_missing_headers():
    with patch("app.email_client.settings") as s:
        s.resend_webhook_secret = WHSEC
        assert verify_webhook_signature(b"{}", {}) is False


def test_verify_signature_rejects_stale_timestamp():
    body = b"{}"
    with patch("app.email_client.settings") as s:
        s.resend_webhook_secret = WHSEC
        stale = sign(body, timestamp=int(time.time()) - 3600)
        assert verify_webhook_signature(body, stale) is False


def test_verify_signature_accepts_webhook_prefixed_headers():
    body = b"{}"
    with patch("app.email_client.settings") as s:
        s.resend_webhook_secret = WHSEC
        headers = {k.replace("svix-", "webhook-"): v for k, v in sign(body).items()}
        assert verify_webhook_signature(body, headers) is True


def test_verify_signature_picks_matching_signature_from_several():
    body = b"{}"
    with patch("app.email_client.settings") as s:
        s.resend_webhook_secret = WHSEC
        headers = sign(body)
        headers["svix-signature"] = "v1,bm90LXRoZS1vbmU= " + headers["svix-signature"]
        assert verify_webhook_signature(body, headers) is True


def test_verify_signature_skipped_without_secret():
    with patch("app.email_client.settings") as s:
        s.resend_webhook_secret = ""
        assert verify_webhook_signature(b"{}", {}) is True


# --- payload parsing ---

def test_parse_inbound_extracts_sender_and_subject():
    parsed = parse_inbound(inbound_payload())
    assert parsed["email_id"] == "em_123"
    assert parsed["from_email"] == "ahmed@example.com"
    assert parsed["from_name"] == "Ahmed Raza"
    assert parsed["subject"] == "Question about my order"


def test_parse_inbound_ignores_other_event_types():
    assert parse_inbound({"type": "email.delivered", "data": {"email_id": "em_1"}}) is None


def test_parse_inbound_requires_email_id():
    payload = inbound_payload()
    del payload["data"]["email_id"]
    assert parse_inbound(payload) is None


def test_parse_inbound_requires_sender():
    assert parse_inbound(inbound_payload(**{"from": ""})) is None


# --- body handling ---

def test_strip_quoted_reply_removes_history():
    text = "Shukriya!\n\nOn Mon, 3 Mar 2026, Support <s@shop.com> wrote:\n> Here is the price"
    assert strip_quoted_reply(text) == "Shukriya!"


def test_strip_quoted_reply_removes_outlook_original_message():
    text = "Thanks\n\n-----Original Message-----\nFrom: Support"
    assert strip_quoted_reply(text) == "Thanks"


def test_strip_quoted_reply_keeps_plain_text():
    assert strip_quoted_reply("Just a question") == "Just a question"


def test_html_to_text_strips_markup_and_scripts():
    out = html_to_text("<style>p{}</style><p>Hello <b>world</b></p><script>x()</script>")
    assert "Hello" in out and "world" in out
    assert "<" not in out and "x()" not in out


def test_extract_body_prefers_text_part():
    assert extract_body({"text": "plain body", "html": "<p>html body</p>"}) == "plain body"


def test_extract_body_falls_back_to_html():
    assert "html body" in extract_body({"text": "", "html": "<p>html body</p>"})


def test_extract_body_truncates_very_long_mail():
    body = extract_body({"text": "x" * 20000})
    assert len(body) < 20000 and body.endswith("[...]")


def test_build_reply_subject():
    assert build_reply_subject("Order 12") == "Re: Order 12"
    assert build_reply_subject("Re: Order 12") == "Re: Order 12"
    assert build_reply_subject("") == "Re: Your enquiry"


def test_text_to_html_escapes_and_paragraphs():
    out = text_to_html("Hello <you>\n\nSecond line")
    # HTML special characters must not leak into the markup.
    assert "&lt;you&gt;" in out and out.count("<p>") == 2


def test_split_address_lowercases_email():
    assert split_address("Ahmed <A@B.Com>") == ("Ahmed", "a@b.com")


# --- loop guards ---

def test_get_header_handles_dict_and_list_shapes():
    assert get_header({"headers": {"Message-ID": "<a@b>"}}, "message-id") == "<a@b>"
    assert get_header({"headers": [{"name": "Message-Id", "value": "<c@d>"}]}, "message-id") == "<c@d>"
    assert get_header({"headers": None}, "message-id") == ""


@pytest.mark.parametrize(
    "email,expected",
    [
        ({"headers": {"Auto-Submitted": "auto-replied"}, "from": "a@b.com"}, True),
        ({"headers": {"Auto-Submitted": "no"}, "from": "a@b.com"}, False),
        ({"headers": {"List-Id": "<news.x>"}, "from": "a@b.com"}, True),
        ({"headers": {"Precedence": "bulk"}, "from": "a@b.com"}, True),
        ({"headers": {}, "from": "no-reply@b.com"}, True),
        ({"headers": {}, "from": "Ahmed <ahmed@b.com>"}, False),
    ],
)
def test_is_auto_reply(email, expected):
    assert is_auto_reply(email) is expected


def test_is_own_address():
    with patch("app.email_client.settings") as s:
        s.resend_from_email = "Al-Madina <support@shop.com>"
        assert is_own_address("support@shop.com") is True
        assert is_own_address("customer@gmail.com") is False


# --- Resend API calls ---

def _client_returning(response):
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    client.post = AsyncMock(return_value=response)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


@pytest.mark.asyncio
async def test_fetch_received_email_returns_json():
    res = MagicMock(status_code=200)
    res.json.return_value = {"id": "em_123", "text": "hello"}
    ctx, client = _client_returning(res)

    with patch("app.email_client.settings") as s, patch("app.email_client.httpx.AsyncClient", return_value=ctx):
        s.resend_api_key = "re_key"
        out = await fetch_received_email("em_123")

    assert out == {"id": "em_123", "text": "hello"}
    assert client.get.call_args.args[0].endswith("/emails/receiving/em_123")
    assert client.get.call_args.kwargs["headers"]["Authorization"] == "Bearer re_key"


@pytest.mark.asyncio
async def test_fetch_received_email_handles_error():
    ctx, _ = _client_returning(MagicMock(status_code=404, text="nope"))
    with patch("app.email_client.settings") as s, patch("app.email_client.httpx.AsyncClient", return_value=ctx):
        s.resend_api_key = "re_key"
        assert await fetch_received_email("em_404") is None


@pytest.mark.asyncio
async def test_send_email_posts_expected_payload():
    res = MagicMock(status_code=200)
    res.json.return_value = {"id": "sent_1"}
    ctx, client = _client_returning(res)

    with patch("app.email_client.settings") as s, patch("app.email_client.httpx.AsyncClient", return_value=ctx):
        s.resend_api_key = "re_key"
        s.resend_from_email = "Al-Madina <support@shop.com>"
        s.resend_reply_to = "replies@shop.com"
        ok = await send_email("customer@gmail.com", "Re: Order", "Your order is ready.", in_reply_to="<abc@mail>")

    assert ok is True
    payload = client.post.call_args.kwargs["json"]
    assert payload["from"] == "Al-Madina <support@shop.com>"
    assert payload["to"] == ["customer@gmail.com"]
    assert payload["subject"] == "Re: Order"
    assert payload["text"] == "Your order is ready."
    assert payload["reply_to"] == "replies@shop.com"
    # Threading headers keep the reply inside the customer's conversation.
    assert payload["headers"] == {"In-Reply-To": "<abc@mail>", "References": "<abc@mail>"}


@pytest.mark.asyncio
async def test_send_email_reports_api_failure():
    ctx, _ = _client_returning(MagicMock(status_code=422, text="bad from"))
    with patch("app.email_client.settings") as s, patch("app.email_client.httpx.AsyncClient", return_value=ctx):
        s.resend_api_key = "re_key"
        s.resend_from_email = "support@shop.com"
        s.resend_reply_to = ""
        assert await send_email("c@gmail.com", "Re: x", "body") is False


@pytest.mark.asyncio
async def test_send_email_without_credentials_logs_instead_of_sending():
    with patch("app.email_client.settings") as s:
        s.resend_api_key = ""
        s.resend_from_email = ""
        assert await send_email("c@gmail.com", "Re: x", "body") is True


@pytest.mark.asyncio
async def test_send_email_refuses_empty_body():
    assert await send_email("c@gmail.com", "Re: x", "") is False


# --- inbound email flow ---

RECEIVED = {
    "id": "em_123",
    "from": "Ahmed Raza <ahmed@example.com>",
    "subject": "Question about my order",
    "text": "Salam, mera order kab aayega?",
    "html": "",
    "headers": {"Message-ID": "<orig@mail.example.com>"},
    "message_id": "<orig@mail.example.com>",
}

CUSTOMER = {"id": 9, "phone": None, "email": "ahmed@example.com", "name": "Ahmed Raza", "bot_active": True}


def flow_patches(**overrides):
    """Default happy-path patches for app.email_agent."""
    defaults = {
        "fetch_received_email": AsyncMock(return_value=dict(RECEIVED)),
        "get_or_create_customer_by_email": AsyncMock(return_value=dict(CUSTOMER)),
        "is_bot_active": AsyncMock(return_value=True),
        "save_message": AsyncMock(return_value=True),
        "run_agent": AsyncMock(return_value="Ji, aap ka order kal dispatch hoga."),
        "send_email": AsyncMock(return_value=True),
    }
    defaults.update(overrides)
    return defaults


async def run_flow(payload=None, **overrides):
    from app import email_agent
    from app.ratelimit import RATE_LIMIT_TRACKER

    RATE_LIMIT_TRACKER.clear()
    p = flow_patches(**overrides)
    with patch("app.email_agent.fetch_received_email", new=p["fetch_received_email"]), \
         patch("app.email_agent.db.get_or_create_customer_by_email", new=p["get_or_create_customer_by_email"]), \
         patch("app.email_agent.db.is_bot_active", new=p["is_bot_active"]), \
         patch("app.email_agent.db.save_message", new=p["save_message"]), \
         patch("app.email_agent.run_agent", new=p["run_agent"]), \
         patch("app.email_agent.send_email", new=p["send_email"]), \
         patch("app.email_agent.is_own_address", return_value=False):
        await email_agent._handle_incoming_email(payload or inbound_payload())
    return p


@pytest.mark.asyncio
async def test_inbound_email_gets_an_ai_reply():
    p = await run_flow()

    # The subject carries intent, so it reaches the model along with the body.
    user_text = p["run_agent"].call_args.args[1]
    assert "Question about my order" in user_text
    assert "mera order kab aayega" in user_text
    # Shared agent, email styling.
    assert p["run_agent"].call_args.kwargs["channel"] == "email"
    assert p["run_agent"].call_args.kwargs["message_id"] == "em_123"

    to, subject, body = p["send_email"].call_args.args[:3]
    assert to == "ahmed@example.com"
    assert subject == "Re: Question about my order"
    assert body == "Ji, aap ka order kal dispatch hoga."
    assert p["send_email"].call_args.kwargs["in_reply_to"] == "<orig@mail.example.com>"


@pytest.mark.asyncio
async def test_inbound_email_skips_auto_replies():
    ooo = dict(RECEIVED, headers={"Auto-Submitted": "auto-replied"})
    p = await run_flow(fetch_received_email=AsyncMock(return_value=ooo))
    p["run_agent"].assert_not_awaited()
    p["send_email"].assert_not_awaited()


@pytest.mark.asyncio
async def test_inbound_email_never_replies_to_itself():
    from app import email_agent
    from app.ratelimit import RATE_LIMIT_TRACKER

    RATE_LIMIT_TRACKER.clear()
    fetch = AsyncMock()
    with patch("app.email_agent.is_own_address", return_value=True), \
         patch("app.email_agent.fetch_received_email", new=fetch):
        await email_agent._handle_incoming_email(inbound_payload())
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_inbound_email_respects_paused_bot():
    p = await run_flow(is_bot_active=AsyncMock(return_value=False))
    p["run_agent"].assert_not_awaited()
    p["send_email"].assert_not_awaited()


@pytest.mark.asyncio
async def test_inbound_email_with_empty_body_asks_for_text():
    empty = dict(RECEIVED, text="", html="")
    p = await run_flow(fetch_received_email=AsyncMock(return_value=empty))
    p["run_agent"].assert_not_awaited()
    assert "could not read any text" in p["send_email"].call_args.args[2]


@pytest.mark.asyncio
async def test_inbound_email_stops_when_body_cannot_be_fetched():
    p = await run_flow(fetch_received_email=AsyncMock(return_value=None))
    p["run_agent"].assert_not_awaited()
    p["send_email"].assert_not_awaited()


@pytest.mark.asyncio
async def test_inbound_email_rate_limited():
    from app import email_agent

    p = flow_patches()
    with patch("app.email_agent.is_rate_limited", return_value=True), \
         patch("app.email_agent.is_own_address", return_value=False), \
         patch("app.email_agent.fetch_received_email", new=p["fetch_received_email"]):
        await email_agent._handle_incoming_email(inbound_payload())
    p["fetch_received_email"].assert_not_awaited()


@pytest.mark.asyncio
async def test_process_incoming_email_never_raises():
    from app import email_agent

    with patch("app.email_agent._handle_incoming_email", new=AsyncMock(side_effect=RuntimeError("boom"))):
        await email_agent.process_incoming_email(inbound_payload())


# --- webhook route ---

def test_email_webhook_returns_404_when_disabled():
    from fastapi.testclient import TestClient
    from app.main import app

    with patch("app.main.settings") as s:
        s.email_agent_enabled = False
        assert TestClient(app).post("/webhook/email", json={}).status_code == 404


def test_email_webhook_rejects_bad_signature():
    from fastapi.testclient import TestClient
    from app.main import app

    with patch("app.main.settings") as s, patch("app.main.verify_email_signature", return_value=False):
        s.email_agent_enabled = True
        assert TestClient(app).post("/webhook/email", json={}).status_code == 403


def test_email_webhook_accepts_signed_payload():
    from fastapi.testclient import TestClient
    from app.main import app

    with patch("app.main.settings") as s, \
         patch("app.main.verify_email_signature", return_value=True), \
         patch("app.main.process_incoming_email", new_callable=AsyncMock) as mock_process:
        s.email_agent_enabled = True
        res = TestClient(app).post("/webhook/email", json=inbound_payload())

    assert res.status_code == 200
    assert res.json()["status"] == "received"
    mock_process.assert_awaited_once()


def test_whatsapp_webhook_is_unaffected_by_the_email_agent():
    from fastapi.testclient import TestClient
    from app.main import app

    with patch("app.main.verify_signature", return_value=True), \
         patch("app.main.process_incoming_message", new_callable=AsyncMock):
        res = TestClient(app).post("/webhook", json={"object": "whatsapp_business_account", "entry": []})
    assert res.status_code == 200
