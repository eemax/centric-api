"""Fetch Centric PLM data into a local database with bronze and silver layers."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("centric-api")
except PackageNotFoundError:  # pragma: no cover - running from a source tree without install
    __version__ = "0.0.0"
