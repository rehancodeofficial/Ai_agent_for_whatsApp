import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from app.llm.gemini_client import GeminiLLMClient
from app.llm.base import LLMResponse


@pytest.mark.asyncio
async def test_gemini_client_conversion():
    client = GeminiLLMClient(client=MagicMock())
    tools = [
        {
            "name": "check_stock",
            "description": "Check stock level of a product",
            "input_schema": {
                "type": "object",
                "properties": {
                    "product_name": {"type": "string"}
                },
                "required": ["product_name"]
            }
        }
    ]

    converted_tools = client._convert_tools(tools)
    assert len(converted_tools) == 1
    assert len(converted_tools[0].function_declarations) == 1
    decl = converted_tools[0].function_declarations[0]
    assert decl.name == "check_stock"
    assert decl.description == "Check stock level of a product"


@pytest.mark.asyncio
async def test_gemini_client_messages_conversion():
    client = GeminiLLMClient(client=MagicMock())
    messages = [
        {"role": "user", "content": "Hello"},
        {"role": "assistant", "content": "Hi there!", "tool_calls": [{"name": "check_stock", "arguments": {"product_name": "Shirt"}}]},
        {"role": "tool", "name": "check_stock", "tool_call_id": "call_1", "content": '{"status": "in_stock"}'}
    ]

    converted = client._convert_messages(messages)
    assert len(converted) == 3
    assert converted[0].role == "user"
    assert converted[1].role == "model"
    assert converted[2].role == "user"
