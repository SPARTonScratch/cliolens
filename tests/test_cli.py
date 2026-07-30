import pytest

from cliolens.cli import main


def test_version(capsys):
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert "0.1.0" in capsys.readouterr().out


def test_basic_dump_to_file(make_project, tmp_path, capsys):
    root = make_project({"hello.py": "print('hello')\n"})
    out = tmp_path / "ctx.md"
    assert main([str(root), "-o", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "# Project Context Dump" in text
    assert "### hello.py" in text
    assert "context dump complete" in capsys.readouterr().err


def test_stdout_output(make_project, capsys):
    root = make_project({"a.py": "x = 1\n"})
    assert main([str(root), "-o", "-"]) == 0
    captured = capsys.readouterr()
    assert "# Project Context Dump" in captured.out
    assert "### a.py" in captured.out


def test_missing_directory(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        main([str(tmp_path / "nope")])
    assert exc.value.code == 1
    assert "does not exist" in capsys.readouterr().err


def test_file_instead_of_directory(make_project, capsys):
    root = make_project({"a.txt": "x"})
    with pytest.raises(SystemExit) as exc:
        main([str(root / "a.txt")])
    assert exc.value.code == 1
    assert "is a file" in capsys.readouterr().err


def test_output_path_is_directory(make_project, capsys):
    root = make_project({"a.txt": "x"})
    with pytest.raises(SystemExit) as exc:
        main([str(root), "-o", str(root)])
    assert exc.value.code == 1
    assert "is a directory" in capsys.readouterr().err


def test_invalid_size_is_usage_error(make_project):
    root = make_project({"a.txt": "x"})
    with pytest.raises(SystemExit) as exc:
        main([str(root), "-m", "banana"])
    assert exc.value.code == 2  # argparse convention for bad flag values


def test_dry_run_writes_nothing(make_project, tmp_path, capsys):
    root = make_project({"a.py": "x = 1\n"})
    out = tmp_path / "should_not_exist.md"
    assert main([str(root), "--dry-run", "-o", str(out)]) == 0
    assert not out.exists()
    text = capsys.readouterr().out
    assert "would include" in text
    assert "a.py" in text


def test_max_tokens_warning(make_project, capsys):
    root = make_project({"a.py": "x = 1\n"})
    assert main([str(root), "-o", "-", "-t", "1"]) == 0
    assert "WARNING" in capsys.readouterr().err


def test_exclude_flag(make_project, capsys):
    root = make_project({"keep.py": "1", "drop.test.js": "2"})
    assert main([str(root), "-o", "-", "-e", "*.test.js"]) == 0
    out = capsys.readouterr().out
    assert "### keep.py" in out
    assert "drop.test.js" not in out


def test_overwrite_existing_output(make_project, tmp_path):
    root = make_project({"a.py": "x = 1\n"})
    out = tmp_path / "ctx.md"
    out.write_text("stale", encoding="utf-8")
    assert main([str(root), "-o", str(out)]) == 0
    assert "stale" not in out.read_text(encoding="utf-8")

def test_default_output_filename(make_project, tmp_path):
    root = make_project({"a.py": "x = 1\n"})
    assert main([str(root)]) == 0
    out = tmp_path / "cliolens project context.md"
    assert out.exists()
    assert "# Project Context Dump" in out.read_text(encoding="utf-8")
