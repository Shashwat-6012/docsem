from unittest.mock import Mock

import pytest

from docsem.analyze.factory import AnalyzerFactory
from docsem.analyze.table import TableContinuationAnalyzer, TableStructureAnalyzer
from docsem.api import DocSem
from docsem.config import AnalysisConfig, DocSemConfig
from docsem.exceptions import ConfigurationError
from docsem.ir.build import IRBuilder
from docsem.llm import factory as llm_factory
from docsem.llm.base import LLMProvider
from docsem.llm.factory import DEFAULT_LLM


def test_analysis_config_defaults_to_gemini():
    assert AnalysisConfig().llm == DEFAULT_LLM
    assert AnalysisConfig().llm_for("table_structure") == DEFAULT_LLM


def test_analysis_config_can_disable_llm_for_every_analyzer():
    config = AnalysisConfig(llm=None)
    provider_factory = Mock()

    analyzers = AnalyzerFactory(config, provider_factory=provider_factory).build()

    assert all(
        isinstance(analyzer, (TableStructureAnalyzer, TableContinuationAnalyzer))
        and analyzer.provider is None
        for analyzer in analyzers
    )
    provider_factory.assert_not_called()


def test_none_override_disables_only_the_selected_analyzer():
    provider = Mock(spec=LLMProvider)
    config = AnalysisConfig(overrides={"table_structure": None})

    analyzers = AnalyzerFactory(config, provider_factory=lambda _config: provider).build()

    assert isinstance(analyzers[0], TableStructureAnalyzer)
    assert analyzers[0].name == "table_structure"
    assert analyzers[0].provider is None
    assert isinstance(analyzers[1], TableContinuationAnalyzer)
    assert analyzers[1].name == "table_continuation"
    assert analyzers[1].provider is provider


def test_docsem_default_constructs_with_injected_provider_factory():
    provider = Mock(spec=LLMProvider)
    calls = []

    def provider_factory(config):
        calls.append(config)
        return provider

    docsem = DocSem(DocSemConfig.default(), provider_factory=provider_factory)

    assert isinstance(docsem.builder, IRBuilder)
    assert calls == [DEFAULT_LLM]


def test_docsem_default_requires_gemini_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("docsem.analyze.factory.create_provider", llm_factory.create_provider)

    with pytest.raises(
        ConfigurationError,
        match=r"GEMINI_API_KEY.*AnalysisConfig\(llm=None\)",
    ):
        DocSem(DocSemConfig.default())


def test_docsem_constructs_offline_when_llm_is_disabled(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setattr("docsem.analyze.factory.create_provider", llm_factory.create_provider)
    config = DocSemConfig.default()
    config = DocSemConfig(
        extraction=config.extraction,
        analysis=AnalysisConfig(llm=None),
    )

    docsem = DocSem(config)

    assert isinstance(docsem.builder, IRBuilder)
    assert docsem.config.analysis.llm is None
