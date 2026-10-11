from unittest.mock import Mock

import pytest

from docsem.config import DocSemConfig
from docsem.exceptions import ConfigurationError
from docsem.extraction.factory import ExtractorConfig, ProviderName, build_extractor


def test_azure_is_the_default_extraction_provider():
    assert ExtractorConfig().provider == ProviderName.AZURE
    assert DocSemConfig.default().extraction.provider == ProviderName.AZURE


def test_azure_extractor_uses_environment_credentials(monkeypatch):
    monkeypatch.setenv("AZURE_ENDPOINT", "https://env.example/")
    monkeypatch.setenv("AZURE_API_KEY", "environment-key")
    azure_extractor = Mock()
    monkeypatch.setattr(
        "docsem.extraction.providers.azure.AzureExtractor",
        azure_extractor,
    )

    extractor = build_extractor(ExtractorConfig())

    assert extractor is azure_extractor.return_value
    azure_extractor.assert_called_once_with(
        endpoint="https://env.example/",
        api_key="environment-key",
        model_id="prebuilt-document",
    )


def test_azure_options_override_environment_credentials(monkeypatch):
    monkeypatch.setenv("AZURE_ENDPOINT", "https://env.example/")
    monkeypatch.setenv("AZURE_API_KEY", "environment-key")
    azure_extractor = Mock()
    monkeypatch.setattr(
        "docsem.extraction.providers.azure.AzureExtractor",
        azure_extractor,
    )

    build_extractor(
        ExtractorConfig(
            options={
                "endpoint": "https://options.example/",
                "api_key": "options-key",
                "model_id": "custom-model",
            }
        )
    )

    azure_extractor.assert_called_once_with(
        endpoint="https://options.example/",
        api_key="options-key",
        model_id="custom-model",
    )


def test_explicit_empty_azure_options_do_not_fall_back_to_environment(monkeypatch):
    monkeypatch.setenv("AZURE_ENDPOINT", "https://env.example/")
    monkeypatch.setenv("AZURE_API_KEY", "environment-key")

    with pytest.raises(ConfigurationError, match="Azure extraction requires"):
        build_extractor(
            ExtractorConfig(
                options={
                    "endpoint": "",
                    "api_key": "options-key",
                }
            )
        )


@pytest.mark.parametrize(
    ("endpoint", "api_key"),
    [
        (None, "environment-key"),
        ("https://env.example/", None),
    ],
)
def test_azure_extractor_requires_both_credentials(monkeypatch, endpoint, api_key):
    monkeypatch.delenv("AZURE_ENDPOINT", raising=False)
    monkeypatch.delenv("AZURE_API_KEY", raising=False)
    options = {}
    if endpoint:
        options["endpoint"] = endpoint
    if api_key:
        options["api_key"] = api_key

    with pytest.raises(ConfigurationError, match="AZURE_ENDPOINT and AZURE_API_KEY"):
        build_extractor(ExtractorConfig(options=options))
