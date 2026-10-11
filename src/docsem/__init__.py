import logging
from importlib.metadata import version

__version__ = version("docsem")
logging.getLogger(__name__).addHandler(logging.NullHandler())
