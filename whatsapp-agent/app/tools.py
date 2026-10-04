import re
import logging
from typing import Any, Dict, List, Optional
from app.odoo_client import OdooClient, OdooError

logger = logging.getLogger(__name__)


def normalize_phone_number(phone: str) -> str:
    """
    Normalize Pakistani and international phone numbers into E.164 format (+923xxxxxxxx).
    Handles 03xx, 923xx, +923xx, with spaces or hyphens.
    """
    if not phone:
        return ""
    # Strip whitespace, hyphens, parens
    cleaned = re.sub(r"[\s\-\(\)]", "", str(phone))
    
    if cleaned.startswith("+"):
        digits = cleaned[1:]
        if digits.startswith("92") and len(digits) == 12:
            return f"+{digits}"
        return cleaned
    
    # Check if starts with 03xx (11 digits total)
    if cleaned.startswith("03") and len(cleaned) == 11:
        return f"+92{cleaned[1:]}"
    
    # Check if starts with 923xx (12 digits total)
    if cleaned.startswith("923") and len(cleaned) == 12:
        return f"+{cleaned}"
        
    # Default fallback
    if cleaned.isdigit() and len(cleaned) == 10 and cleaned.startswith("3"):
        return f"+92{cleaned}"

    return cleaned if cleaned.startswith("+") else f"+{cleaned}"


