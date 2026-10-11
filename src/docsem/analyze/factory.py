# analyze/factory.py
"""
Builds the configured analyzer list for DocumentIR construction.

Resolves, per analyzer, which LLM provider (if any) it should use from
AnalysisConfig, and shares one provider instance between analyzers that
resolve to the same LLMConfig.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable
from typing import ClassVar

from ..config import AnalysisConfig
from ..exceptions import ConfigurationError
from ..llm import LLMProvider
from ..llm.factory import LLMConfig, create_provider
from .base import Analyzer
from .table import TableContinuationAnalyzer, TableStructureAnalyzer

logger = logging.getLogger(__name__)

AnalyzerBuilder = Callable[[LLMProvider | None], Analyzer]


class AnalyzerFactory:
    """
    Creates analyzers from an AnalysisConfig.

    - Analyzer builders are registered by Analyzer.name (class-level registry).
    - Providers are created lazily, cached per distinct LLMConfig, and shared.
    - An analyzer whose resolved LLMConfig is None gets provider=None
      (rule-based behaviour only); no provider is instantiated for it.

    Example
    -------
    >>> factory = AnalyzerFactory(config.analysis)
    >>> analyzers = factory.build()
    """

    _registry: ClassVar[dict[str, AnalyzerBuilder]] = {}

    def __init__(
        self,
        config: AnalysisConfig,
        *,
        provider_factory: Callable[[LLMConfig], LLMProvider] | None = None,
    ) -> None:
        self._config = config
        self._provider_factory = (
            provider_factory if provider_factory is not None else create_provider
        )
        self._providers: dict[str, LLMProvider] = {}
        self._lock = threading.Lock()

    # ---- registry ----------------------------------------------------------
    @classmethod
    def register(cls, name: str) -> Callable[[AnalyzerBuilder], AnalyzerBuilder]:
        """Decorator: register a builder under an analyzer name."""

        def decorator(builder: AnalyzerBuilder) -> AnalyzerBuilder:
            if name in cls._registry:
                raise ConfigurationError(f"Analyzer {name!r} is already registered.")
            cls._registry[name] = builder
            return builder

        return decorator

    @classmethod
    def available(cls) -> tuple[str, ...]:
        """Names of all registered analyzers."""
        return tuple(cls._registry)

    # ---- public API --------------------------------------------------------
    def build(self) -> list[Analyzer]:
        """Build every analyzer listed in config.enabled, in order."""
        return [self.build_one(name) for name in self._config.enabled]

    def build_one(self, name: str) -> Analyzer:
        try:
            builder = self._registry[name]
        except KeyError:
            raise ConfigurationError(
                f"Unknown analyzer {name!r}. Available: {', '.join(self.available())}"
            ) from None
        return builder(self.provider_for(name))

    def provider_for(self, analyzer_name: str) -> LLMProvider | None:
        """Resolve (and lazily create) the provider for one analyzer."""
        return self._get_provider(self._config.llm_for(analyzer_name))

    # ---- provider cache ----------------------------------------------------
    def _get_provider(self, cfg: LLMConfig | None) -> LLMProvider | None:
        if cfg is None:
            return None
        key = self._cache_key(cfg)
        with self._lock:
            provider = self._providers.get(key)
            if provider is None:
                logger.debug("creating LLM provider %s (%s)", cfg.provider, cfg.model)
                provider = self._provider_factory(cfg)
                self._providers[key] = provider
            return provider

    @staticmethod
    def _cache_key(cfg: LLMConfig) -> str:
        # json with sorted keys: stable for nested options, unlike sorted(items()),
        # which breaks on unorderable values. default=repr covers odd types.
        return json.dumps(
            {"provider": str(cfg.provider.value), "model": cfg.model, "options": cfg.options},
            sort_keys=True,
            default=repr,
        )


# ---- built-in analyzers ----------------------------------------------------
@AnalyzerFactory.register("table_structure")
def _table_structure(provider: LLMProvider | None) -> Analyzer:
    return TableStructureAnalyzer(provider=provider)


@AnalyzerFactory.register("table_continuation")
def _table_continuation(provider: LLMProvider | None) -> Analyzer:
    return TableContinuationAnalyzer(provider=provider)
