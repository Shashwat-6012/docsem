from docsem.extraction.base import BaseExtractor, ExtractionInput, ExtractionResult

class PaddleOCRExtractor(BaseExtractor):
    provider_name = "paddleocr"

    def __init__(self, lang: str = "en", use_gpu: bool = False):
        pass

    def extract(self, input_data: ExtractionInput) -> ExtractionResult:
        raise NotImplementedError("PaddleOCRExtractor.extract is not implemented yet.")
