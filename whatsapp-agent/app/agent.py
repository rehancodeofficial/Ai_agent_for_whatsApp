import asyncio
import json
import logging
from typing import Any, Dict, List, Optional

from app.config import settings
from app import db
from app.handoff import notify_staff
from app.llm import LLMClient, get_llm_client
from app.odoo_client import OdooClient
from app.prompts import build_system_prompt
from app.tools import TOOLS, ToolRegistry

logger = logging.getLogger(__name__)

# Tool schema mapping for input validation
TOOL_PARAM_SCHEMAS = {
    t["name"]: set(t["input_schema"].get("properties", {}).keys())
    for t in TOOLS
}


def validate_and_filter_tool_input(tool_name: str, tool_input: Dict[str, Any]) -> Dict[str, Any]:
    """Validate tool input against allowed schema parameters, ignoring unknown parameters."""
    if not isinstance(tool_input, dict):
        return {}
    allowed = TOOL_PARAM_SCHEMAS.get(tool_name, set())
    return {k: v for k, v in tool_input.items() if k in allowed}


async def execute_tool(
    tool_name: str,
    tool_input: Dict[str, Any],
    customer: Dict[str, Any],
    tool_registry: ToolRegistry,
    recent_messages: List[Dict[str, str]],
) -> Dict[str, Any]:
    """
    Execute a single tool call with:
    - Server-side injection of verified customer details (phone number, customer ID).
    - Tool schema validation filtering unknown arguments.
    - Security isolation (no cross-customer data leakage).
    """
    verified_phone = customer["phone"]
    customer_name = customer.get("name") or "Valued Customer"

    logger.info("Executing tool '%s' for customer %s", tool_name, verified_phone)

    # Check for invalid argument errors passed from LLM parser
    if isinstance(tool_input, dict) and "_error" in tool_input:
        return {"status": "error", "message": tool_input["_error"]}

    clean_input = validate_and_filter_tool_input(tool_name, tool_input)

    if tool_name == "search_product":
        query = clean_input.get("query", "")
        return await tool_registry.search_product(query)

    elif tool_name == "check_stock":
        product_name = clean_input.get("product_name", "")
        return await tool_registry.check_stock(product_name)

    elif tool_name == "create_lead":
        name = clean_input.get("name") or customer_name
        description = clean_input.get("description", "")
        return await tool_registry.create_lead(name=name, verified_phone=verified_phone, description=description)

    elif tool_name == "create_draft_quotation":
        items = clean_input.get("items", [])
        c_name = clean_input.get("customer_name") or customer_name
        return await tool_registry.create_draft_quotation(
            verified_phone=verified_phone,
            customer_name=c_name,
            items=items,
        )

    elif tool_name == "get_order_status":
        order_ref = clean_input.get("order_ref")
        return await tool_registry.get_order_status(verified_phone=verified_phone, order_ref=order_ref)

    elif tool_name == "handoff_to_human":
        reason = clean_input.get("reason", "Customer requested human representative.")
        try:
            await db.set_bot_active(customer["id"], False)
        except Exception:
            pass
        await notify_staff(customer, reason, recent_messages)
        return await tool_registry.handoff_to_human(reason)

    else:
        return {"status": "error", "message": f"Unknown tool: {tool_name}"}


