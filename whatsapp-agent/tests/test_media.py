import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent import build_media_prompt
from app.llm.gemini_client import GeminiLLMClient
from app.whatsapp import (
    download_media,
    is_supported_media_mime,
    normalize_mime_type,
    parse_incoming,
    parse_media,
)


def media_payload(msg_type: str, media: dict) -> dict:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123456",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "contacts": [{"profile": {"name": "Ahmed Raza"}, "wa_id": "923001234567"}],
                            "messages": [
                                {
                                    "from": "923001234567",
                                    "id": "wamid.MEDIA1",
                                    "timestamp": "1700000000",
                                    "type": msg_type,
                                    msg_type: media,
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }


# --- mime helpers ---

def test_normalize_mime_type_strips_codec_parameter():
    assert normalize_mime_type("audio/ogg; codecs=opus") == "audio/ogg"
    assert normalize_mime_type("IMAGE/JPEG") == "image/jpeg"
    assert normalize_mime_type("") == ""


@pytest.mark.parametrize(
    "mime,supported",
    [
        ("image/jpeg", True),
        ("image/webp", True),
        ("audio/ogg; codecs=opus", True),
        ("video/mp4", True),
        ("application/pdf", True),
        ("text/csv", False),
        ("application/zip", False),
        ("", False),
    ],
)
def test_is_supported_media_mime(mime, supported):
    assert is_supported_media_mime(mime) is supported


# --- parse_media ---

def test_parse_media_voice_note():
    payload = media_payload("audio", {"id": "MED_AUDIO", "mime_type": "audio/ogg; codecs=opus", "voice": True})
    assert parse_media(payload) == {
        "kind": "audio",
        "media_id": "MED_AUDIO",
        "mime_type": "audio/ogg",
        "caption": "",
    }


def test_parse_media_image_with_caption():
    payload = media_payload("image", {"id": "MED_IMG", "mime_type": "image/jpeg", "caption": "  is ka rate? "})
    parsed = parse_media(payload)
    assert parsed["kind"] == "image"
    assert parsed["media_id"] == "MED_IMG"
    assert parsed["caption"] == "is ka rate?"


def test_parse_media_returns_none_for_text():
    text_payload = media_payload("text", {"body": "hello"})
    assert parse_media(text_payload) is None


def test_parse_media_returns_none_without_media_id():
    assert parse_media(media_payload("image", {"mime_type": "image/jpeg"})) is None


def test_parse_incoming_still_returns_five_values_for_media():
    payload = media_payload("image", {"id": "MED_IMG", "mime_type": "image/jpeg"})
    phone, name, wa_id, msg_type, text = parse_incoming(payload)
    assert phone == "923001234567"
    assert msg_type == "image"


# --- download_media ---

def _http_client_returning(responses):
    client = MagicMock()
    client.get = AsyncMock(side_effect=responses)
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=client)
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx, client


@pytest.mark.asyncio
async def test_download_media_two_step_success():
    meta = MagicMock(status_code=200)
    meta.json.return_value = {"url": "https://lookaside.fb/x", "mime_type": "audio/ogg; codecs=opus", "file_size": 3}
    blob = MagicMock(status_code=200, content=b"abc", headers={})

    ctx, client = _http_client_returning([meta, blob])
    with patch("app.whatsapp.settings") as mock_settings, patch("app.whatsapp.httpx.AsyncClient", return_value=ctx):
        mock_settings.wa_token = "token"
        result = await download_media("MED_AUDIO")

    assert result == (b"abc", "audio/ogg")
    # The CDN fetch must carry the bearer token too.
    assert client.get.call_args_list[1].kwargs["headers"]["Authorization"] == "Bearer token"


@pytest.mark.asyncio
async def test_download_media_rejects_oversized_file():
    meta = MagicMock(status_code=200)
    meta.json.return_value = {"url": "https://lookaside.fb/x", "mime_type": "video/mp4", "file_size": 99 * 1024 * 1024}
    ctx, _ = _http_client_returning([meta])

    with patch("app.whatsapp.settings") as mock_settings, patch("app.whatsapp.httpx.AsyncClient", return_value=ctx):
        mock_settings.wa_token = "token"
        assert await download_media("MED_BIG") is None


