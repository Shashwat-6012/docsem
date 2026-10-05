from __future__ import annotations

import json
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import Any, Literal, TypedDict, cast


class Message(TypedDict):
    role: Literal["system", "user", "assistant"]
    content: str


class LLMError(RuntimeError):
    pass


class LLMProvider(ABC):
    """Chat messages + JSON schema in, parsed JSON dict out."""

    @abstractmethod
    def _complete(self, messages: list[Message], schema: dict, max_tokens: int) -> str:
        """Return the raw model text (expected to be a JSON document)."""

    def generate_json(
        self, messages: Sequence[Message], schema: dict, *, max_tokens: int = 512
    ) -> dict[str, Any]:
        raw = self._complete(list(messages), schema, max_tokens)
        try:
            return cast(dict[str, Any], json.loads(_strip_fences(raw)))
        except json.JSONDecodeError as e:
            raise LLMError(f"Provider returned invalid JSON: {raw[:200]!r}") from e


def _strip_fences(text: str) -> str:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        t = t.rsplit("```", 1)[0]
    return t.strip()


def fold_system(messages: list[Message]) -> list[Message]:
    """For chat templates without a system role (Gemma): prepend it to the first user turn."""
    system = [m["content"] for m in messages if m["role"] == "system"]
    rest: list[Message] = [dict(m) for m in messages if m["role"] != "system"]  # type: ignore[misc]
    if system:
        prefix = "\n\n".join(system)
        if rest and rest[0]["role"] == "user":
            rest[0]["content"] = f"{prefix}\n\n{rest[0]['content']}"
        else:
            rest.insert(0, {"role": "user", "content": prefix})
    return rest
