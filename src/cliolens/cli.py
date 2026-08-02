"""Command-line interface for ClioLens."""

from __future__ import annotations

import argparse
import contextlib
import os
import sys
from pathlib import Path
from typing import NoReturn

from . import __version__
from .formatter import MarkdownFormatter
from .scanner import ProjectScanner
from .utils import estimate_tokens, format_count, format_size, parse_size, style

PROG = "cliolens"
DEFAULT_OUTPUT = "cliolens project context.md"
STDOUT_TOKEN = "-"

EXAMPLES = """\
examples:
  cliolens .                                  dump to 'cliolens project context.md' (default)
  cliolens . -o -                             print the dump to stdout instead
  cliolens . -o context.md                    dump to a specific file
  cliolens C:\\Dev\\app -o dump.md -e "*.test.js" -e "docs/"
  cliolens . --dry-run                        preview what would be included/skipped
  cliolens . -o out.md -m 50KB --no-gitignore strict mode: small files only
  cliolens . -o out.md -t 128000              warn if the dump exceeds 128K tokens
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROG,
        description=(
            "Pack a project directory into a single AI-ready context file: "
            "directory tree + full file contents + token estimates."
        ),
        epilog=EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        metavar="DIRECTORY",
        help="root directory to scan (default: current directory)",
    )
    parser.add_argument(
        "-o", "--output", metavar="PATH", default=DEFAULT_OUTPUT,
        help="output file path (default: 'cliolens project context.md' in the current "
             "directory; use '-o -' to print to stdout; existing files are overwritten)",
    )
    parser.add_argument(
        "-m", "--max-file-size", default="100KB", metavar="SIZE",
        help="max file size included in full; larger files are truncated with a notice "
             "(supports B/KB/MB/GB, default: 100KB)",
    )
    parser.add_argument(
        "-e", "--exclude", action="append", default=[], metavar="GLOB",
        help="additional glob pattern to exclude, e.g. '*.log' or 'temp/' (repeatable)",
    )
    parser.add_argument(
        "-n", "--no-gitignore", action="store_true",
        help="ignore the project's .gitignore files (built-in exclusions still apply)",
    )
    parser.add_argument(
        "-f", "--follow-symlinks", action="store_true",
        help="follow symbolic links during traversal (loop-safe)",
    )
    parser.add_argument(
        "-d", "--dry-run", action="store_true",
        help="report what would be included/skipped without generating output",
    )
    parser.add_argument(
        "-t", "--max-tokens", type=int, default=0, metavar="N",
        help="warn if the estimated token count exceeds N (0 = no limit)",
    )
    parser.add_argument(
        "--show-binary", action="store_true",
        help="dump binary file contents as base64-encoded text in the File "
             "Contents section; without this flag, a placeholder notice is shown",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}",
    )
    return parser


def _fail(message: str) -> NoReturn:
    print(f"{style('error:', 'red', 'bold')} {message}", file=sys.stderr)
    raise SystemExit(1)


def _warn(message: str) -> None:
    print(f"{style('warning:', 'yellow', 'bold')} {message}", file=sys.stderr)


def _write_stdout(document: str) -> None:
    try:
        with contextlib.suppress(AttributeError, OSError, ValueError):
            sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
        sys.stdout.write(document)
        sys.stdout.flush()
    except UnicodeEncodeError:
        # Console encoding can't take UTF-8
        sys.stdout.buffer.write(document.encode("utf-8", errors="replace"))
        sys.stdout.buffer.flush()
    except BrokenPipeError:
        # Downstream consumer (e.g. `| more`) closed the pipe -> exit quietly.
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, sys.stdout.fileno())
        raise SystemExit(1) from None


def _print_dry_run(result, *, show_binary: bool = False) -> None:
    print(
        f"{style('[dry run]', 'cyan', 'bold')} {style('cliolens v' + __version__, 'cyan')}"
        " — no output generated\n"
    )
    print(f"root: {result.root}\n")
    print(
        style(
            f"would include ({format_count(result.files_included)} files):",
            "green", "bold",
        )
    )
    for entry in result.entries:
        tags = []
        if entry.is_binary:
            tags.append("binary, base64 content" if show_binary else "binary, placeholder")
        if entry.truncated:
            tags.append("truncated")
        suffix = f"  [{', '.join(tags)}]" if tags else ""
        print(f"  {entry.relative_path}  ({format_size(entry.size_bytes)}){suffix}")
    print()
    print(style(f"would skip ({format_count(result.files_skipped)}):", "yellow", "bold"))
    for skip in result.skipped:
        print(f"  {skip.relative_path}  — {skip.reason}")
    print()
    print(
        f"summary: {format_count(result.files_scanned)} scanned · "
        f"{format_count(result.files_included)} included · "
        f"{format_count(result.files_skipped)} skipped"
    )


def _print_summary(result, args, output_path: Path | None, document: str) -> None:
    tokens = estimate_tokens(document)
    resolved = output_path.resolve() if output_path is not None else None
    print(
        "\n".join(
            [
                f"{style('cliolens v' + __version__, 'cyan', 'bold')}"
                f"{style(' — context dump complete', 'dim')}",
                f"  root      {result.root}",
                f"  scanned   {format_count(result.files_scanned)} files",
                f"  included  {format_count(result.files_included)} files",
                f"  skipped   {format_count(result.files_skipped)} files",
                f"  output    {resolved if resolved is not None else '<stdout>'}",
            ]
        ),
        file=sys.stderr,
    )

    if args.max_tokens > 0 and tokens > args.max_tokens:
        print(
            style(
                f"WARNING: Estimated tokens ({format_count(tokens)}) exceed --max-tokens "
                f"limit ({format_count(args.max_tokens)}).\n"
                "The model may truncate or reject this context. Consider:\n"
                "  - Reducing --max-file-size\n"
                "  - Adding --exclude patterns\n"
                "  - Splitting the project into multiple dumps",
                "yellow",
            ),
            file=sys.stderr,
        )


def _run(argv: list[str] | None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    # -- validate inputs ----------------------------------------------------
    root = Path(args.directory)
    if not root.exists():
        _fail(f"Directory '{root}' does not exist.")
    if root.is_file():
        _fail(f"'{root}' is a file, not a directory.")
    if not root.is_dir():
        _fail(f"'{root}' is not a directory.")
    try:
        with os.scandir(root):
            pass
    except PermissionError:
        _fail(f"Permission denied reading '{root}'.")
    except OSError as exc:
        _fail(f"Could not read '{root}': {exc}")

    try:
        max_size = parse_size(args.max_file_size)
    except ValueError as exc:
        parser.error(str(exc))  # exits with code 2 (usage error)

    if args.max_tokens < 0:
        parser.error("--max-tokens must be >= 0")

    use_stdout = args.output == STDOUT_TOKEN
    output_path = None if use_stdout else Path(args.output)
    if output_path is not None and output_path.is_dir():
        _fail(f"Output path '{output_path}' is a directory. Specify a file path.")

    # -- scan ----------------------------------------------------------------
    scanner = ProjectScanner(
        root,
        max_file_size=max_size,
        use_gitignore=not args.no_gitignore,
        user_excludes=args.exclude,
        follow_symlinks=args.follow_symlinks,
        show_binary=args.show_binary,
        protect_paths={output_path} if output_path is not None else None,
        warn=_warn,
    )
    result = scanner.scan()

    if args.dry_run:
        _print_dry_run(result, show_binary=args.show_binary)
        return 0

    # -- format & write -------------------------------------------------------
    document = MarkdownFormatter(
        max_file_size_label=args.max_file_size,
    ).format(result)

    if output_path is not None:
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(document, encoding="utf-8")  # overwrites silently
        except PermissionError:
            _fail(f"Permission denied writing '{output_path}'.")
        except OSError as exc:
            _fail(f"Could not write '{output_path}': {exc}")
    else:
        _write_stdout(document)

    _print_summary(result, args, output_path, document)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point (registered as the ``cliolens`` console script)."""
    try:
        return _run(argv)
    except KeyboardInterrupt:
        print(style("\ninterrupted — no output written.", "red"), file=sys.stderr)
        return 130
