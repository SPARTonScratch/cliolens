<p align="center">
    <img src="https://raw.githubusercontent.com/SPARTonScratch/cliolens/main/assets/cliolens-logo.png" width="256" alt="ClioLens logo">
</p>

<h1 align="center">ClioLens</h1>

<p align="center"><b>One command. One file. Your whole codebase, ready for any AI.</b></p>

ClioLens packs a project directory into a single markdown *context dump* with
a visual directory tree, the full text of every relevant file, and metadata. It's designed to be attached to free web-based AI models
(Qwen, Kimi, ChatGPT, Claude…) for whole-codebase analysis.

No API keys, local GPU, IDE extensions, or telemetry. Read-only.

---

## Why

Free-tier web models accept file uploads but can't see your repo. Copy-pasting
files one by one is slow and loses structure. ClioLens gives you the
copy-paste workflow in one shot, with guardrails:

- **Zero-config**: `cliolens .` produces something useful immediately.
- **Respects your project**: build artifacts, dependencies, VCS data, and
  `.gitignore` rules are excluded automatically.
- **Transparent**: you always know what was included, what was skipped, and why.
- **Model-aware**: clean headers, syntax-fenced code, token budgets.

## Requirements

- Python 3.10+ (on Windows 11, [uv](https://astral.sh/uv) manages this for you)
- Any OS; developed and tested against Windows 11 (paths with spaces,
  Unicode, backslashes, and long paths all handled)

## Installation

**Prerequisites (one-time):**

```powershell
# uv — manages Python and packages for you
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"

# Git — needed to fetch from GitHub (skip if you already have it)
winget install Git.Git
```

**Install ClioLens** (open a **new terminal** after the steps above):

```powershell
uv tool install git+https://github.com/SPARTonScratch/cliolens
cliolens --version   # verify
```

To pin a specific release instead of tracking `main`:

```powershell
uv tool install git+https://github.com/SPARTonScratch/cliolens@v0.1.0
```

### From source (for contributors)

```powershell
git clone https://github.com/SPARTonScratch/cliolens
cd cliolens
uv tool install .        # or: uv pip install -e ".[dev]" for editable + test tooling
```

Upgrade / uninstall: `uv tool upgrade cliolens` · `uv tool uninstall cliolens`.
Plain `pip install .` also works from a local clone.

## Quickstart

```powershell
# Dump the current directory, writes 'cliolens project context.md' right here
cliolens .

# Want it on stdout instead (to pipe or copy)?
cliolens . -o -

# Preview exactly what will be included before generating
cliolens . --dry-run

# Check against a 128K-token model window
cliolens . -t 128000
```

## CLI reference

```
cliolens [OPTIONS] [DIRECTORY]
```

| Flag | Default | Description |
|---|---|---|
| `DIRECTORY` | `.` | Root directory to scan |
| `-o, --output PATH` | `cliolens project context.md` | Output file in the current directory (`-o -` for stdout; existing files overwritten) |
| `-m, --max-file-size SIZE` | `100KB` | Larger files are truncated with a notice (`B`/`KB`/`MB`/`GB`) |
| `-e, --exclude GLOB` | — | Extra exclude pattern, repeatable (`*.log`, `temp/`, `src/gen/*.py`) |
| `-n, --no-gitignore` | off | Ignore `.gitignore` files (built-in exclusions still apply) |
| `-f, --follow-symlinks` | off | Follow symlinks (loop-safe via inode tracking) |
| `-d, --dry-run` | off | Report include/skip decisions without generating output |
| `-t, --max-tokens N` | `0` | Warn on stderr if the estimate exceeds N |
| `--show-binary` | off | Dump binary file contents as base64-encoded text in File Contents; without this flag, a placeholder notice (type + size) is shown instead. Binaries always appear in the tree and counts regardless. |
| `--version` | — | Print version and exit |

## What gets excluded

**Always (built-in):** `.git`, `.svn`, `.hg`, `node_modules`, `vendor`,
`__pycache__`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `.venv`,
`venv`, `env`, `ENV`, `dist`, `build`, `target`, `.out`, `.idea`, `.vscode`,
`.DS_Store`, `Thumbs.db`, and files matching `*.pyc *.pyo *.class *.o *.so
*.dll *.exe *.bin *.lock`.

**Then:** every `.gitignore` in the tree (root and nested, including `!`
negations and `**` patterns, via the `pathspec` library).

**Then:** your `--exclude` globs — matched against the full relative path,
the basename (gitignore-style), or a path suffix. `dir/` patterns prune the
whole directory.

**Plus:** the output file itself, as a safety guard so re-runs never dump the
previous dump. On Windows all matching is case-insensitive.

**Binary files** (images, fonts, media, archives, ML artifacts…) are detected
by a built-in extension blacklist (fast path) with content analysis as the
fallback authority. They **always** appear in the directory tree, count toward
included files, and show a placeholder notice in the File Contents section
with their MIME type and size. Use `--show-binary` to replace the placeholder
with the file's full content as base64-encoded text (subject to
`--max-file-size` truncation, measured in bytes).

## Anatomy of the output

````markdown
# Project Context Dump

## Metadata
- **Project Name:** my-app
- **Root Path:** C:\Users\Dev\my-app
- **Generated At:** 2026-08-01 18:40:05 -02:00
- **Files Scanned:** 142
- **Files Included:** 87
- **Files Skipped:** 55
- **Estimated Tokens:** 45,210 (~180,840 characters)
- **Total Source Size:** 312.4KB

---

## Directory Tree

```
my-app/
├── src/
│   └── main.py
└── README.md
```

---

## File Contents

### src/main.py

```python
def main():
    print("hello")
```

End of dump. Generated by cliolens v0.1.0
````

- **Scanned** = every file encountered; **included** = in the dump
  (truncated files and binary files count as included); **skipped** = excluded
  or unreadable.
- Oversized files keep their first 50 lines plus a truncation banner.
- With `--show-binary`, binary placeholders are replaced with base64-encoded
  content in a code fence, truncated at `--max-file-size` if necessary.

## Token estimation

`ceil(characters / 4)` is used to estimate token count, so expect ±20% of a real tokenizer on typical source. Diagnostics and warnings print to **stderr** in color (TTY-only, honors `NO_COLOR`), so piping stdout is always clean markdown.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Input/runtime error (missing dir, permission denied, bad output path…) |
| `2` | Usage error (unknown flag, malformed `--max-file-size`) |
| `130` | Interrupted (Ctrl+C) |

## Development

```powershell
uv pip install -e ".[dev]"     # editable install + test tooling
pytest                         # unit + integration tests
pytest --cov=cliolens          # with coverage
ruff check src tests           # lint
```

```
src/cliolens/
├── cli.py        # argument parsing, orchestration, error presentation
├── scanner.py    # traversal, filter pipeline, binary detection, metadata
├── formatter.py  # tree rendering, markdown assembly, content wrapping
└── utils.py      # token estimation, size formatting, path helpers
```

## License

MIT License, see [LICENSE](LICENSE).