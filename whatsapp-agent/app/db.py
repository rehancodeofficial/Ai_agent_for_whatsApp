import logging
from typing import Any, Dict, List, Optional
import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from app.config import settings

logger = logging.getLogger(__name__)

# Global async connection pool
pool: Optional[AsyncConnectionPool] = None


async def init_db_pool(conninfo: Optional[str] = None) -> AsyncConnectionPool:
    """Initialize the global psycopg AsyncConnectionPool."""
    global pool
    url = conninfo or settings.database_url
    if pool is None:
        logger.info("Initializing Postgres AsyncConnectionPool")
        pool = AsyncConnectionPool(conninfo=url, open=False, min_size=1, max_size=10)
        await pool.open()
    return pool


async def close_db_pool() -> None:
    """Close the global psycopg AsyncConnectionPool."""
    global pool
    if pool is not None:
        logger.info("Closing Postgres AsyncConnectionPool")
        await pool.close()
        pool = None


async def get_or_create_customer(phone: str, name: Optional[str] = None, conn: Optional[psycopg.AsyncConnection] = None) -> Dict[str, Any]:
    """
    Find existing customer by phone number, or insert a new one.
    Updates name if existing customer has no name but new name is provided.
    """
    async def _execute(c: psycopg.AsyncConnection) -> Dict[str, Any]:
        async with c.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT id, phone, name, odoo_partner_id, bot_active, created_at FROM customers WHERE phone = %s",
                (phone,),
            )
            customer = await cur.fetchone()
            if customer:
                if name and not customer.get("name"):
                    await cur.execute(
                        "UPDATE customers SET name = %s WHERE id = %s RETURNING id, phone, name, odoo_partner_id, bot_active, created_at",
                        (name, customer["id"]),
                    )
                    customer = await cur.fetchone()
                return customer

            await cur.execute(
                """
                INSERT INTO customers (phone, name, bot_active)
                VALUES (%s, %s, TRUE)
                RETURNING id, phone, name, odoo_partner_id, bot_active, created_at
                """,
                (phone, name),
            )
            new_customer = await cur.fetchone()
            return new_customer

    if conn is not None:
        return await _execute(conn)
    
    if pool is None:
        raise RuntimeError("Database connection pool is not initialized")
    async with pool.connection() as connection:
        return await _execute(connection)


async def update_customer_odoo_id(customer_id: int, odoo_partner_id: int, conn: Optional[psycopg.AsyncConnection] = None) -> None:
    """Associate customer record with Odoo res.partner ID."""
    async def _execute(c: psycopg.AsyncConnection) -> None:
        async with c.cursor() as cur:
            await cur.execute(
                "UPDATE customers SET odoo_partner_id = %s WHERE id = %s",
                (odoo_partner_id, customer_id),
            )

    if conn is not None:
        await _execute(conn)
    elif pool is not None:
        async with pool.connection() as connection:
            await _execute(connection)


async def save_message(
    customer_id: int,
    role: str,
    content: str,
    wa_message_id: Optional[str] = None,
    conn: Optional[psycopg.AsyncConnection] = None,
) -> bool:
    """
    Save a message to the database.
    Returns False if wa_message_id is provided and already exists (duplicate webhook payload).
    """
    async def _execute(c: psycopg.AsyncConnection) -> bool:
        async with c.cursor() as cur:
            if wa_message_id:
                await cur.execute(
                    "SELECT id FROM messages WHERE wa_message_id = %s",
                    (wa_message_id,),
                )
                if await cur.fetchone():
                    logger.info("Duplicate wa_message_id detected: %s", wa_message_id)
                    return False

            await cur.execute(
                """
                INSERT INTO messages (customer_id, role, content, wa_message_id)
                VALUES (%s, %s, %s, %s)
                """,
                (customer_id, role, content, wa_message_id),
            )
            return True

    if conn is not None:
        return await _execute(conn)

    if pool is None:
        raise RuntimeError("Database connection pool is not initialized")
    async with pool.connection() as connection:
        return await _execute(connection)


async def get_recent_messages(
    customer_id: int,
    limit: int = 10,
    conn: Optional[psycopg.AsyncConnection] = None,
) -> List[Dict[str, str]]:
    """
    Fetch recent messages for customer in Claude format: [{"role": "user"/"assistant", "content": "..."}]
    Returned in chronological order (oldest first).
    """
    async def _execute(c: psycopg.AsyncConnection) -> List[Dict[str, str]]:
        async with c.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """
                SELECT role, content FROM (
                    SELECT role, content, created_at
                    FROM messages
                    WHERE customer_id = %s
                    ORDER BY created_at DESC, id DESC
                    LIMIT %s
                ) sub
                ORDER BY created_at ASC, id ASC
                """,
                (customer_id, limit),
            )
            rows = await cur.fetchall()
            return [{"role": r["role"], "content": r["content"]} for r in rows]

    if conn is not None:
        return await _execute(conn)

    if pool is None:
        raise RuntimeError("Database connection pool is not initialized")
    async with pool.connection() as connection:
        return await _execute(connection)


async def set_bot_active(
    customer_id: int,
    active: bool,
    conn: Optional[psycopg.AsyncConnection] = None,
) -> None:
    """Set the bot_active state for a customer."""
    async def _execute(c: psycopg.AsyncConnection) -> None:
        async with c.cursor() as cur:
            await cur.execute(
                "UPDATE customers SET bot_active = %s WHERE id = %s",
                (active, customer_id),
            )

    if conn is not None:
        await _execute(conn)
    elif pool is not None:
        async with pool.connection() as connection:
            await _execute(connection)


async def is_bot_active(
    customer_id: int,
    conn: Optional[psycopg.AsyncConnection] = None,
) -> bool:
    """Check if bot is currently active for a customer."""
    async def _execute(c: psycopg.AsyncConnection) -> bool:
        async with c.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "SELECT bot_active FROM customers WHERE id = %s",
                (customer_id,),
            )
            res = await cur.fetchone()
            return res["bot_active"] if res else False

    if conn is not None:
        return await _execute(conn)

    if pool is None:
        raise RuntimeError("Database connection pool is not initialized")
    async with pool.connection() as connection:
        return await _execute(connection)
