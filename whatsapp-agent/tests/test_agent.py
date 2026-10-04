import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.agent import run_agent, execute_tool
from app.llm.base import LLMClient, LLMResponse
from app.tools import ToolRegistry


@pytest.mark.asyncio
async def test_run_agent_plain_response():
    mock_llm = AsyncMock(spec=LLMClient)
    mock_llm.chat.return_value = LLMResponse(
        text="Hello! How can I help you today?",
        tool_calls=[],
        usage={"input_tokens": 50, "output_tokens": 10},
        stop_reason="end_turn",
    )

    mock_customer = {"id": 1, "phone": "+923001234567", "name": "Ali", "bot_active": True}

    with patch("app.db.save_message", return_value=True), \
         patch("app.db.is_bot_active", return_value=True), \
         patch("app.db.get_recent_messages", return_value=[{"role": "user", "content": "Hi"}]):

        reply = await run_agent(mock_customer, "Hi", llm_client=mock_llm)
        assert reply == "Hello! How can I help you today?"
        mock_llm.chat.assert_called_once()


@pytest.mark.asyncio
async def test_run_agent_tool_use_loop():
    mock_llm = AsyncMock(spec=LLMClient)

    # Step 1: Model requests tool call
    res_1 = LLMResponse(
        text="",
        tool_calls=[{"id": "call_01", "name": "check_stock", "arguments": {"product_name": "Cotton Shirt"}}],
        usage={"input_tokens": 100, "output_tokens": 30},
        stop_reason="tool_use",
    )

    # Step 2: Model gives final text response
    res_2 = LLMResponse(
        text="Cotton Shirt is in stock (10 available) for 1500 PKR.",
        tool_calls=[],
        usage={"input_tokens": 150, "output_tokens": 20},
        stop_reason="end_turn",
    )

    mock_llm.chat.side_effect = [res_1, res_2]

    mock_odoo = AsyncMock()
    mock_odoo.search_read_async.return_value = [
        {"id": 101, "name": "Cotton Shirt", "list_price": 1500.0, "qty_available": 10.0, "default_code": "CS-01"}
    ]

    mock_customer = {"id": 1, "phone": "+923001234567", "name": "Ali", "bot_active": True}

    with patch("app.db.save_message", return_value=True), \
         patch("app.db.is_bot_active", return_value=True), \
         patch("app.db.get_recent_messages", return_value=[{"role": "user", "content": "Cotton shirt stock?"}]):

        reply = await run_agent(mock_customer, "Cotton shirt stock?", llm_client=mock_llm, odoo_client=mock_odoo)
        assert "Cotton Shirt is in stock" in reply
        assert mock_llm.chat.call_count == 2
