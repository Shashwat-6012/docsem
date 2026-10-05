from unittest.mock import Mock

import pytest


@pytest.fixture(autouse=True)
def disable_external_services(monkeypatch):
    monkeypatch.setattr("docsem.api.build_extractor", lambda _config: Mock())
    monkeypatch.setattr("docsem.ir.build.create_provider", lambda _name: None)
