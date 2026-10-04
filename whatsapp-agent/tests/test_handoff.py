import pytest
from unittest.mock import AsyncMock, patch
from app.handoff import notify_staff, pause_bot_for_customer, resume_bot_for_customer


@pytest.mark.asyncio
async def test_notify_staff():
    mock_customer = {"id": 1, "phone": "+923001234567", "name": "Ali", "odoo_partner_id": None}
    recent_msgs = [{"role": "user", "content": "I want a refund!"}]

    with patch("app.handoff.send_text", return_value=True) as mock_send:
        success = await notify_staff(mock_customer, "Customer requested refund", recent_msgs)
        assert success is True
        mock_send.assert_called_once()
        call_args = mock_send.call_args[0]
        assert "HUMAN HANDOFF ALERT" in call_args[1]
        assert "+923001234567" in call_args[1]


@pytest.mark.asyncio
async def test_pause_resume_bot():
    with patch("app.db.get_or_create_customer", return_value={"id": 5, "phone": "+923009999999"}), \
         patch("app.db.set_bot_active") as mock_set_active:

        res_pause = await pause_bot_for_customer("+923009999999")
        assert res_pause["status"] == "success"
        mock_set_active.assert_called_with(5, False)

        res_resume = await resume_bot_for_customer("+923009999999")
        assert res_resume["status"] == "success"
        mock_set_active.assert_called_with(5, True)
