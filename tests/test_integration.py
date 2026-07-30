"""End-to-end: synthetic project in, markdown document out."""

from cliolens.cli import main


def test_end_to_end(make_project, tmp_path):
    root = make_project(
        {
            ".gitignore": "*.log\nsecret/\n",
            "README.md": "# Sample\n",
            "src/app.py": "def run():\n    return 42\n",
            "src/web/index.html": "<html></html>\n",
            "tests/test_app.py": "assert True\n",
            "secret/keys.txt": "hunter2",
            "debug.log": "noise",
            "assets/blob.bin": b"\x00\x01\x02",
            "assets/logo.png": b"\x89PNG\x00\x00",
            "node_modules/junk.js": "var x;",
        }
    )
    out = tmp_path / "dump.md"
    assert main([str(root), "-o", str(out), "-m", "100KB"]) == 0

    doc = out.read_text(encoding="utf-8")

    # Excluded content never leaks.
    assert "hunter2" not in doc
    assert "noise" not in doc
    assert "junk.js" not in doc

    # Binary files appear in the tree but get no contents entry by default.
    assert "logo.png" in doc               # present in tree
    assert "[Binary file:" not in doc      # no notice without --show-binary

    # Included content is fenced with the right language.
    assert "### src/app.py" in doc
    assert "```python" in doc
    assert "```html" in doc

    # Sections appear in the strict spec order.
    assert doc.index("## Metadata") < doc.index("## Directory Tree") < doc.index("## File Contents")

    # The tree is fenced so it renders as-is in markdown viewers.
    tree_start = doc.index("## Directory Tree")
    assert "```" in doc[tree_start : doc.index("## File Contents")]


def test_rerun_does_not_dump_previous_dump(make_project, tmp_path):
    root = make_project({"a.py": "x = 1\n"})
    out = root / "context.md"
    assert main([str(root), "-o", str(out)]) == 0
    assert main([str(root), "-o", str(out)]) == 0
    doc = out.read_text(encoding="utf-8")
    assert "### context.md" not in doc
    assert "output file (protected)" not in doc  # skipped silently from contents
