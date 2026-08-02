"""Shared utilities: token estimation, size parsing/formatting, path
normalisation, and small terminal helpers.
"""

from __future__ import annotations

import math
import os
import re
import sys
from pathlib import PurePath

__all__ = [
    "estimate_tokens",
    "format_count",
    "format_size",
    "parse_size",
    "style",
    "supports_color",
    "to_posix",
]

_BYTES_PER_UNIT = {"B": 1, "KB": 1024, "MB": 1024**2, "GB": 1024**3}
_SIZE_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(B|KB|MB|GB)?\s*$", re.IGNORECASE)

_ANSI_CODES = {
    "bold": "1",
    "dim": "2",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
}


def estimate_tokens(text: str) -> int:
    """Rough LLM token estimate: ``ceil(characters / 4)``."""
    return math.ceil(len(text) / 4)


def parse_size(value: str) -> int:
    """Parse a human size like ``100KB``, ``1.5MB`` or ``512`` into bytes.

    Raises ``ValueError`` on malformed input.
    """
    match = _SIZE_RE.match(value)
    if match is None:
        raise ValueError(
            f"invalid size {value!r} -> expected forms like '100KB', '1.5MB', '2GB' or '512'"
        )
    number = float(match.group(1))
    unit = (match.group(2) or "B").upper()
    return int(number * _BYTES_PER_UNIT[unit])


def format_size(num_bytes: float) -> str:
    """Render a byte count as ``512B`` / ``1.0KB`` / ``3.2MB``."""
    n = float(num_bytes)
    if n < 1024:
        return f"{int(n)}B"
    for unit in ("KB", "MB", "GB", "TB"):
        n /= 1024.0
        if n < 1024 or unit == "TB":
            return f"{n:.1f}{unit}"
    return f"{n:.1f}TB"  # pragma: no cover


def format_count(n: int) -> str:
    """``1234567`` -> ``'1,234,567'``."""
    return f"{n:,}"


def to_posix(path: str | PurePath) -> str:
    """Normalise any path-ish value to a forward-slash string."""
    return PurePath(path).as_posix()


def supports_color(stream=None) -> bool:
    """True when *stream* (default: stderr) can display ANSI colors."""
    stream = stream if stream is not None else sys.stderr
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if os.environ.get("TERM") == "dumb":
        return False
    try:
        return bool(stream.isatty())
    except (AttributeError, ValueError):
        return False


def style(text: str, *codes: str) -> str:
    """Wrap *text* in ANSI codes when the terminal supports color.

    Honors the ``NO_COLOR`` / ``FORCE_COLOR`` conventions. Used only for
    stderr diagnostics; the dump itself is always plain markdown.
    """
    if not supports_color():
        return text
    seq = ";".join(_ANSI_CODES[c] for c in codes if c in _ANSI_CODES)
    return f"\x1b[{seq}m{text}\x1b[0m" if seq else text
