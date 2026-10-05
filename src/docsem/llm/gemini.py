from __future__ import annotations

import logging
import threading
from typing import Any, cast

from .base import LLMProvider, Message

log = logging.getLogger(__name__)


class GeminiProvider(LLMProvider):
    def __init__(
        self,
        model: str = "gemini-3.5-flash-lite",
        temperature: float = 0.0,
        max_output_tokens: int = 2048,
    ):
        try:
            from google import genai
        except ImportError as e:
            raise ImportError("Install with: pip install docsem[gemini]") from e
        self._client = genai.Client()
        self._lock = threading.Lock()  # a Client instance is not thread-safe
        self.model = model
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    @staticmethod
    def _to_steps(messages: list[Message]) -> tuple[str | None, list[dict]]:
        """Split out system prompt and convert chat turns to Interactions steps."""
        system_parts: list[str] = []
        steps: list[dict] = []
        for m in messages:
            if m["role"] == "system":
                system_parts.append(m["content"])
            elif m["role"] == "user":
                steps.append(
                    {"type": "user_input", "content": [{"type": "text", "text": m["content"]}]}
                )
            else:  # assistant
                steps.append(
                    {"type": "model_output", "content": [{"type": "text", "text": m["content"]}]}
                )
        system = "\n\n".join(system_parts) if system_parts else None
        return system, steps

    def _complete(self, messages: list[Message], schema: dict, max_tokens: int) -> str:
        system, steps = self._to_steps(messages)

        kwargs: dict[str, Any] = dict(
            model=self.model,
            input=steps,
            response_format={
                "type": "text",
                "mime_type": "application/json",
                "schema": schema,
            },
            store=False,  # stateless: we resend history ourselves, no server retention
        )
        if system:
            kwargs["system_instruction"] = system

        with self._lock:
            interaction = self._client.interactions.create(**kwargs)
            print(interaction.output_text)
        return cast(str, interaction.output_text)
