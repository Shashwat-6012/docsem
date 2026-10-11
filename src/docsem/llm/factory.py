from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from ..exceptions import ConfigurationError
from .base import LLMProvider


class LLMProviderName(StrEnum):
    GEMINI = "gemini"
    OPENAI = "openai"
    ANTHROPIC = "anthropic"
    LOCAL = "local"


@dataclass(frozen=True)
class LLMConfig:
    provider: LLMProviderName
    model: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


ProviderBuilder = Callable[[LLMConfig], LLMProvider]
_REGISTRY: dict[LLMProviderName, ProviderBuilder] = {}


def register_provider(name: LLMProviderName) -> Callable[[ProviderBuilder], ProviderBuilder]:
    def decorator(builder: ProviderBuilder) -> ProviderBuilder:
        if name in _REGISTRY:
            raise ConfigurationError(f"LLM provider {name.value!r} is already registered.")
        _REGISTRY[name] = builder
        return builder

    return decorator


def create_provider(cfg: LLMConfig) -> LLMProvider:
    try:
        builder = _REGISTRY[cfg.provider]
    except KeyError:
        raise ConfigurationError(
            f"No LLM provider registered for {cfg.provider.value!r}."
        ) from None
    return builder(cfg)


@register_provider(LLMProviderName.GEMINI)
def _build_gemini(cfg: LLMConfig) -> LLMProvider:
    if not os.environ.get("GEMINI_API_KEY"):
        raise ConfigurationError(
            "GEMINI_API_KEY is not set. Set it to use Gemini, or run without an LLM "
            "by configuring AnalysisConfig(llm=None)."
        )

    from .gemini import GeminiProvider

    return GeminiProvider(model=cfg.model or "gemini-3.5-flash-lite", **cfg.options)


@register_provider(LLMProviderName.LOCAL)
def _build_local(cfg: LLMConfig) -> LLMProvider:
    from .llama_cpp import LlamaCppProvider

    options = dict(cfg.options)
    if cfg.model is not None:
        options.setdefault("model_path", cfg.model)
    return LlamaCppProvider(**options)


DEFAULT_LLM = LLMConfig(LLMProviderName.GEMINI, model="gemini-3.5-flash-lite")
