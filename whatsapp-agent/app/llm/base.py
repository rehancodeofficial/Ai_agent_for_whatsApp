from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class LLMResponse:
    text: Optional[str] = None
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    usage: Dict[str, int] = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})
    stop_reason: str = "end_turn"


class LLMClient(ABC):
    @abstractmethod
    async def chat(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int = 500,
    ) -> LLMResponse:
        """
        Send a chat completion request to the LLM backend.

        Parameters:
        - system: System prompt string.
        - messages: Trajectory of messages (role, content, tool_calls, etc.).
        - tools: Standard JSON tool schemas list.
        - max_tokens: Maximum tokens in completion response.

        Returns:
        - LLMResponse with text, tool_calls, usage, and stop_reason.
        """
        pass
