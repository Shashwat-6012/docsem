from unittest.mock import Mock

import pytest

from docsem.llm.base import LLMProvider


@pytest.fixture(autouse=True)
def disable_external_services(monkeypatch):
    monkeypatch.setattr("docsem.api.build_extractor", lambda _config: Mock())
    monkeypatch.setattr(
        "docsem.analyze.factory.create_provider",
        lambda _config: Mock(spec=LLMProvider),
    )