@pytest.mark.asyncio
async def test_download_media_returns_none_on_error():
    meta = MagicMock(status_code=404, text="not found")
    ctx, _ = _http_client_returning([meta])

    with patch("app.whatsapp.settings") as mock_settings, patch("app.whatsapp.httpx.AsyncClient", return_value=ctx):
        mock_settings.wa_token = "token"
        assert await download_media("MED_GONE") is None


@pytest.mark.asyncio
async def test_download_media_without_token():
    with patch("app.whatsapp.settings") as mock_settings:
        mock_settings.wa_token = ""
        assert await download_media("MED_ANY") is None


# --- prompt building ---

def test_build_media_prompt_uses_instruction_without_caption():
    prompt = build_media_prompt({"kind": "audio"})
    assert "voice note" in prompt
    assert "caption" not in prompt.lower()


def test_build_media_prompt_includes_caption():
    prompt = build_media_prompt({"kind": "image", "caption": "is ka rate?"})
    assert "is ka rate?" in prompt


def test_build_media_prompt_falls_back_for_unknown_kind():
    assert build_media_prompt({"kind": "hologram"})


# --- Gemini multimodal parts ---

def test_user_parts_builds_text_and_inline_media():
    client = GeminiLLMClient.__new__(GeminiLLMClient)
    parts = client._user_parts([
        {"type": "text", "text": "ye kya hai?"},
        {"type": "media", "mime_type": "image/jpeg", "data": b"\xff\xd8\xff"},
    ])
    assert len(parts) == 2
    assert parts[0].text == "ye kya hai?"
    assert parts[1].inline_data.mime_type == "image/jpeg"
    assert parts[1].inline_data.data == b"\xff\xd8\xff"


def test_user_parts_handles_plain_string():
    client = GeminiLLMClient.__new__(GeminiLLMClient)
    parts = client._user_parts("salam")
    assert len(parts) == 1 and parts[0].text == "salam"


def test_user_parts_never_returns_empty():
    client = GeminiLLMClient.__new__(GeminiLLMClient)
    assert len(client._user_parts([])) == 1


# --- agent attaches media to the turn ---

@pytest.mark.asyncio
async def test_run_agent_sends_media_to_llm():
    from app.agent import run_agent
    from app.llm.base import LLMClient, LLMResponse

    mock_llm = AsyncMock(spec=LLMClient)
    mock_llm.chat.return_value = LLMResponse(text="Ye iPhone 15 lag raha hai.", tool_calls=[])
    customer = {"id": 1, "phone": "923001234567", "name": "Ali", "bot_active": True}

    with patch("app.db.save_message", return_value=True), \
         patch("app.db.is_bot_active", return_value=True), \
         patch("app.db.get_recent_messages", return_value=[{"role": "user", "content": "[image message]"}]):
        reply = await run_agent(
            customer,
            "[image message]",
            llm_client=mock_llm,
            media={"data": b"\xff\xd8\xff", "mime_type": "image/jpeg", "kind": "image", "caption": ""},
        )

    assert reply == "Ye iPhone 15 lag raha hai."
    sent_messages = mock_llm.chat.call_args.kwargs["messages"]
    # The text placeholder is replaced by one multimodal turn, not duplicated.
    assert len(sent_messages) == 1
    content = sent_messages[0]["content"]
    assert content[0]["type"] == "text" and "tasveer" in content[0]["text"]
    assert content[1] == {"type": "media", "mime_type": "image/jpeg", "data": b"\xff\xd8\xff"}