class ToolRegistry:
    """Registry executing tool calls against Odoo with verified customer context."""

    def __init__(self, odoo_client: Optional[OdooClient] = None):
        self.odoo = odoo_client or OdooClient()

    async def search_product(self, query: str) -> Dict[str, Any]:
        """Search products in catalog matching query."""
        try:
            domain = [
                ("sale_ok", "=", True),
                "|",
                ("name", "ilike", query),
                ("default_code", "ilike", query),
            ]
            products = await self.odoo.search_read_async(
                model="product.product",
                domain=domain,
                fields=["id", "name", "list_price", "qty_available", "default_code"],
                limit=5,
                use_cache=True,
            )
            return {
                "status": "success",
                "count": len(products),
                "products": [
                    {
                        "product_id": p["id"],
                        "name": p["name"],
                        "price": p["list_price"],
                        "stock_quantity": p["qty_available"],
                        "code": p.get("default_code") or "",
                    }
                    for p in products
                ],
            }
        except OdooError as e:
            logger.error("Error searching product: %s", str(e))
            return {"status": "error", "message": "Failed to search product catalog."}
        except Exception as e:
            logger.error("Unexpected error in search_product: %s", str(e))
            return {"status": "error", "message": "Unexpected error searching products."}

    async def check_stock(self, product_name: str) -> Dict[str, Any]:
        """Check availability and stock level for a product."""
        res = await self.search_product(product_name)
        if res.get("status") == "error":
            return res
        products = res.get("products", [])
        if not products:
            return {"status": "not_found", "message": f"No product matching '{product_name}' was found."}

        exact_match = products[0]
        qty = exact_match["stock_quantity"]
        in_stock = qty > 0
        return {
            "status": "success",
            "product_name": exact_match["name"],
            "product_id": exact_match["product_id"],
            "in_stock": in_stock,
            "quantity_available": qty,
            "price": exact_match["price"],
        }

    async def find_customer(self, verified_phone: str) -> Dict[str, Any]:
        """Find partner record matching customer's normalized phone number."""
        norm_phone = normalize_phone_number(verified_phone)
        try:
            # Match by phone or mobile in Odoo res.partner
            domain = [
                "|",
                ("phone", "ilike", norm_phone[-10:]),
                ("mobile", "ilike", norm_phone[-10:]),
            ]
            partners = await self.odoo.search_read_async(
                model="res.partner",
                domain=domain,
                fields=["id", "name", "phone", "mobile", "email"],
                limit=1,
            )
            if partners:
                p = partners[0]
                return {
                    "status": "success",
                    "partner_id": p["id"],
                    "name": p["name"],
                    "phone": p.get("phone") or p.get("mobile") or norm_phone,
                }

            return {"status": "not_found", "message": "Customer record not found in Odoo."}
        except OdooError as e:
            logger.error("Error finding customer in Odoo: %s", str(e))
            return {"status": "error", "message": "Failed to query customer records."}

    async def get_or_create_partner(self, name: str, verified_phone: str) -> int:
        """Helper to get existing partner_id or create new res.partner in Odoo."""
        found = await self.find_customer(verified_phone)
        if found.get("status") == "success":
            return found["partner_id"]

        norm_phone = normalize_phone_number(verified_phone)
        partner_id = await self.odoo.execute_kw_async(
            "res.partner",
            "create",
            [{"name": name, "mobile": norm_phone, "comment": "Created via WhatsApp AI Agent"}],
        )
        return int(partner_id)

    async def create_lead(self, name: str, verified_phone: str, description: str) -> Dict[str, Any]:
        """Create a crm.lead tagged 'WhatsApp AI'."""
        try:
            norm_phone = normalize_phone_number(verified_phone)
            # Find tag or fallback
            tag_ids = await self.odoo.search_read_async(
                model="crm.tag",
                domain=[("name", "=", "WhatsApp AI")],
                fields=["id"],
                limit=1,
            )
            tag_id_list = [tag_ids[0]["id"]] if tag_ids else []

            if not tag_id_list:
                # Create tag if missing
                try:
                    new_tag = await self.odoo.execute_kw_async("crm.tag", "create", [{"name": "WhatsApp AI"}])
                    tag_id_list = [int(new_tag)]
                except Exception:
                    logger.warning("Could not create crm.tag WhatsApp AI")

            lead_vals = {
                "name": f"WhatsApp Inquiry: {name}",
                "contact_name": name,
                "mobile": norm_phone,
                "description": description,
                "tag_ids": [(6, 0, tag_id_list)] if tag_id_list else [],
            }
            lead_id = await self.odoo.execute_kw_async("crm.lead", "create", [lead_vals])
            return {
                "status": "success",
                "lead_id": lead_id,
                "message": f"Lead created successfully for {name}.",
            }
        except OdooError as e:
            logger.error("Failed to create lead in Odoo: %s", str(e))
            return {"status": "error", "message": "Failed to create lead record."}

    async def create_draft_quotation(
        self, verified_phone: str, customer_name: str, items: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """Create a sale order strictly in 'draft' state."""
        try:
            if not items:
                return {"status": "error", "message": "No items provided for quotation."}

            partner_id = await self.get_or_create_partner(customer_name, verified_phone)

            order_line_tuples = []
            for item in items:
                p_id = item.get("product_id")
                qty = item.get("qty", 1)
                order_line_tuples.append((0, 0, {"product_id": p_id, "product_uom_qty": qty}))

            order_vals = {
                "partner_id": partner_id,
                "state": "draft",
                "order_line": order_line_tuples,
                "note": "Created via WhatsApp AI Agent (Draft Only)",
            }
            order_id = await self.odoo.execute_kw_async("sale.order", "create", [order_vals])

            # Read back quotation reference & amount
            orders = await self.odoo.search_read_async(
                model="sale.order",
                domain=[("id", "=", order_id)],
                fields=["name", "amount_total"],
            )
            order_ref = orders[0]["name"] if orders else f"SO-{order_id}"
            total_amount = orders[0]["amount_total"] if orders else 0.0

            return {
                "status": "success",
                "quotation_id": order_id,
                "quotation_ref": order_ref,
                "total_amount": total_amount,
                "state": "draft",
                "message": f"Draft quotation {order_ref} created.",
            }
        except OdooError as e:
            logger.error("Failed to create draft quotation: %s", str(e))
            return {"status": "error", "message": "Failed to create draft quotation in Odoo."}

    async def get_order_status(self, verified_phone: str, order_ref: Optional[str] = None) -> Dict[str, Any]:
        """Fetch status of orders strictly belonging to verified customer phone partner."""
        try:
            cust = await self.find_customer(verified_phone)
            if cust.get("status") != "success":
                return {"status": "not_found", "message": "No existing customer or order history found for your number."}

            partner_id = cust["partner_id"]
            domain: List[Any] = [("partner_id", "=", partner_id)]
            if order_ref:
                domain.append(("name", "ilike", order_ref.strip()))

            orders = await self.odoo.search_read_async(
                model="sale.order",
                domain=domain,
                fields=["name", "date_order", "state", "amount_total"],
                limit=5,
            )

            if not orders:
                return {"status": "not_found", "message": "No orders found for your account."}

            formatted_orders = []
            for o in orders:
                state_map = {
                    "draft": "Draft Quotation",
                    "sent": "Quotation Sent",
                    "sale": "Confirmed Order",
                    "done": "Completed",
                    "cancel": "Cancelled",
                }
                formatted_orders.append({
                    "order_ref": o["name"],
                    "date": str(o.get("date_order", "")),
                    "status": state_map.get(o.get("state"), o.get("state")),
                    "total_amount": o.get("amount_total"),
                })

            return {"status": "success", "orders": formatted_orders}
        except OdooError as e:
            logger.error("Failed to get order status: %s", str(e))
            return {"status": "error", "message": "Failed to retrieve order status."}

    async def handoff_to_human(self, reason: str) -> Dict[str, Any]:
        """Signal that conversation needs human handoff."""
        return {
            "status": "handoff_triggered",
            "reason": reason,
            "message": "Handoff to human agent initiated.",
        }


# Agent Tools JSON Schemas Definition
TOOLS = [
    {
        "name": "search_product",
        "description": "Search products in catalog by name or keyword. Returns product names, prices, IDs, and stock levels.",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Product name or search keyword (e.g., 'shirt', 'rice', 'shoes')"}
            },
            "required": ["query"],
        },
    },
    {
        "name": "check_stock",
        "description": "Check if a specific product is in stock and get exact available quantity and price.",
        "input_schema": {
            "type": "object",
            "properties": {
                "product_name": {"type": "string", "description": "Product name to check availability"}
            },
            "required": ["product_name"],
        },
    },
    {
        "name": "create_lead",
        "description": "Create a sales inquiry lead when a customer shows interest in buying or requests a custom quote.",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Customer's name"},
                "description": {"type": "string", "description": "Summary of customer requirement or inquiry"}
            },
            "required": ["name", "description"],
        },
    },
    {
        "name": "create_draft_quotation",
        "description": "Create a DRAFT sales order quotation in Odoo. NEVER confirms or charges order. Call ONLY after customer explicitly agrees to place draft quote.",
        "input_schema": {
            "type": "object",
            "properties": {
                "customer_name": {"type": "string", "description": "Customer's full name"},
                "items": {
                    "type": "array",
                    "description": "List of products with product_id and quantity",
                    "items": {
                        "type": "object",
                        "properties": {
                            "product_id": {"type": "integer", "description": "Odoo product.product ID"},
                            "qty": {"type": "number", "description": "Quantity to order"}
                        },
                        "required": ["product_id", "qty"],
                    },
                },
            },
            "required": ["customer_name", "items"],
        },
    },
    {
        "name": "get_order_status",
        "description": "Check status of customer's previous or current orders.",
        "input_schema": {
            "type": "object",
            "properties": {
                "order_ref": {"type": "string", "description": "Optional order reference number e.g. SO001"}
            },
            "required": [],
        },
    },
    {
        "name": "handoff_to_human",
        "description": "Transfer conversation to human support staff when customer is angry, has payment/refund issues, wants discount, or question cannot be answered by tools.",
        "input_schema": {
            "type": "object",
            "properties": {
                "reason": {"type": "string", "description": "Detailed reason for human handoff"}
            },
            "required": ["reason"],
        },
    },
]
