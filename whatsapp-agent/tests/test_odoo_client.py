import pytest
from unittest.mock import MagicMock, patch
from app.odoo_client import OdooClient, OdooAuthError, OdooAccessDeniedError, OdooConnectionError


def test_odoo_client_authenticate_success():
    client = OdooClient(url="http://fake-odoo.local", db="testdb", user="admin", api_key="secret")
    with patch("xmlrpc.client.ServerProxy") as mock_proxy:
        mock_common = MagicMock()
        mock_common.authenticate.return_value = 2
        mock_proxy.return_value = mock_common

        uid = client.authenticate()
        assert uid == 2
        mock_common.authenticate.assert_called_once_with("testdb", "admin", "secret", {})


def test_odoo_client_authenticate_failure():
    client = OdooClient(url="http://fake-odoo.local", db="testdb", user="admin", api_key="wrong")
    with patch("xmlrpc.client.ServerProxy") as mock_proxy:
        mock_common = MagicMock()
        mock_common.authenticate.return_value = False
        mock_proxy.return_value = mock_common

        with pytest.raises(OdooAuthError):
            client.authenticate()


def test_odoo_client_search_read_caching():
    client = OdooClient(cache_ttl_seconds=60)
    client.uid = 2
    with patch.object(client, "execute_kw") as mock_execute:
        mock_execute.return_value = [{"id": 1, "name": "Item A"}]

        res1 = client.search_read("product.product", domain=[("name", "ilike", "Item")], use_cache=True)
        assert len(res1) == 1
        assert mock_execute.call_count == 1

        # Second call with use_cache=True should hit cache
        res2 = client.search_read("product.product", domain=[("name", "ilike", "Item")], use_cache=True)
        assert len(res2) == 1
        assert mock_execute.call_count == 1  # Still 1 call


@pytest.mark.asyncio
async def test_odoo_client_async_execute():
    client = OdooClient()
    client.uid = 2
    with patch.object(client, "execute_kw", return_value=[{"id": 5}]) as mock_exec:
        res = await client.execute_kw_async("res.partner", "search_read", [], {})
        assert res == [{"id": 5}]
