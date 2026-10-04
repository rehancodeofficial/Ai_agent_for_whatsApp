import pytest
from unittest.mock import AsyncMock, MagicMock
from app.tools import ToolRegistry, normalize_phone_number


def test_normalize_phone_number():
    assert normalize_phone_number("03001234567") == "+923001234567"
    assert normalize_phone_number("923001234567") == "+923001234567"
    assert normalize_phone_number("+923001234567") == "+923001234567"
    assert normalize_phone_number("0300 123-4567") == "+923001234567"
    assert normalize_phone_number("+14155552671") == "+14155552671"


@pytest.mark.asyncio
async def test_search_product_mock():
    mock_odoo = AsyncMock()
    mock_odoo.search_read_async.return_value = [
        {"id": 101, "name": "Cotton Shirt", "list_price": 1500.0, "qty_available": 25.0, "default_code": "CS-01"}
    ]
    registry = ToolRegistry(odoo_client=mock_odoo)

    res = await registry.search_product("shirt")
    assert res["status"] == "success"
    assert res["count"] == 1
    assert res["products"][0]["name"] == "Cotton Shirt"
    assert res["products"][0]["price"] == 1500.0


@pytest.mark.asyncio
async def test_check_stock_in_stock():
    mock_odoo = AsyncMock()
    mock_odoo.search_read_async.return_value = [
        {"id": 101, "name": "Cotton Shirt", "list_price": 1500.0, "qty_available": 10.0, "default_code": "CS-01"}
    ]
    registry = ToolRegistry(odoo_client=mock_odoo)

    res = await registry.check_stock("Cotton Shirt")
    assert res["status"] == "success"
    assert res["in_stock"] is True
    assert res["quantity_available"] == 10.0


@pytest.mark.asyncio
async def test_create_lead():
    mock_odoo = AsyncMock()
    mock_odoo.search_read_async.return_value = [{"id": 1}]
    mock_odoo.execute_kw_async.return_value = 50

    registry = ToolRegistry(odoo_client=mock_odoo)
    res = await registry.create_lead("Ahmed", "+923001234567", "Wants bulk inquiry")
    assert res["status"] == "success"
    assert res["lead_id"] == 50


@pytest.mark.asyncio
async def test_create_draft_quotation():
    mock_odoo = AsyncMock()
    mock_odoo.search_read_async.side_effect = [
        # find_customer call
        [{"id": 5, "name": "Ahmed", "phone": "+923001234567"}],
        # sale.order read back call
        [{"id": 12, "name": "SO0012", "amount_total": 3000.0}],
    ]
    mock_odoo.execute_kw_async.return_value = 12

    registry = ToolRegistry(odoo_client=mock_odoo)
    res = await registry.create_draft_quotation(
        verified_phone="+923001234567",
        customer_name="Ahmed",
        items=[{"product_id": 101, "qty": 2}],
    )

    assert res["status"] == "success"
    assert res["quotation_ref"] == "SO0012"
    assert res["state"] == "draft"
    assert res["total_amount"] == 3000.0


@pytest.mark.asyncio
async def test_get_order_status_customer_scoping():
    mock_odoo = AsyncMock()
    mock_odoo.search_read_async.side_effect = [
        # find_customer call
        [{"id": 5, "name": "Ahmed", "phone": "+923001234567"}],
        # sale.order search call
        [{"name": "SO005", "date_order": "2026-01-01", "state": "draft", "amount_total": 1500.0}],
    ]
    registry = ToolRegistry(odoo_client=mock_odoo)

    res = await registry.get_order_status("+923001234567")
    assert res["status"] == "success"
    assert len(res["orders"]) == 1
    assert res["orders"][0]["order_ref"] == "SO005"
    
    # Verify search domain scoped to partner_id = 5
    call_args = mock_odoo.search_read_async.call_args_list[1]
    assert ("partner_id", "=", 5) in call_args.kwargs["domain"]
