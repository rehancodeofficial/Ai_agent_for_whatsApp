import hmac
import hashlib
import pytest
from unittest.mock import AsyncMock, patch
from app.whatsapp import parse_incoming, verify_signature, split_message, send_text


def test_parse_incoming_valid_text():
    sample_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123456",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "15550228795", "phone_number_id": "100000000000000"},
                            "contacts": [{"profile": {"name": "Ahmed Raza"}, "wa_id": "923001234567"}],
                            "messages": [
                                {
                                    "from": "923001234567",
                                    "id": "wamid.HBgLMTExMTExMTExMTE=",
                                    "timestamp": "1700000000",
                                    "text": {"body": "Salam! Kya Cotton Shirt available hai?"},
                                    "type": "text",
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    parsed = parse_incoming(sample_payload)
    assert parsed is not None
    phone, name, wa_id, msg_type, text = parsed
    assert phone == "923001234567"
    assert name == "Ahmed Raza"
    assert wa_id == "wamid.HBgLMTExMTExMTExMTE="
    assert msg_type == "text"
    assert text == "Salam! Kya Cotton Shirt available hai?"


def test_parse_incoming_status_update():
    status_payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123456",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "15550228795"},
                            "statuses": [{"id": "wamid.123", "status": "delivered"}],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }
    assert parse_incoming(status_payload) is None


def test_verify_signature():
    secret = "my_app_secret_123"
    raw_body = b'{"object": "whatsapp"}'
    computed_hash = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    valid_header = f"sha256={computed_hash}"

    assert verify_signature(raw_body, valid_header, app_secret=secret) is True
    assert verify_signature(raw_body, "sha256=wronghash", app_secret=secret) is False
    assert verify_signature(raw_body, None, app_secret=secret) is False


def test_split_message():
    short_text = "Hello world"
    assert split_message(short_text, max_length=100) == ["Hello world"]

    long_text = ("Line 1\n" * 200)  # ~1400 chars
    long_text_2 = ("Line 2\n" * 200)
    full_text = long_text + long_text_2
    chunks = split_message(full_text, max_length=1500)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 1500


@pytest.mark.asyncio
async def test_send_text_mock():
    with patch("httpx.AsyncClient.post") as mock_post:
        mock_res = AsyncMock()
        mock_res.status_code = 200
        mock_post.return_value = mock_res

        with patch("app.config.settings.wa_token", "fake_token"), \
             patch("app.config.settings.wa_phone_number_id", "12345"):
            success = await send_text("923001234567", "Hello from test!")
            assert success is True
            assert mock_post.call_count == 1
