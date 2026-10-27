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

            # 4. Standard User message
            elif role == "user":
                contents.append(types.Content(role="user", parts=[types.Part.from_text(text=str(content or ""))]))

        return contents

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

            # Guard: Gemini API throws if contents list is empty
            if not contents:
                logger.error("Gemini chat called with empty contents list — returning fallback")
                from app.llm.base import LLMResponse
                return LLMResponse(text="", tool_calls=[], usage={"input_tokens": 0, "output_tokens": 0})

            config = types.GenerateContentConfig(
                system_instruction=system if system else None,
                tools=gemini_tools,
                max_output_tokens=max_tokens,
                temperature=0.3,
            )

            # Call Gemini async API
            response = await self.client.aio.models.generate_content(
                model=self.model,
                contents=contents,
                config=config,
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
                usage_dict["output_tokens"] = getattr(response.usage_metadata, "candidates_token_count", 0) or 0

            stop_reason = "end_turn"
            if tool_calls_output:
                stop_reason = "tool_use"
            elif response.candidates and getattr(response.candidates[0], "finish_reason", None):
                raw_reason = str(response.candidates[0].finish_reason).lower()
                stop_reason = raw_reason
                if "max_tokens" in raw_reason or "length" in raw_reason:
                    logger.warning("Gemini stopped due to MAX_TOKENS — consider increasing max_tokens")
                elif "safety" in raw_reason or "recitation" in raw_reason:
                    logger.warning("Gemini stopped due to SAFETY filter — response may be empty")
                    text_output = text_output or ""

            return LLMResponse(
                text=text_output,
                tool_calls=tool_calls_output,
                usage=usage_dict,
                stop_reason=stop_reason,
            )

        except Exception as e:
            logger.error(f"Gemini API error: {e}", exc_info=True)
            raise e
