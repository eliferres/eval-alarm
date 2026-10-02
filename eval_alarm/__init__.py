"""Regression alarms for prompts, skills and agent instructions."""

__version__ = "0.1.0"

from .cli import main  # noqa: E402  after __version__, which cli imports

__all__ = ["main", "__version__"]
