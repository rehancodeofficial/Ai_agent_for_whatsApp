import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.main import app
from app.config import settings

client = TestClient(app)


def test_health_check():
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_webhook_get_verification_success():
    with patch("app.config.settings.wa_verify_token", "my_test_verify_token"):
        response = client.get(
            "/webhook",
            params={
                "hub.mode": "subscribe",
                "hub.verify_token": "my_test_verify_token",
                "hub.challenge": "123456789",
            },
        )
        assert response.status_code == 200
        assert response.text == "123456789"


def test_webhook_get_verification_failure():
    response = client.get(
        "/webhook",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong_token",
            "hub.challenge": "123456789",
        },
    )
    assert response.status_code == 403


def test_webhook_post_success():
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123456",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "contacts": [{"profile": {"name": "Test User"}, "wa_id": "923001234567"}],
                            "messages": [
                                {
                                    "from": "923001234567",
                                    "id": "wamid.test01",
                                    "timestamp": "1700000000",
                                    "text": {"body": "Hello bot!"},
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

    with patch("app.main.verify_signature", return_value=True), \
         patch("app.main.process_incoming_message", new_callable=AsyncMock) as mock_process:
        response = client.post("/webhook", json=payload)
        assert response.status_code == 200
        assert response.json()["status"] == "received"


def test_admin_pause_resume_auth():
    # Unauthorized request (missing token)
    response = client.post("/admin/pause/+923001234567")
    assert response.status_code == 401

    # Forbidden request (wrong token)
    response = client.post("/admin/pause/+923001234567", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 403

    # Authorized request
    with patch("app.main.pause_bot_for_customer", new_callable=AsyncMock) as mock_pause:
        mock_pause.return_value = {"status": "success", "message": "Bot paused"}
        headers = {"Authorization": f"Bearer {settings.admin_token}"}
        response = client.post("/admin/pause/+923001234567", headers=headers)
        assert response.status_code == 200
        assert response.json()["status"] == "success"
