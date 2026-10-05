from importlib import import_module
from typing import cast

from .base import LLMError, LLMProvider, Message

_REGISTRY = {
    "llama_cpp": ("docsem.llm.llama_cpp", "LlamaCppProvider"),
    "gemini": ("docsem.llm.gemini", "GeminiProvider"),
}


def create_provider(name: str, **kwargs) -> LLMProvider:
    try:
        module, cls = _REGISTRY[name]
    except KeyError:
        raise ValueError(f"Unknown provider {name!r}; choose from {sorted(_REGISTRY)}") from None
    return cast(LLMProvider, getattr(import_module(module), cls)(**kwargs))


__all__ = ["LLMProvider", "LLMError", "Message", "create_provider"]
