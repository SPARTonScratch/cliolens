"""Shared fixtures for the ClioLens test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def make_project(tmp_path: Path):
    """Factory: build a project tree from a ``{relative_path: content}`` map.

    ``content`` may be ``str`` (written as UTF-8) or ``bytes`` (raw).
    Returns the project root directory.
    """

    def _make(files: dict[str, str | bytes], root: str = "demo") -> Path:
        base = tmp_path / root
        base.mkdir(parents=True, exist_ok=True)
        for rel, content in files.items():
            target = base / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, bytes):
                target.write_bytes(content)
            else:
                target.write_text(content, encoding="utf-8")
        return base

    return _make
