import logging

logger = logging.getLogger(__name__)


def enable_default_logging(level: int = logging.INFO) -> None:
    """Convenience for scripts or notebooks that want simple package logging."""
    pkg_logger = logging.getLogger("docsem")
    pkg_logger.setLevel(level)
    if not any(isinstance(handler, logging.StreamHandler) for handler in pkg_logger.handlers):
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
        )
        pkg_logger.addHandler(handler)
