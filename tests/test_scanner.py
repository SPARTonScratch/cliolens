import os
import sys

import pytest

from cliolens.scanner import BinaryDetector, BuiltInFilter, ProjectScanner

MB = 1024 * 1024


def scan(root, **kwargs):
    kwargs.setdefault("max_file_size", MB)
    return ProjectScanner(root, **kwargs).scan()


def rel_paths(result):
    return {e.relative_path for e in result.entries}


# ---------------------------------------------------------------- discovery


def test_empty_directory(tmp_path):
    result = scan(tmp_path)
    assert result.entries == []
    assert result.directories == []
    assert result.files_scanned == 0
    assert result.files_included == 0


def test_basic_discovery(make_project):
    root = make_project(
        {
            "main.py": "print('hi')\n",
            "src/utils/helpers.py": "def helper(): pass\n",
            "README.md": "# demo\n",
        }
    )
    result = scan(root)
    assert rel_paths(result) == {"main.py", "src/utils/helpers.py", "README.md"}
    assert "src" in result.directories
    assert "src/utils" in result.directories
    assert result.files_scanned == 3
    assert result.files_skipped == 0


def test_paths_with_spaces(make_project):
    root = make_project({"My Docs/hello world.py": "x = 1\n"})
    assert "My Docs/hello world.py" in rel_paths(scan(root))


def test_unicode_paths(make_project):
    root = make_project({"données/résumé.txt": "café\n"})
    assert "données/résumé.txt" in rel_paths(scan(root))


# ----------------------------------------------------------------- filtering


def test_builtin_exclusions(make_project):
    root = make_project(
        {
            "keep.py": "x = 1\n",
            ".git/config": "[core]\n",
            "node_modules/lib/index.js": "module.exports = 1\n",
            "__pycache__/keep.cpython-311.pyc": "junk",
            "dist/bundle.js": "var a;\n",
            "package-lock.json": "{}",
            ".DS_Store": "x",
        }
    )
    result = scan(root)
    assert rel_paths(result) == {"keep.py"}
    assert any("built-in" in s.reason for s in result.skipped)


def test_gitignore_respected(make_project):
    root = make_project(
        {
            ".gitignore": "*.log\ntemp/\n!important.log\n",
            "debug.log": "noise",
            "important.log": "keep me",
            "temp/scratch.txt": "x",
            "src/app.py": "pass\n",
        }
    )
    result = scan(root)
    assert rel_paths(result) == {".gitignore", "important.log", "src/app.py"}


def test_nested_gitignore_negation(make_project):
    root = make_project(
        {
            ".gitignore": "*.txt\n",
            "notes.txt": "x",
            "docs/.gitignore": "!keep.txt\n",
            "docs/keep.txt": "keep",
            "docs/drop.txt": "x",
        }
    )
    result = scan(root)
    assert rel_paths(result) == {".gitignore", "docs/.gitignore", "docs/keep.txt"}


def test_no_gitignore_flag(make_project):
    root = make_project({".gitignore": "*.md\n", "README.md": "r\n", "main.py": "p\n"})
    result = scan(root, use_gitignore=False)
    assert "README.md" in rel_paths(result)


def test_user_excludes(make_project):
    root = make_project(
        {
            "src/app.js": "1",
            "src/app.test.js": "2",
            "docs/guide.md": "# g",
            "README.md": "r",
        }
    )
    result = scan(root, user_excludes=["*.test.js", "docs/"])
    assert rel_paths(result) == {"src/app.js", "README.md"}


def test_malformed_gitignore_is_tolerated(make_project, capsys):
    root = make_project({".gitignore": "*.log\n", "a.log": "x", "b.py": "y"})
    result = scan(root)  # must not raise
    assert "b.py" in rel_paths(result)
    assert "a.log" not in rel_paths(result)


def test_case_folding_on_windows(monkeypatch):
    import cliolens.scanner as sc

    monkeypatch.setattr(sc, "IS_WINDOWS", True)
    filt = BuiltInFilter()
    assert filt.reason("NODE_MODULES", is_dir=True) is not None
    assert filt.reason("Thumbs.db", is_dir=False) is not None
    assert filt.reason("Package.JSON", is_dir=False) is None


