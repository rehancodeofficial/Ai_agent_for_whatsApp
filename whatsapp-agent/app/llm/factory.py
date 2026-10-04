import logging
from app.llm.base import LLMClient
from app.llm.gemini_client import GeminiLLMClient

logger = logging.getLogger(__name__)


def get_llm_client() -> LLMClient:
    """Returns Google Gemini LLM Client as the sole AI provider."""
    logger.info("Initializing Google Gemini LLM client.")
    return GeminiLLMClient()
