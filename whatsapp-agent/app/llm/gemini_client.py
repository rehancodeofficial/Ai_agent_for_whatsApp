import logging
from typing import Any, Dict, List, Optional
from google import genai
from google.genai import types

from app.config import settings
from app.llm.base import LLMClient, LLMResponse

logger = logging.getLogger(__name__)


class GeminiLLMClient(LLMClient):
    """Wraps Google Gen AI SDK (`google-genai`) to implement LLMClient interface."""

    def __init__(
        self,
        client: Optional[genai.Client] = None,
        model: Optional[str] = None,
    ):
        self._model = model
        if client:
            self.client = client
        else:
            api_key = settings.gemini_api_key
            if not api_key:
                logger.warning("GEMINI_API_KEY is not set.")
            self.client = genai.Client(api_key=api_key or "placeholder")

    @property
    def model(self) -> str:
        return self._model or settings.gemini_model

    def _convert_tools(self, tools: List[Dict[str, Any]]) -> Optional[List[types.Tool]]:
        """Converts OpenAI/Anthropic style JSON schema tools to Gemini google.genai types.Tool list."""
        if not tools:
            return None

        declarations = []
        for tool in tools:
            name = tool.get("name")
            description = tool.get("description", "")
            parameters = tool.get("input_schema") or tool.get("parameters")

            # Deep clean parameters dict for Gemini FunctionDeclaration compatibility
            clean_params = None
            if parameters:
                clean_params = self._clean_schema(parameters)

            decl = types.FunctionDeclaration(
                name=name,
                description=description,
                parameters=clean_params,
            )
            declarations.append(decl)

        return [types.Tool(function_declarations=declarations)]

    def _clean_schema(self, schema: Dict[str, Any]) -> Dict[str, Any]:
        """Ensures JSON schema dictionary is compliant with Gemini API requirements."""
        if not isinstance(schema, dict):
            return schema

        cleaned = {}
        for k, v in schema.items():
            if k == "$schema":
                continue
            if isinstance(v, dict):
                cleaned[k] = self._clean_schema(v)
            elif isinstance(v, list):
                cleaned[k] = [self._clean_schema(item) if isinstance(item, dict) else item for item in v]
            else:
                cleaned[k] = v
        return cleaned

    def _convert_messages(
        self, messages: List[Dict[str, Any]]
    ) -> List[types.Content]:
        """Converts standard message dict trajectory to Gemini types.Content objects."""
        contents: List[types.Content] = []

        for msg in messages:
            role = msg.get("role")
            content = msg.get("content")
            tool_calls = msg.get("tool_calls")
            tool_call_id = msg.get("tool_call_id")
            name = msg.get("name")

            # 1. System prompt is handled separately in config, ignore here if present
            if role == "system":
                continue

            # 2. Assistant message
            if role in ("assistant", "model"):
                parts: List[types.Part] = []
                if content:
                    parts.append(types.Part.from_text(text=str(content)))
                if tool_calls:
                    for tc in tool_calls:
                        if "raw_part" in tc and tc["raw_part"]:
                            parts.append(tc["raw_part"])
                        else:
                            fn_name = tc.get("name") or (tc.get("function", {}).get("name") if isinstance(tc.get("function"), dict) else None)
                            args = tc.get("arguments") or (tc.get("function", {}).get("arguments") if isinstance(tc.get("function"), dict) else {})
                            if isinstance(args, str):
                                import json
                                try:
                                    args = json.loads(args)
                                except Exception:
                                    args = {}
                            if fn_name:
                                parts.append(types.Part.from_function_call(name=fn_name, args=args))
                if parts:
                    contents.append(types.Content(role="model", parts=parts))

            # 3. Tool execution result message (role='tool' or role='user' with tool_call_id)
            elif role == "tool" or (role == "user" and tool_call_id):
                fn_name = name or msg.get("tool_name", "tool_result")
                response_content = content
                if isinstance(response_content, str):
                    import json
                    try:
                        response_dict = json.loads(response_content)
                    except Exception:
                        response_dict = {"result": response_content}
                elif isinstance(response_content, dict):
                    response_dict = response_content
                else:
                    response_dict = {"result": str(response_content)}

                part = types.Part.from_function_response(
                    name=fn_name,
                    response=response_dict
                )
                contents.append(types.Content(role="user", parts=[part]))

            # 4. Standard User message (text, or a list of text/media parts)
            elif role == "user":
                contents.append(types.Content(role="user", parts=self._user_parts(content)))

        return contents

    def _user_parts(self, content: Any) -> List[types.Part]:
        """
        Build the parts of a user message. `content` is either a plain string or
        a list of parts: {"type": "text", "text": ...} and
        {"type": "media", "mime_type": ..., "data": <bytes>}.
        """
        if not isinstance(content, list):
            return [types.Part.from_text(text=str(content or ""))]

        parts: List[types.Part] = []
        for item in content:
            if not isinstance(item, dict):
                parts.append(types.Part.from_text(text=str(item)))
                continue

            if item.get("type") == "media" and item.get("data"):
                parts.append(
                    types.Part.from_bytes(
                        data=item["data"],
                        mime_type=item.get("mime_type") or "application/octet-stream",
                    )
                )
            elif item.get("text"):
                parts.append(types.Part.from_text(text=str(item["text"])))

        return parts or [types.Part.from_text(text="")]

    def _build_config(
        self,
        system: str,
        gemini_tools: Optional[List[types.Tool]],
        max_tokens: int,
        with_thinking: bool = True,
    ) -> types.GenerateContentConfig:
        """
        Build the request config. Thinking tokens come out of max_output_tokens,
        so on a thinking model an unbounded budget can leave nothing for the
        visible answer and the reply arrives cut off mid-sentence.
        """
        kwargs: Dict[str, Any] = {
            "system_instruction": system if system else None,
            "tools": gemini_tools,
            "max_output_tokens": max_tokens,
            "temperature": 0.3,
        }
        if with_thinking and settings.gemini_thinking_budget >= 0:
            kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=settings.gemini_thinking_budget
            )
        return types.GenerateContentConfig(**kwargs)

    async def chat(
        self,
        system: str,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int = 500,
    ) -> LLMResponse:
        """Sends chat request to Google Gemini API asynchronously using `google-genai` SDK."""
        try:
            gemini_tools = self._convert_tools(tools)
            contents = self._convert_messages(messages)

            # Call Gemini async API. Models without thinking support reject
            # thinking_config outright, so fall back to a plain request.
            try:
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=self._build_config(system, gemini_tools, max_tokens),
                )
            except Exception as e:
                if "thinking" not in str(e).lower():
                    raise
                logger.warning("Model %s rejected thinking_config (%s). Retrying without it.", self.model, e)
                response = await self.client.aio.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=self._build_config(system, gemini_tools, max_tokens, with_thinking=False),
                )

            text_output: Optional[str] = None
            tool_calls_output: List[Dict[str, Any]] = []

            if response.candidates and len(response.candidates) > 0:
                candidate = response.candidates[0]
                if candidate.content and candidate.content.parts:
                    for part in candidate.content.parts:
                        if part.text:
                            text_output = (text_output or "") + part.text
                        if part.function_call:
                            fc = part.function_call
                            tool_calls_output.append({
                                "id": f"call_{fc.name}",
                                "name": fc.name,
                                "arguments": dict(fc.args) if fc.args else {},
                                "raw_part": part,
                            })

            # Usage tracking
            usage_dict = {"input_tokens": 0, "output_tokens": 0}
            if hasattr(response, "usage_metadata") and response.usage_metadata:
                usage_dict["input_tokens"] = getattr(response.usage_metadata, "prompt_token_count", 0) or 0
                # Thinking tokens are billed as output, so count them too.
                usage_dict["output_tokens"] = (
                    (getattr(response.usage_metadata, "candidates_token_count", 0) or 0)
                    + (getattr(response.usage_metadata, "thoughts_token_count", 0) or 0)
                )

            stop_reason = "end_turn"
            if tool_calls_output:
                stop_reason = "tool_use"
            elif response.candidates and getattr(response.candidates[0], "finish_reason", None):
                stop_reason = str(response.candidates[0].finish_reason).lower()

            return LLMResponse(
                text=text_output,
                tool_calls=tool_calls_output,
                usage=usage_dict,
                stop_reason=stop_reason,
            )

        except Exception as e:
            logger.error(f"Gemini API error: {e}", exc_info=True)
            raise e