async def run_agent(
    customer: Dict[str, Any],
    user_text: str,
    llm_client: Optional[LLMClient] = None,
    odoo_client: Optional[OdooClient] = None,
    wa_message_id: Optional[str] = None,
) -> str:
    """
    Agent execution loop using Google Gemini:
    1. Saves user message to DB and loads recent history.
    2. Calls LLMClient (Google Gemini) with tools.
    3. Executes tool calls (max 5 iterations) with retries & backoff.
    4. Logs token usage and cost estimation.
    5. Saves assistant reply and returns response.
    """
    customer_id = customer["id"]
    phone = customer["phone"]

    # Save user message to database if DB pool is available
    if user_text:
        try:
            saved = await db.save_message(customer_id, "user", user_text, wa_message_id=wa_message_id)
            if not saved:
                logger.info("Message %s already processed. Skipping agent execution.", wa_message_id)
                return ""
        except Exception as e:
            logger.warning("DB not available for saving message: %s", str(e))

    # Check if bot is active for this customer
    try:
        if not await db.is_bot_active(customer_id):
            logger.info("Bot is inactive for customer %s (%s). Skipping response.", customer_id, phone)
            return ""
    except Exception:
        pass

    # Load history (last 10 messages)
    try:
        history = await db.get_recent_messages(customer_id, limit=10)
    except Exception:
        history = []

    # Pick LLM client
    active_llm = llm_client or get_llm_client()

    tool_registry = ToolRegistry(odoo_client=odoo_client)
    system_prompt = build_system_prompt(settings.business_name)

    # Build initial message trajectory from DB history
    messages: List[Dict[str, Any]] = []
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})

    # Always ensure the current user message is the last entry in the trajectory.
    # If DB history already contains it as the last message, skip adding it again.
    # If not (e.g., DB was unavailable, or history was empty), add it explicitly.
    if user_text:
        last_user_msg = next(
            (m["content"] for m in reversed(messages) if m["role"] == "user"),
            None,
        )
        if last_user_msg != user_text:
            messages.append({"role": "user", "content": user_text})

    max_iterations = 5
    iteration = 0
    final_reply = ""
    handoff_occurred = False

    fallback_reply = (
        "Aapki darkhwast par amal ho raha hai. Hamari team jald aap se rabta karegi.\n"
        "Our team will contact you shortly."
    )

    while iteration < max_iterations:
        iteration += 1
        logger.info("Agent iteration %s for customer %s", iteration, phone)

        # Retry logic: 2 retries, exponential backoff, timeout 30s
        response = None
        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    active_llm.chat(
                        system=system_prompt,
                        messages=messages,
                        tools=TOOLS,
                        max_tokens=500,
                    ),
                    timeout=30.0,
                )
                break
            except (asyncio.TimeoutError, Exception) as e:
                logger.warning("LLM API attempt %d failed for customer %s: %s", attempt + 1, phone, str(e))
                if attempt < max_retries:
                    await asyncio.sleep(2 ** attempt)

        if not response:
            logger.error("LLM calls failed after retries for customer %s.", phone)
            return fallback_reply

        # Token usage logging & cost estimation
        input_tokens = response.usage.get("input_tokens", 0)
        output_tokens = response.usage.get("output_tokens", 0)
        cost = (input_tokens / 1_000_000.0) * settings.llm_price_input + (output_tokens / 1_000_000.0) * settings.llm_price_output
        logger.info(
            "LLM Token Usage - Input: %d, Output: %d, Est. Cost: $%.6f",
            input_tokens,
            output_tokens,
            cost,
        )

        if response.tool_calls:
            messages.append({
                "role": "assistant",
                "content": response.text or "",
                "tool_calls": response.tool_calls,
            })

            for tc in response.tool_calls:
                t_id = tc["id"]
                t_name = tc["name"]
                t_args = tc.get("arguments", {})

                res = await execute_tool(t_name, t_args, customer, tool_registry, history)
                if t_name == "handoff_to_human":
                    handoff_occurred = True

                messages.append({
                    "role": "tool",
                    "name": t_name,
                    "tool_call_id": t_id,
                    "content": json.dumps(res),
                })

            if handoff_occurred:
                final_reply = (
                    "Aapki baat hamari support team ko transfer kar di gayi hai. "
                    "Humare representative jald aap se rabta karenge. Shukriya!\n\n"
                    "Your conversation has been transferred to human support."
                )
                break
        else:
            final_reply = response.text or ""
            break

    if not final_reply:
        if handoff_occurred:
            final_reply = "Aapki request human agent ko forwarding kar di gayi hai."
        else:
            final_reply = fallback_reply

    if final_reply:
        try:
            await db.save_message(customer_id, "assistant", final_reply)
        except Exception:
            pass

    return final_reply
