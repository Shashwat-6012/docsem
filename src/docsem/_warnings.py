import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class WarningAggregator:
    """Collect recoverable issues during one document run and emit a summary warning."""

    def __init__(self, logger: logging.Logger, doc_id: str):
        self._logger = logger
        self._doc_id = doc_id
        self._pages_by_issue: dict[str, list[int]] = defaultdict(list)

    def add(self, issue: str, page: int) -> None:
        """Record a recoverable issue on a page without logging immediately."""
        self._pages_by_issue[issue].append(page)

    def flush(self) -> None:
        """Emit one warning per issue type. Call once at the end of a document run."""
        for issue, pages in self._pages_by_issue.items():
            self._logger.warning(
                "%s on %d page(s)",
                issue,
                len(pages),
                extra={
                    "docsem_doc_id": self._doc_id,
                    "docsem_issue": issue,
                    "docsem_count": len(pages),
                    "docsem_sample_pages": pages[:10],
                },
            )
        self._pages_by_issue.clear()
