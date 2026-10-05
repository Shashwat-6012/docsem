import logging

import pytest

from docsem.api import DocSem
from docsem.extraction.base import BaseExtractor, ExtractionInput, ExtractionResult
from docsem.ir.build import IRBuilder
from docsem.ir.document import DocumentIR


class FakeExtractor(BaseExtractor):
    def __init__(self, result: ExtractionResult):
        self.result = result

    def extract(self, input_data: ExtractionInput) -> ExtractionResult:
        return self.result


class FakeBuilder(IRBuilder):
    def __init__(self):
        pass

    def build(self, result: ExtractionResult) -> DocumentIR:
        return DocumentIR(source=result)


@pytest.fixture
def sample_document(tmp_path):
    source = tmp_path / "sample.pdf"
    source.write_bytes(b"%PDF-1.4\n")
    return source


def test_package_is_silent_by_default(sample_document, capsys):
    doc = DocSem()
    doc.extractor = FakeExtractor(ExtractionResult(page_count=1))
    doc.builder = FakeBuilder()

    doc.process(sample_document)

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
    assert not any(
        isinstance(handler, logging.StreamHandler) and not isinstance(handler, logging.NullHandler)
        for handler in logging.getLogger("docsem").handlers
    )


def test_start_and_finish_info(sample_document, caplog):
    result = ExtractionResult(page_count=2)
    doc = DocSem()
    doc.extractor = FakeExtractor(result)
    doc.builder = FakeBuilder()

    with caplog.at_level(logging.INFO, logger="docsem"):
        doc.process(sample_document)

    start_records = [
        record for record in caplog.records if record.getMessage() == "document processing started"
    ]
    finish_records = [
        record for record in caplog.records if record.getMessage() == "document processing finished"
    ]

    assert len(start_records) == 1
    assert len(finish_records) == 1
    assert hasattr(finish_records[0], "docsem_duration_ms")


def test_aggregated_warning(sample_document, caplog):
    result = ExtractionResult(page_count=4)
    doc = DocSem()
    doc.extractor = FakeExtractor(result)
    doc.builder = FakeBuilder()

    with caplog.at_level(logging.WARNING, logger="docsem"):
        doc.process(sample_document)

    warning_records = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(warning_records) == 1
    assert warning_records[0].getMessage() == "no_text_layer on 4 page(s)"
    assert warning_records[0].docsem_issue == "no_text_layer"
    assert warning_records[0].docsem_count == 4


def test_no_per_page_info(sample_document, caplog):
    result = ExtractionResult(page_count=3)
    doc = DocSem()
    doc.extractor = FakeExtractor(result)
    doc.builder = FakeBuilder()

    with caplog.at_level(logging.INFO, logger="docsem"):
        doc.process(sample_document)

    info_records = [
        record
        for record in caplog.records
        if record.levelno == logging.INFO and record.name.startswith("docsem")
    ]
    assert len(info_records) == 2


def test_no_content_leakage(sample_document, caplog):
    result = ExtractionResult(page_count=1, raw_text="top secret extracted text")
    doc = DocSem()
    doc.extractor = FakeExtractor(result)
    doc.builder = FakeBuilder()

    with caplog.at_level(logging.INFO, logger="docsem"):
        doc.process(sample_document)

    for record in caplog.records:
        assert "top secret extracted text" not in record.getMessage()
        for value in record.__dict__.values():
            if isinstance(value, str):
                assert "top secret extracted text" not in value
