import asyncio
import logging
import time
import xmlrpc.client
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings

logger = logging.getLogger(__name__)


class OdooError(Exception):
    """Base exception for Odoo errors."""
    pass


class OdooConnectionError(OdooError):
    """Raised when XML-RPC connection fails or times out."""
    pass


class OdooAuthError(OdooError):
    """Raised when Odoo authentication fails."""
    pass


class OdooAccessDeniedError(OdooError):
    """Raised when Odoo denies access to a model/method."""
    pass


class OdooClient:
    """
    Interface for Odoo XML-RPC API with authentication, async execution,
    and in-memory TTL caching for read-only queries.
    """

    def __init__(
        self,
        url: Optional[str] = None,
        db: Optional[str] = None,
        user: Optional[str] = None,
        api_key: Optional[str] = None,
        cache_ttl_seconds: int = 90,
        timeout: int = 10,
    ):
        self.url = (url or settings.odoo_url).rstrip("/")
        self.db = db or settings.odoo_db
        self.user = user or settings.odoo_user
        self.api_key = api_key or settings.odoo_api_key
        self.cache_ttl = cache_ttl_seconds
        self.timeout = timeout
        self.uid: Optional[int] = None

        # Simple in-memory TTL cache: {cache_key: (timestamp, value)}
        self._cache: Dict[str, Tuple[float, Any]] = {}

    def authenticate(self) -> int:
        """Authenticate with Odoo and return user ID (uid)."""
        if self.uid is not None:
            return self.uid

        logger.info("Authenticating with Odoo at %s (db=%s, user=%s)", self.url, self.db, self.user)
        try:
            common = xmlrpc.client.ServerProxy(
                f"{self.url}/xmlrpc/2/common",
                allow_none=True,
                use_datetime=True,
            )
            uid = common.authenticate(self.db, self.user, self.api_key, {})
            if not uid:
                logger.error("Odoo authentication failed for user %s", self.user)
                raise OdooAuthError("Invalid Odoo credentials or DB name.")
            self.uid = uid
            logger.info("Successfully authenticated with Odoo, uid=%s", uid)
            return uid
        except OdooError:
            raise
        except (xmlrpc.client.ProtocolError, xmlrpc.client.Fault) as e:
            logger.error("Odoo RPC fault during authentication: %s", str(e))
            raise OdooAuthError(f"Odoo Auth Error: {e}") from e
        except Exception as e:
            logger.error("Failed to connect to Odoo server: %s", str(e))
            raise OdooConnectionError(f"Odoo Connection Error: {e}") from e

    async def authenticate_async(self) -> int:
        """Async wrapper for authenticate using asyncio.to_thread."""
        return await asyncio.to_thread(self.authenticate)

    def execute_kw(
        self,
        model: str,
        method: str,
        args: Optional[List[Any]] = None,
        kwargs: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Execute a method on an Odoo model via XML-RPC."""
        uid = self.authenticate()
        args = args or []
        kwargs = kwargs or {}

        try:
            models = xmlrpc.client.ServerProxy(
                f"{self.url}/xmlrpc/2/object",
                allow_none=True,
                use_datetime=True,
            )
            res = models.execute_kw(self.db, uid, self.api_key, model, method, args, kwargs)
            return res
        except xmlrpc.client.Fault as e:
            logger.error("Odoo Fault on %s.%s: %s", model, method, e.faultString)
            if "AccessError" in e.faultString or "Access Denied" in e.faultString:
                raise OdooAccessDeniedError(f"Access Denied for {model}.{method}: {e.faultString}") from e
            raise OdooError(f"Odoo Error: {e.faultString}") from e
        except Exception as e:
            logger.error("Odoo Connection Exception on %s.%s: %s", model, method, str(e))
            raise OdooConnectionError(f"Failed to communicate with Odoo: {e}") from e

    async def execute_kw_async(
        self,
        model: str,
        method: str,
        args: Optional[List[Any]] = None,
        kwargs: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Async non-blocking execution of execute_kw via thread pool."""
        return await asyncio.to_thread(self.execute_kw, model, method, args, kwargs)

    def search_read(
        self,
        model: str,
        domain: Optional[List[Any]] = None,
        fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
        use_cache: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Helper method to search and read records from Odoo.
        Optionally uses in-memory TTL cache.
        """
        domain = domain or []
        kwargs: Dict[str, Any] = {}
        if fields is not None:
            kwargs["fields"] = fields
        if limit is not None:
            kwargs["limit"] = limit

        cache_key = f"search_read:{model}:{str(domain)}:{str(fields)}:{limit}"
        if use_cache:
            cached = self._get_from_cache(cache_key)
            if cached is not None:
                logger.debug("Odoo cache hit for %s", cache_key)
                return cached

        results = self.execute_kw(model, "search_read", [domain], kwargs)
        if use_cache and isinstance(results, list):
            self._set_cache(cache_key, results)
        return results

    async def search_read_async(
        self,
        model: str,
        domain: Optional[List[Any]] = None,
        fields: Optional[List[str]] = None,
        limit: Optional[int] = None,
        use_cache: bool = False,
    ) -> List[Dict[str, Any]]:
        """Async non-blocking search_read."""
        return await asyncio.to_thread(self.search_read, model, domain, fields, limit, use_cache)

    def _get_from_cache(self, key: str) -> Optional[Any]:
        if key in self._cache:
            ts, val = self._cache[key]
            if time.time() - ts < self.cache_ttl:
                return val
            del self._cache[key]
        return None

    def _set_cache(self, key: str, value: Any) -> None:
        self._cache[key] = (time.time(), value)

    def clear_cache(self) -> None:
        self._cache.clear()
