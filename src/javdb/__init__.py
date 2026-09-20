"""Search JAVDatabase and export movie metadata using only the standard library."""

__version__ = "0.2.0"

from .client import Client, JavDBError
from .export import to_json, to_nfo
from .parsing import parse_movie, parse_search

__all__ = ["Client", "JavDBError", "parse_movie", "parse_search", "to_json", "to_nfo"]