@pytest.mark.asyncio
async def test_run_agent_without_media_sends_plain_text():
    from app.agent import run_agent
    from app.llm.base import LLMClient, LLMResponse

    mock_llm = AsyncMock(spec=LLMClient)
    mock_llm.chat.return_value = LLMResponse(text="Salam!", tool_calls=[])
    customer = {"id": 1, "phone": "923001234567", "name": "Ali", "bot_active": True}

    with patch("app.db.save_message", return_value=True), \
         patch("app.db.is_bot_active", return_value=True), \
         patch("app.db.get_recent_messages", return_value=[{"role": "user", "content": "salam"}]):
        await run_agent(customer, "salam", llm_client=mock_llm)

    assert mock_llm.chat.call_args.kwargs["messages"] == [{"role": "user", "content": "salam"}]


# --- webhook wiring ---

@pytest.mark.asyncio
async def test_handle_incoming_voice_note_reaches_agent():
    from app import main

    payload = media_payload("audio", {"id": "MED_AUDIO", "mime_type": "audio/ogg; codecs=opus", "voice": True})

    with patch("app.main.db.get_or_create_customer", new=AsyncMock(return_value={"id": 7, "phone": "923001234567", "name": "Ahmed Raza"})), \
         patch("app.main.db.is_bot_active", new=AsyncMock(return_value=True)), \
         patch("app.main.download_media", new=AsyncMock(return_value=(b"OggS", "audio/ogg"))), \
         patch("app.main.mark_as_read", new=AsyncMock(return_value=True)), \
         patch("app.main.run_agent", new=AsyncMock(return_value="Ji farmaiye!")) as mock_agent, \
         patch("app.main.send_text", new=AsyncMock(return_value=True)) as mock_send:
        main.RATE_LIMIT_TRACKER.clear()
        await main._handle_incoming_message(payload)

    media = mock_agent.call_args.kwargs["media"]
    assert media == {"data": b"OggS", "mime_type": "audio/ogg", "kind": "audio", "caption": ""}
    # The DB placeholder, not the raw bytes, is what gets stored as the user turn.
    assert mock_agent.call_args.args[1] == "[audio message]"
    mock_send.assert_awaited_once_with("923001234567", "Ji farmaiye!")


@pytest.mark.asyncio
async def test_handle_incoming_image_caption_becomes_user_text():
    from app import main

    payload = media_payload("image", {"id": "MED_IMG", "mime_type": "image/jpeg", "caption": "is ka rate?"})

    with patch("app.main.db.get_or_create_customer", new=AsyncMock(return_value={"id": 7, "phone": "923001234567", "name": "Ahmed Raza"})), \
         patch("app.main.db.is_bot_active", new=AsyncMock(return_value=True)), \
         patch("app.main.download_media", new=AsyncMock(return_value=(b"\xff\xd8\xff", "image/jpeg"))), \
         patch("app.main.mark_as_read", new=AsyncMock(return_value=True)), \
         patch("app.main.run_agent", new=AsyncMock(return_value="Rs. 100,000")) as mock_agent, \
         patch("app.main.send_text", new=AsyncMock(return_value=True)):
        main.RATE_LIMIT_TRACKER.clear()
        await main._handle_incoming_message(payload)

    assert mock_agent.call_args.args[1] == "is ka rate?"
    assert mock_agent.call_args.kwargs["media"]["caption"] == "is ka rate?"


@pytest.mark.asyncio
async def test_handle_incoming_unsupported_type_is_rejected_politely():
    from app import main

    payload = media_payload("document", {"id": "MED_DOC", "mime_type": "application/zip"})

    with patch("app.main.db.get_or_create_customer", new=AsyncMock(return_value={"id": 7, "phone": "923001234567", "name": "Ahmed Raza"})), \
         patch("app.main.db.is_bot_active", new=AsyncMock(return_value=True)), \
         patch("app.main.db.save_message", new=AsyncMock(return_value=True)), \
         patch("app.main.download_media", new=AsyncMock()) as mock_download, \
         patch("app.main.run_agent", new=AsyncMock()) as mock_agent, \
         patch("app.main.send_text", new=AsyncMock(return_value=True)) as mock_send:
        main.RATE_LIMIT_TRACKER.clear()
        await main._handle_incoming_message(payload)

    mock_download.assert_not_awaited()
    mock_agent.assert_not_awaited()
    assert "parh nahi sakte" in mock_send.call_args.args[1]