def test_output_file_protected(make_project):
    root = make_project({"a.py": "x\n"})
    out = root / "context.md"
    out.write_text("previous dump", encoding="utf-8")
    result = scan(root, protect_paths={out})
    assert "context.md" not in rel_paths(result)
    assert any("protected" in s.reason for s in result.skipped)


# ------------------------------------------------------------------- binary


def test_binary_detector_null_bytes(tmp_path):
    p = tmp_path / "null.bin"
    p.write_bytes(b"abc\x00def")
    assert BinaryDetector.sniff(p)[0] is True


def test_binary_detector_control_chars(tmp_path):
    p = tmp_path / "ctrl.bin"
    p.write_bytes(bytes(range(1, 9)) * 10)
    assert BinaryDetector.sniff(p)[0] is True


def test_binary_detector_plain_text(tmp_path):
    p = tmp_path / "t.txt"
    p.write_text("hello\nworld\n", encoding="utf-8")
    is_bin, enc = BinaryDetector.sniff(p)
    assert is_bin is False
    assert enc == "utf-8"


def test_binary_detector_latin1(tmp_path):
    p = tmp_path / "l.txt"
    p.write_bytes("café résumé".encode("latin-1"))
    is_bin, enc = BinaryDetector.sniff(p)
    assert is_bin is False
    assert enc == "latin-1"


def test_binary_files_skipped_by_default(make_project):
    root = make_project({"app.py": "print(1)\n", "logo.png": b"\x89PNG\r\n\x1a\n\x00\x00"})
    result = scan(root)
    assert rel_paths(result) == {"app.py"}
    assert any(s.reason == "binary" for s in result.skipped)


def test_binary_files_listed_with_flag(make_project):
    root = make_project({"logo.png": b"\x89PNG\x00\x00"})
    result = scan(root, include_binary=True)
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.is_binary
    assert entry.content is None
    assert "png" in entry.mime_type


# ---------------------------------------------------------------- truncation


def test_large_file_truncated(make_project):
    lines = "".join(f"line {i}\n" for i in range(200))
    root = make_project({"big.txt": lines})
    result = scan(root, max_file_size=1024)
    entry = result.entries[0]
    assert entry.truncated
    assert entry.omitted_lines == 150
    assert entry.content.startswith("line 0")
    assert entry.content.count("\n") == 49  # 50 lines joined


def test_size_boundary_not_truncated(make_project):
    root = make_project({"exact.txt": "a" * 1024})
    result = scan(root, max_file_size=1024)
    assert not result.entries[0].truncated


# ------------------------------------------------------------------ symlinks


@pytest.mark.skipif(sys.platform == "win32", reason="creating symlinks needs admin on Windows")
def test_symlinked_dir_skipped_by_default(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    os.symlink(root, root / "src" / "loop", target_is_directory=True)
    result = scan(root)
    assert rel_paths(result) == {"src/a.py"}
    assert any("symlinked directory" in s.reason for s in result.skipped)


@pytest.mark.skipif(sys.platform == "win32", reason="creating symlinks needs admin on Windows")
def test_symlink_loop_is_safe_when_following(tmp_path):
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("x = 1\n", encoding="utf-8")
    os.symlink(root, root / "src" / "loop", target_is_directory=True)
    result = scan(root, follow_symlinks=True)  # must terminate
    assert "src/a.py" in rel_paths(result)
    assert any("loop" in s.reason for s in result.skipped)


# --------------------------------------------------------------- permissions


@pytest.mark.skipif(
    sys.platform == "win32" or (hasattr(os, "getuid") and os.getuid() == 0),
    reason="POSIX permission bits only (and not as root)",
)
def test_unreadable_file_skipped_with_warning(tmp_path):
    root = tmp_path / "p"
    root.mkdir()
    (root / "ok.txt").write_text("fine\n", encoding="utf-8")
    locked = root / "locked.txt"
    locked.write_text("secret\n", encoding="utf-8")
    locked.chmod(0o000)
    try:
        result = scan(root)
    finally:
        locked.chmod(0o644)
    assert rel_paths(result) == {"ok.txt"}
    assert any("unreadable" in s.reason for s in result.skipped)
