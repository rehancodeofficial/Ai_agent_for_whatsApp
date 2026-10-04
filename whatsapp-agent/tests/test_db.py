import pytest
from unittest.mock import AsyncMock, MagicMock
from app.db import (
    get_or_create_customer,
    save_message,
    get_recent_messages,
    set_bot_active,
    is_bot_active,
)

def make_mock_conn(fetchone_results=None, fetchall_results=None):
    mock_conn = MagicMock()
    mock_cur = AsyncMock()
    if fetchone_results:
        mock_cur.fetchone.side_effect = fetchone_results
    if fetchall_results:
        mock_cur.fetchall.return_value = fetchall_results

    mock_cursor_ctx = AsyncMock()
    mock_cursor_ctx.__aenter__.return_value = mock_cur
    mock_conn.cursor.return_value = mock_cursor_ctx
    return mock_conn, mock_cur


@pytest.mark.asyncio
async def test_get_or_create_customer_mock():
    mock_conn, _ = make_mock_conn(fetchone_results=[
        None,
        {"id": 1, "phone": "+923001234567", "name": "Ali", "odoo_partner_id": None, "bot_active": True}
    ])

    cust = await get_or_create_customer("+923001234567", "Ali", conn=mock_conn)
    assert cust["id"] == 1
    assert cust["phone"] == "+923001234567"
    assert cust["name"] == "Ali"


@pytest.mark.asyncio
async def test_save_message_duplicate_detection():
    mock_conn, _ = make_mock_conn(fetchone_results=[{"id": 10}])
    saved = await save_message(1, "user", "Hello", wa_message_id="msg_123", conn=mock_conn)
    assert saved is False


@pytest.mark.asyncio
async def test_save_message_success():
    mock_conn, _ = make_mock_conn(fetchone_results=[None])
    saved = await save_message(1, "user", "Hello", wa_message_id="msg_456", conn=mock_conn)
    assert saved is True


@pytest.mark.asyncio
async def test_get_recent_messages():
    mock_conn, _ = make_mock_conn(fetchall_results=[
        {"role": "user", "content": "Price of shirt?"},
        {"role": "assistant", "content": "Shirt is 1500 PKR."}
    ])

    messages = await get_recent_messages(1, limit=5, conn=mock_conn)
    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_bot_active_toggle():
    mock_conn, mock_cur = make_mock_conn(fetchone_results=[{"bot_active": True}])
    active = await is_bot_active(1, conn=mock_conn)
    assert active is True

    await set_bot_active(1, False, conn=mock_conn)
    mock_cur.execute.assert_called_with(
        "UPDATE customers SET bot_active = %s WHERE id = %s",
        (False, 1),
    )