@pytest.mark.asyncio
async def test_handle_incoming_download_failure_tells_the_customer():
    from app import main

    payload = media_payload("image", {"id": "MED_IMG", "mime_type": "image/jpeg"})

    with patch("app.main.db.get_or_create_customer", new=AsyncMock(return_value={"id": 7, "phone": "923001234567", "name": "Ahmed Raza"})), \
         patch("app.main.db.is_bot_active", new=AsyncMock(return_value=True)), \
         patch("app.main.db.save_message", new=AsyncMock(return_value=True)), \
         patch("app.main.download_media", new=AsyncMock(return_value=None)), \
         patch("app.main.run_agent", new=AsyncMock()) as mock_agent, \
         patch("app.main.send_text", new=AsyncMock(return_value=True)) as mock_send:
        main.RATE_LIMIT_TRACKER.clear()
        await main._handle_incoming_message(payload)

    mock_agent.assert_not_awaited()
    assert "download nahi ho saki" in mock_send.call_args.args[1]


# --- thinking budget ---

def test_build_config_caps_thinking_by_default():
    client = GeminiLLMClient.__new__(GeminiLLMClient)
    cfg = client._build_config("sys", None, 500)
    assert cfg.thinking_config is not None
    assert cfg.thinking_config.thinking_budget == 0
    assert cfg.max_output_tokens == 500


def test_build_config_omits_thinking_when_model_decides():
    client = GeminiLLMClient.__new__(GeminiLLMClient)
    with patch("app.llm.gemini_client.settings") as mock_settings:
        mock_settings.gemini_thinking_budget = -1
        assert client._build_config("sys", None, 500).thinking_config is None


def test_build_config_fallback_drops_thinking():
    client = GeminiLLMClient.__new__(GeminiLLMClient)
    assert client._build_config("sys", None, 500, with_thinking=False).thinking_config is None


@pytest.mark.asyncio
async def test_chat_retries_without_thinking_when_model_rejects_it():
    client = GeminiLLMClient.__new__(GeminiLLMClient)
    client._model = "gemini-2.0-flash"

    response = MagicMock()
    response.candidates = []
    response.usage_metadata = None

    generate = AsyncMock(side_effect=[ValueError("thinking_config is not supported"), response])
    client.client = MagicMock(aio=MagicMock(models=MagicMock(generate_content=generate)))

    result = await client.chat(system="sys", messages=[{"role": "user", "content": "hi"}], tools=[])

    assert generate.await_count == 2
    assert generate.await_args_list[0].kwargs["config"].thinking_config is not None
    assert generate.await_args_list[1].kwargs["config"].thinking_config is None
    assert result.text is None


@pytest.mark.asyncio
async def test_chat_does_not_retry_other_errors():
    client = GeminiLLMClient.__new__(GeminiLLMClient)
    client._model = "gemini-3.8-flash"
    generate = AsyncMock(side_effect=RuntimeError("429 RESOURCE_EXHAUSTED"))
    client.client = MagicMock(aio=MagicMock(models=MagicMock(generate_content=generate)))

    with pytest.raises(RuntimeError):
        await client.chat(system="sys", messages=[{"role": "user", "content": "hi"}], tools=[])
    assert generate.await_count == 1


@pytest.mark.asyncio
async def test_chat_counts_thinking_tokens_as_output():
    client = GeminiLLMClient.__new__(GeminiLLMClient)
    client._model = "gemini-3.8-flash"

    response = MagicMock()
    response.candidates = []
    response.usage_metadata = MagicMock(
        prompt_token_count=100, candidates_token_count=8, thoughts_token_count=192
    )
    client.client = MagicMock(aio=MagicMock(models=MagicMock(generate_content=AsyncMock(return_value=response))))

    result = await client.chat(system="sys", messages=[{"role": "user", "content": "hi"}], tools=[])
    assert result.usage == {"input_tokens": 100, "output_tokens": 200}
